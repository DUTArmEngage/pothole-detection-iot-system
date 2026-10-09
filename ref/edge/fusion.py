"""
fusion.py — Inertial corroboration and gating.

Implements the sensor fusion strategy described in Section 3.7 of the
dissertation, following the trigger principle established by Kandoi et al.
(2021): the inertial unit does not detect potholes, it confirms them.

Two distinct functions are provided and it matters that they are kept separate,
because they trade different things:

  GATING reduces computational load. Inference runs only when recent vertical
  acceleration suggests a physical event occurred. This saves CPU and power at
  the cost of missing defects the vehicle does not strike.

  CORROBORATION reduces false positives. Inference runs on every frame, but a
  visual detection is only confirmed when it coincides with an inertial event.
  This costs full CPU load but retains the option of reporting unconfirmed
  visual detections at lower severity.

The configuration selects one, both, or neither. Running with fusion disabled
entirely produces the vision-only baseline required by the experimental design
in Section 3.4.1.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Optional, Tuple


class FusionMode(str, Enum):
    DISABLED = "disabled"            # vision only — Configuration C baseline
    CORROBORATE = "corroborate"      # infer always, confirm with IMU
    GATE = "gate"                    # infer only on IMU trigger
    GATE_AND_CORROBORATE = "both"    # infer on trigger, confirm within window


@dataclass
class IMUSample:
    """One inertial reading. Acceleration in g, timestamp in seconds."""
    t: float
    ax: float
    ay: float
    az: float

    @property
    def vertical_deviation(self) -> float:
        """Vertical acceleration with 1 g gravity removed."""
        return abs(self.az - 1.0)

    @property
    def magnitude(self) -> float:
        return math.sqrt(self.ax ** 2 + self.ay ** 2 + self.az ** 2)


@dataclass
class FusionDecision:
    triggered: bool
    corroborated: bool
    peak_g: Optional[float]
    rms_g: float
    reason: str


class IMUFusion:
    """
    Rolling-window inertial analysis.

    Parameters
    ----------
    trigger_threshold_g : float
        Vertical deviation above which an impact is declared. Calibrated
        empirically; see Section 3.8.1. The default is a starting point only.
    window_s : float
        Length of the rolling analysis window.
    corroboration_window_s : float
        Time either side of a visual detection within which an inertial event
        counts as corroborating it.
    baseline_alpha : float
        Smoothing factor for the adaptive baseline. Set to 0 to disable
        adaptation and use a fixed threshold.
    """

    def __init__(
        self,
        mode: FusionMode = FusionMode.GATE_AND_CORROBORATE,
        trigger_threshold_g: float = 0.45,
        window_s: float = 1.0,
        corroboration_window_s: float = 0.6,
        baseline_alpha: float = 0.02,
        min_threshold_g: float = 0.20,
    ) -> None:
        self.mode = mode
        self.threshold = trigger_threshold_g
        self.window_s = window_s
        self.corr_window = corroboration_window_s
        self.alpha = baseline_alpha
        self.min_threshold = min_threshold_g

        self._buf: Deque[IMUSample] = deque()
        self._baseline = 0.0
        self._events: Deque[Tuple[float, float]] = deque(maxlen=256)  # (t, peak_g)

    # ------------------------------------------------------------------
    def add_sample(self, s: IMUSample) -> None:
        """Add one inertial reading and update the rolling window."""
        self._buf.append(s)
        cutoff = s.t - self.window_s
        while self._buf and self._buf[0].t < cutoff:
            self._buf.popleft()

        dev = s.vertical_deviation
        if self.alpha > 0:
            self._baseline = (1 - self.alpha) * self._baseline + self.alpha * dev

        if dev >= self._effective_threshold():
            if not self._events or (s.t - self._events[-1][0]) > 0.25:
                self._events.append((s.t, dev))
            elif dev > self._events[-1][1]:
                self._events[-1] = (s.t, dev)

    def _effective_threshold(self) -> float:
        """Threshold adjusted for measured surface roughness."""
        if self.alpha <= 0:
            return self.threshold
        return max(self.min_threshold, self.threshold + self._baseline)

    # ------------------------------------------------------------------
    def should_infer(self, now: float) -> bool:
        """Whether the vision pipeline should run at this instant."""
        if self.mode in (FusionMode.DISABLED, FusionMode.CORROBORATE):
            return True
        return any(abs(now - t) <= self.corr_window for t, _ in self._events)

    def evaluate(self, now: float) -> FusionDecision:
        """Assess the inertial state around a visual detection."""
        rms = self._window_rms()

        if self.mode is FusionMode.DISABLED:
            return FusionDecision(True, True, None, rms, "fusion disabled")

        peak: Optional[float] = None
        for t, g in reversed(self._events):
            if abs(now - t) <= self.corr_window:
                peak = g if peak is None else max(peak, g)

        if peak is not None:
            return FusionDecision(True, True, peak, rms,
                                  f"impact {peak:.3f} g within {self.corr_window}s")

        if self.mode is FusionMode.GATE:
            # Gating already passed for inference to have run
            return FusionDecision(True, True, None, rms, "gate passed, no peak retained")

        return FusionDecision(False, False, None, rms, "no inertial corroboration")

    def _window_rms(self) -> float:
        if not self._buf:
            return 0.0
        vals = [s.vertical_deviation for s in self._buf]
        return math.sqrt(sum(v * v for v in vals) / len(vals))

    # ------------------------------------------------------------------
    @property
    def baseline_g(self) -> float:
        return self._baseline

    @property
    def effective_threshold_g(self) -> float:
        return self._effective_threshold()

    def reset(self) -> None:
        self._buf.clear()
        self._events.clear()
        self._baseline = 0.0
