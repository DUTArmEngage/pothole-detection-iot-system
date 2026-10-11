"""Laptop client for the Pi. Epic 2 covers IMU; video joins in epic 3.

`PiClient(fake=True)` replays simulated IMU samples so the laptop side runs
with the Pi powered off (CAP-8).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from shared import config
from shared.imu_poller import ImuPoller, http_fetcher
from shared.schemas import to_iso


def fake_window_fetcher(hz: int = 100):
    from tests.fake_imu import FakeImu

    imu = FakeImu()
    import time

    t0 = time.monotonic()

    def fetch() -> List[dict]:
        now = datetime.now(timezone.utc)
        t = time.monotonic() - t0
        n = int(config.IMU_WINDOW_S * hz)
        out = []
        for i in range(n, -1, -1):  # oldest first, last sample is "now"
            back = i / hz
            out.append(imu.sample_at(t - back, now - timedelta(seconds=back)).to_dict())
        return out

    return fetch


class PiClient:
    def __init__(self, base_url: Optional[str] = None, fake: bool = False, enabled: bool = True):
        base = (base_url or f"http://{config.PI_HOST}:{config.PI_PORT}").rstrip("/")
        self.fake = fake
        self.enabled = enabled  # False: no IMU in this setup (lab demo), nothing is polled
        self.imu = ImuPoller(fake_window_fetcher() if fake else http_fetcher(f"{base}/imu/latest"))

    def start(self) -> None:
        if self.enabled:
            self.imu.start()

    def stop(self) -> None:
        self.imu.stop()
