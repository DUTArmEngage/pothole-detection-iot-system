"""Payload definitions imported by both devices.

Every timestamp is ISO 8601 UTC, stamped at its source. Pi and laptop clocks
are NTP-synced, so a `ts` from one device is comparable with the other's clock.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_now_iso() -> str:
    return to_iso(datetime.now(timezone.utc))


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def age_seconds(ts: str, now: Optional[datetime] = None) -> float:
    now = now or datetime.now(timezone.utc)
    return (now - parse_ts(ts)).total_seconds()


def _opt_float(d: dict, key: str) -> Optional[float]:
    return None if d.get(key) is None else float(d[key])


@dataclass
class GpsFix:
    lat: float
    lon: float
    ts: str
    speed_kmh: Optional[float] = None
    heading: Optional[float] = None
    accuracy_m: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GpsFix":
        fix = cls(
            lat=float(d["lat"]),
            lon=float(d["lon"]),
            ts=str(d["ts"]),
            speed_kmh=_opt_float(d, "speed_kmh"),
            heading=_opt_float(d, "heading"),
            accuracy_m=_opt_float(d, "accuracy_m"),
        )
        parse_ts(fix.ts)  # reject malformed timestamps early
        return fix


@dataclass
class ImuSample:
    ts: str
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ImuSample":
        sample = cls(ts=str(d["ts"]), **{k: float(d[k]) for k in ("ax", "ay", "az", "gx", "gy", "gz")})
        parse_ts(sample.ts)
        return sample
