"""Pi GPS client: polls the laptop's GET /gps/current and tracks freshness.

Staleness is judged from the fix's source `ts` (CAP-5), not from whether the
last HTTP call succeeded, so a laptop that answers with an old fix is stale too.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from collections import deque
from typing import Deque, Optional

from shared import config
from shared.schemas import GpsFix, age_seconds


class GpsClient:
    def __init__(self, url: Optional[str] = None):
        self.url = url or f"http://{config.LAPTOP_HOST}:{config.LAPTOP_PORT}/gps/current"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._latest: Optional[GpsFix] = None
        self._trail: Deque[tuple] = deque(maxlen=config.TRAIL_MAX_POINTS)
        self._error: Optional[str] = None

    def poll_once(self) -> None:
        try:
            with urllib.request.urlopen(self.url, timeout=config.HTTP_TIMEOUT_S) as resp:
                fix = GpsFix.from_dict(json.load(resp))
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
            return
        with self._lock:
            self._error = None
            if self._latest is None or fix.ts != self._latest.ts:
                self._trail.append((fix.lat, fix.lon))
            self._latest = fix

    def snapshot(self) -> dict:
        """State for the Dashscreen. `stale` is true with no fix or a fix older than the threshold."""
        with self._lock:
            fix, trail, error = self._latest, list(self._trail), self._error
        age = age_seconds(fix.ts) if fix else None
        return {
            "fix": fix.to_dict() if fix else None,
            "trail": trail,
            "age_s": age,
            "stale": fix is None or age > config.STALE_AFTER_S,
            "error": error,
        }

    def run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(config.GPS_POLL_S)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
