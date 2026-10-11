"""Pi IMU reader: samples the MPU-6050 at ~100 Hz into a ~1 s rolling window.

The window (not just the latest sample) is what `/imu/latest` serves, so the
laptop's 100 ms polls overlap and cannot miss an impact peak.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Deque, List

from shared import config
from shared.schemas import ImuSample, parse_ts, to_iso

SAMPLE_HZ = 100
SampleSource = Callable[[], ImuSample]


class Mpu6050Source:
    """Real sensor, via the reference driver in ref/imu.py (I2C bus 3 by default)."""

    def __init__(self, bus: int = 3, addr: int = 0x68):
        from ref.imu import MPU6050  # Linux-only (fcntl); imported lazily

        self._imu = MPU6050(bus=bus, addr=addr, sample_rate_hz=SAMPLE_HZ).open()

    def __call__(self) -> ImuSample:
        r = self._imu.read()
        ts = to_iso(datetime.fromtimestamp(r.t, timezone.utc))
        return ImuSample(ts=ts, ax=r.ax, ay=r.ay, az=r.az, gx=r.gx, gy=r.gy, gz=r.gz)

    def close(self) -> None:
        self._imu.close()


class ImuReader:
    def __init__(self, source: SampleSource, hz: int = SAMPLE_HZ):
        self._source = source
        self._period = 1.0 / hz
        self._buf: Deque[ImuSample] = deque(maxlen=int(hz * config.IMU_WINDOW_S * 2))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.errors = 0

    def sample_once(self) -> ImuSample:
        s = self._source()
        with self._lock:
            self._buf.append(s)
        return s

    def window(self) -> List[ImuSample]:
        """Samples within IMU_WINDOW_S of the newest one, oldest first."""
        with self._lock:
            buf = list(self._buf)
        if not buf:
            return []
        cutoff = parse_ts(buf[-1].ts).timestamp() - config.IMU_WINDOW_S
        return [s for s in buf if parse_ts(s.ts).timestamp() >= cutoff]

    def run(self) -> None:
        next_t = time.perf_counter()
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception as exc:  # a dropped I2C read must not kill the reader
                self.errors += 1
                if self.errors in (1, 10, 100):
                    print(f"[imu] read error #{self.errors}: {exc}")
            next_t += self._period
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.perf_counter()

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
