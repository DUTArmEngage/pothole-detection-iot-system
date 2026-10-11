"""Replay a route as a stream of location fixes (no hardware needed)."""

from __future__ import annotations

import math
import time
from typing import List, Optional, Tuple

from shared.schemas import utc_now_iso

# A short loop around Berea, Durban (lat, lon).
DEFAULT_ROUTE: List[Tuple[float, float]] = [
    (-29.8533, 31.0017),
    (-29.8533, 31.0072),
    (-29.8571, 31.0072),
    (-29.8571, 31.0017),
    (-29.8533, 31.0017),
]


def haversine_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(h))


class FakeRoute:
    """Walks the route at constant speed, looping forever. Call `read()` for a fix."""

    def __init__(self, route: Optional[List[Tuple[float, float]]] = None, speed_kmh: float = 30.0):
        self.route = route or DEFAULT_ROUTE
        self.speed_ms = speed_kmh / 3.6
        self._legs = [haversine_m(a, b) for a, b in zip(self.route, self.route[1:])]
        self._total = sum(self._legs)
        self._t0 = time.monotonic()

    def position_at(self, travelled_m: float) -> Tuple[float, float]:
        d = travelled_m % self._total
        for (a, b), leg in zip(zip(self.route, self.route[1:]), self._legs):
            if d <= leg:
                f = d / leg if leg else 0.0
                return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
            d -= leg
        return self.route[-1]

    def read(self) -> dict:
        lat, lon = self.position_at((time.monotonic() - self._t0) * self.speed_ms)
        return {"lat": lat, "lon": lon, "ts": utc_now_iso(), "accuracy_m": 5.0}
