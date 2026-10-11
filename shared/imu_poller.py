"""Polls a Pi `GET /imu/latest` and keeps a de-duplicated recent sample history.

Used by the laptop's `pi_client` and by the Dashscreen (polling the Pi's own
server). Each response is a ~1 s window, so consecutive polls overlap. Samples
already seen are dropped by timestamp; if a response starts *after* the newest
sample we already hold, samples were missed and `gaps` is incremented.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from collections import deque
from typing import Callable, Deque, List, Optional

from shared import config
from shared.schemas import ImuSample, age_seconds, parse_ts

Fetcher = Callable[[], List[dict]]  # returns the raw sample dicts of one window

HISTORY_S = 10.0
HISTORY_MAX = 2000


def http_fetcher(url: str) -> Fetcher:
    def fetch() -> List[dict]:
        with urllib.request.urlopen(url, timeout=config.HTTP_TIMEOUT_S) as resp:
            return json.load(resp)["samples"]

    return fetch


class ImuPoller:
    def __init__(self, fetch: Fetcher):
        self._fetch = fetch
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._history: Deque[ImuSample] = deque(maxlen=HISTORY_MAX)
        self._newest = None  # datetime of newest sample held
        self.polls = 0
        self.gaps = 0
        self.error: Optional[str] = None

    def poll_once(self) -> int:
        """Fetch one window and ingest it. Returns the number of new samples."""
        try:
            raw = self._fetch()
            samples = [ImuSample.from_dict(d) for d in raw]
        except Exception as exc:
            with self._lock:
                self.error = str(exc)
            return 0
        samples.sort(key=lambda s: s.ts)
        with self._lock:
            self.error = None
            self.polls += 1
            if not samples:
                return 0
            if self._newest is not None and parse_ts(samples[0].ts) > self._newest:
                self.gaps += 1  # the window began after what we already had
            fresh = [s for s in samples if self._newest is None or parse_ts(s.ts) > self._newest]
            self._history.extend(fresh)
            if fresh:
                self._newest = parse_ts(fresh[-1].ts)
            return len(fresh)

    def between(self, t0: float, t1: float) -> List[ImuSample]:
        """Held samples with t0 <= ts <= t1 (epoch seconds), oldest first."""
        with self._lock:
            hist = list(self._history)
        return [s for s in hist if t0 <= parse_ts(s.ts).timestamp() <= t1]

    def newest_epoch(self) -> Optional[float]:
        with self._lock:
            return self._newest.timestamp() if self._newest else None

    def snapshot(self, seconds: float = 5.0) -> dict:
        with self._lock:
            hist = list(self._history)
            polls, gaps, error = self.polls, self.gaps, self.error
        age = age_seconds(hist[-1].ts) if hist else None
        cutoff = parse_ts(hist[-1].ts).timestamp() - seconds if hist else 0
        recent = [s.to_dict() for s in hist if parse_ts(s.ts).timestamp() >= cutoff]
        return {
            "samples": recent,
            "latest": hist[-1].to_dict() if hist else None,
            "age_s": age,
            "stale": age is None or age > config.STALE_AFTER_S,
            "polls": polls,
            "gaps": gaps,
            "error": error,
        }

    def run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(config.IMU_POLL_S)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
