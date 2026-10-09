"""
severity.py — Severity classification for confirmed detections.

Severity is derived from three observable quantities rather than from model
confidence alone, because confidence measures how certain the model is that a
pothole is present, not how bad the pothole is. Those are different questions
and a maintenance authority needs the second one.

The three inputs are:

  1. Apparent size — bounding box area as a fraction of the frame. Larger
     defects occupy more of the field of view at a given distance.
  2. Detection confidence — a clear, well-defined defect boundary produces
     higher confidence than a marginal or partially occluded one.
  3. Impact magnitude — peak vertical acceleration recorded by the IMU within
     the corroboration window, where available. This is the only one of the
     three that measures physical severity directly.

Weighting is configurable. The defaults favour impact magnitude when inertial
data is present, and fall back to a size-and-confidence blend when it is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class SeverityWeights:
    """Relative contribution of each input to the composite severity score."""
    size: float = 0.30
    confidence: float = 0.25
    impact: float = 0.45          # used only when IMU data is present
    size_no_imu: float = 0.55     # redistribution when IMU is absent
    conf_no_imu: float = 0.45


@dataclass
class SeverityThresholds:
    """Score boundaries between severity classes."""
    medium: float = 0.40
    high: float = 0.70


@dataclass
class SeverityResult:
    severity: Severity
    score: float
    size_component: float
    confidence_component: float
    impact_component: Optional[float]
    used_imu: bool


class SeverityClassifier:
    """
    Composite severity classifier.

    Parameters
    ----------
    frame_area : int
        Total pixel area of the input frame, used to normalise box area.
    max_impact_g : float
        Acceleration magnitude treated as the ceiling of the impact scale.
        Values at or above this saturate the impact component at 1.0.
    """

    def __init__(
        self,
        frame_area: int,
        max_impact_g: float = 4.0,
        weights: Optional[SeverityWeights] = None,
        thresholds: Optional[SeverityThresholds] = None,
        size_saturation: float = 0.12,
    ) -> None:
        self.frame_area = max(frame_area, 1)
        self.max_impact_g = max_impact_g
        self.w = weights or SeverityWeights()
        self.t = thresholds or SeverityThresholds()
        self.size_saturation = size_saturation

    def classify(
        self,
        box_area: float,
        confidence: float,
        peak_impact_g: Optional[float] = None,
    ) -> SeverityResult:
        """Return a severity classification for one detection."""
        # Size component: fraction of frame, saturating at size_saturation
        size_frac = box_area / self.frame_area
        size_c = min(size_frac / self.size_saturation, 1.0)

        # Confidence component: rescaled so that the operating band 0.35–1.0
        # spans the full 0–1 range, since detections below 0.35 never reach here
        conf_c = max(0.0, min((confidence - 0.35) / 0.65, 1.0))

        if peak_impact_g is not None:
            impact_c = min(peak_impact_g / self.max_impact_g, 1.0)
            score = (
                self.w.size * size_c
                + self.w.confidence * conf_c
                + self.w.impact * impact_c
            )
            used_imu = True
        else:
            impact_c = None
            score = self.w.size_no_imu * size_c + self.w.conf_no_imu * conf_c
            used_imu = False

        if score >= self.t.high:
            sev = Severity.HIGH
        elif score >= self.t.medium:
            sev = Severity.MEDIUM
        else:
            sev = Severity.LOW

        return SeverityResult(
            severity=sev,
            score=round(score, 4),
            size_component=round(size_c, 4),
            confidence_component=round(conf_c, 4),
            impact_component=round(impact_c, 4) if impact_c is not None else None,
            used_imu=used_imu,
        )
