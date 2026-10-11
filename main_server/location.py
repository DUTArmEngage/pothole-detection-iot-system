"""Laptop OS location source (Windows Location Service via GeoCoordinateWatcher).

A long-lived PowerShell child polls the watcher once a second and prints one
JSON line per reading; `read()` returns the newest one. The first fix can take
~20 s after start. Windows fixes are often coarse (tens of metres), which the
spec accepts, so `accuracy_m` is carried through but not acted on.
"""

from __future__ import annotations

import json
import subprocess
import threading
from typing import Optional

from shared.schemas import utc_now_iso

_SCRIPT = r"""
Add-Type -AssemblyName System.Device
$w = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::Default)
[void]$w.TryStart($false, [TimeSpan]::FromSeconds(5))
while ($true) {
  $l = $w.Position.Location
  if (-not $l.IsUnknown) {
    $o = @{ lat = $l.Latitude; lon = $l.Longitude; accuracy_m = $l.HorizontalAccuracy }
    [Console]::Out.WriteLine(($o | ConvertTo-Json -Compress)); [Console]::Out.Flush()
  }
  Start-Sleep -Seconds 1
}
"""


class WindowsLocationSource:
    def __init__(self) -> None:
        self._proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._lock = threading.Lock()
        self._latest: Optional[dict] = None
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self._proc.stdout:
            try:
                reading = json.loads(line)
            except ValueError:
                continue
            with self._lock:
                self._latest = reading

    def read(self) -> dict:
        with self._lock:
            reading = self._latest
        if reading is None:
            raise RuntimeError("waiting for first location fix (can take ~20 s)")
        # Stamped here, at the laptop, when the fix is served: the Pi's stale
        # check then reflects whether the laptop is still answering.
        return {**reading, "ts": utc_now_iso()}

    def close(self) -> None:
        self._proc.terminate()
