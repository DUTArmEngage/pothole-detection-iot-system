"""
buffer.py — Local event store with acknowledgement-gated release.

Implements the store-and-forward mechanism described in Section 3.10.1. The
design rule is that an event is retained until the broker confirms receipt.
Nothing is deleted on send; the acknowledgement flag is cleared only on PUBACK.

This is what makes the framework tolerate the connectivity interruptions that
Ntiokam et al. (2024) identify as a primary barrier to IoT deployment in
African cities, and it is the mechanism the offline recovery test in
Section 4.5 exercises.
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional


# ----------------------------------------------------------------------
@dataclass
class RoadEvent:
    """One confirmed, geolocated road-condition event."""
    event_id: str
    device_id: str
    detected_at: str          # ISO 8601, UTC
    latitude: float
    longitude: float
    confidence: float
    severity: str
    severity_score: float
    defect_class: str
    box: List[float]                       # [x1, y1, x2, y2]
    peak_impact_g: Optional[float] = None
    speed_kph: Optional[float] = None
    heading_deg: Optional[float] = None
    frame_ref: Optional[str] = None
    schema: str = "roadcond/1.0"

    @staticmethod
    def new(**kw) -> "RoadEvent":
        kw.setdefault("event_id", str(uuid.uuid4()))
        kw.setdefault("detected_at",
                      datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
        return RoadEvent(**kw)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @property
    def payload_bytes(self) -> int:
        return len(self.to_json().encode("utf-8"))


# ----------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id       TEXT PRIMARY KEY,
    device_id      TEXT NOT NULL,
    detected_at    TEXT NOT NULL,
    latitude       REAL NOT NULL,
    longitude      REAL NOT NULL,
    confidence     REAL NOT NULL,
    severity       TEXT NOT NULL,
    severity_score REAL NOT NULL,
    defect_class   TEXT NOT NULL,
    payload        TEXT NOT NULL,
    acknowledged   INTEGER NOT NULL DEFAULT 0,
    queued_at      TEXT NOT NULL,
    acked_at       TEXT,
    send_attempts  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_unacked  ON events(acknowledged, queued_at);
CREATE INDEX IF NOT EXISTS idx_severity ON events(severity, detected_at);

CREATE TABLE IF NOT EXISTS telemetry (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at  TEXT NOT NULL,
    cpu_percent  REAL,
    cpu_temp_c   REAL,
    mem_percent  REAL,
    gnss_fix     TEXT,
    link_state   TEXT
);
"""


class EventBuffer:
    """Durable local queue for road-condition events."""

    def __init__(self, db_path: str = "data/events.db") -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    def enqueue(self, ev: RoadEvent) -> None:
        """Persist an event. Called before any transmission is attempted."""
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self._conn() as c:
            c.execute(
                """INSERT OR IGNORE INTO events
                   (event_id, device_id, detected_at, latitude, longitude,
                    confidence, severity, severity_score, defect_class,
                    payload, acknowledged, queued_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,0,?)""",
                (ev.event_id, ev.device_id, ev.detected_at, ev.latitude,
                 ev.longitude, ev.confidence, ev.severity, ev.severity_score,
                 ev.defect_class, ev.to_json(), now),
            )

    def pending(self, limit: int = 100) -> List[sqlite3.Row]:
        """Events awaiting broker acknowledgement, oldest first."""
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM events WHERE acknowledged=0 "
                "ORDER BY queued_at ASC LIMIT ?", (limit,)
            ).fetchall()

    def mark_sent(self, event_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE events SET send_attempts=send_attempts+1 "
                      "WHERE event_id=?", (event_id,))

    def acknowledge(self, event_id: str) -> None:
        """Clear the pending flag. Called only on broker confirmation."""
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self._conn() as c:
            c.execute("UPDATE events SET acknowledged=1, acked_at=? "
                      "WHERE event_id=?", (now, event_id))

    # ------------------------------------------------------------------
    def stats(self) -> dict:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) n FROM events").fetchone()["n"]
            unacked = c.execute(
                "SELECT COUNT(*) n FROM events WHERE acknowledged=0").fetchone()["n"]
            by_sev = {
                r["severity"]: r["n"] for r in c.execute(
                    "SELECT severity, COUNT(*) n FROM events GROUP BY severity")
            }
            payload = c.execute(
                "SELECT COALESCE(SUM(LENGTH(payload)),0) b FROM events").fetchone()["b"]
        return {
            "total_events": total,
            "pending": unacked,
            "acknowledged": total - unacked,
            "by_severity": by_sev,
            "total_payload_bytes": payload,
            "delivery_rate": round((total - unacked) / total, 4) if total else None,
        }

    def all_events(self) -> List[dict]:
        with self._conn() as c:
            return [json.loads(r["payload"])
                    for r in c.execute("SELECT payload FROM events "
                                       "ORDER BY detected_at ASC")]

    def record_telemetry(self, **kw) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self._conn() as c:
            c.execute(
                """INSERT INTO telemetry
                   (recorded_at, cpu_percent, cpu_temp_c, mem_percent,
                    gnss_fix, link_state) VALUES (?,?,?,?,?,?)""",
                (now, kw.get("cpu_percent"), kw.get("cpu_temp_c"),
                 kw.get("mem_percent"), kw.get("gnss_fix"), kw.get("link_state")),
            )

    def reset(self) -> None:
        """Clear all stored data. Used between experimental runs."""
        with self._conn() as c:
            c.execute("DELETE FROM events")
            c.execute("DELETE FROM telemetry")


# ----------------------------------------------------------------------
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two coordinates."""
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def cluster_events(events: List[dict], radius_m: float = 15.0) -> List[dict]:
    """
    Aggregate events representing the same physical defect.

    Exploits the averaging property described in Section 2.6: positional error
    is substantially random rather than systematic, so repeated observations of
    one defect average toward its true location.
    """
    clusters: List[dict] = []
    for ev in events:
        placed = False
        for cl in clusters:
            if haversine_m(ev["latitude"], ev["longitude"],
                           cl["latitude"], cl["longitude"]) <= radius_m:
                n = cl["observation_count"]
                cl["latitude"] = (cl["latitude"] * n + ev["latitude"]) / (n + 1)
                cl["longitude"] = (cl["longitude"] * n + ev["longitude"]) / (n + 1)
                cl["observation_count"] = n + 1
                cl["max_confidence"] = max(cl["max_confidence"], ev["confidence"])
                order = {"low": 0, "medium": 1, "high": 2}
                if order[ev["severity"]] > order[cl["severity"]]:
                    cl["severity"] = ev["severity"]
                cl["event_ids"].append(ev["event_id"])
                placed = True
                break
        if not placed:
            clusters.append({
                "latitude": ev["latitude"],
                "longitude": ev["longitude"],
                "severity": ev["severity"],
                "max_confidence": ev["confidence"],
                "observation_count": 1,
                "event_ids": [ev["event_id"]],
            })
    return clusters
