"""Simulated MPU-6050 samples (no hardware needed).

Resting az reads ~1 g with small noise; every `impact_every_s` a short
half-sine impact lands on az, like a wheel hitting a pothole.
"""

from __future__ import annotations

import math
import random
import time
from datetime import datetime, timezone
from typing import Optional

from shared.schemas import ImuSample, to_iso


class FakeImu:
    def __init__(self, impact_every_s: float = 5.0, impact_g: float = 1.5, impact_len_s: float = 0.2, seed: Optional[int] = 1):
        self.impact_every_s = impact_every_s
        self.impact_g = impact_g
        self.impact_len_s = impact_len_s
        self._rng = random.Random(seed)
        self._t0 = time.monotonic()

    def sample_at(self, t: float, wall: Optional[datetime] = None) -> ImuSample:
        """Sample for `t` seconds since start; `wall` is the timestamp to stamp it with."""
        phase = t % self.impact_every_s
        bump = 0.0
        if phase < self.impact_len_s:
            bump = self.impact_g * math.sin(math.pi * phase / self.impact_len_s)
        n = self._rng.gauss
        return ImuSample(
            ts=to_iso(wall or datetime.now(timezone.utc)),
            ax=n(0, 0.01),
            ay=n(0, 0.01),
            az=1.0 + bump + n(0, 0.01),
            gx=n(0, 0.3) + bump * 5,
            gy=n(0, 0.3),
            gz=n(0, 0.3),
        )

    def read(self) -> ImuSample:
        return self.sample_at(time.monotonic() - self._t0)
