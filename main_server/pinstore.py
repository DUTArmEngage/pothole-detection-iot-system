"""Persistent store of pothole pins: a SQLite database plus one snapshot JPEG per pin.

A pin is one pothole encounter the camera saw with high confidence, placed at the laptop's
position at the time. Pins survive restarts, so a later run (or another tool) can use them.
`GET /api/pins.geojson` on the Dashboard exports them for GIS tools.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import List, Optional

from shared.schemas import to_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS pins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,      -- frame source time, ISO 8601 UTC
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    accuracy_m  REAL,               -- location accuracy reported by the OS, if any
    severity    TEXT NOT NULL,
    confidence  REAL NOT NULL,
    session     TEXT NOT NULL,      -- start time of the run that saved it
    image       TEXT                -- snapshot file name inside the pins directory
);
CREATE INDEX IF NOT EXISTS pins_ts ON pins (ts);
"""


class PinStore:
    def __init__(self, db_path: str, images_dir: Optional[str] = None):
        self.db_path = db_path
        self.images_dir = images_dir or os.path.join(os.path.dirname(os.path.abspath(db_path)), "pins")
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        os.makedirs(self.images_dir, exist_ok=True)
        self.session = to_iso(datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def add(self, ts: str, lat: float, lon: float, severity: str, confidence: float,
            accuracy_m: Optional[float] = None, jpeg: Optional[bytes] = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO pins (ts, lat, lon, accuracy_m, severity, confidence, session) VALUES (?,?,?,?,?,?,?)",
                (ts, lat, lon, accuracy_m, severity, confidence, self.session),
            )
            pin_id = cur.lastrowid
            if jpeg:
                name = f"{pin_id}.jpg"
                with open(os.path.join(self.images_dir, name), "wb") as f:
                    f.write(jpeg)
                self._db.execute("UPDATE pins SET image=? WHERE id=?", (name, pin_id))
            self._db.commit()
            return pin_id

    def update(self, pin_id: int, severity: str, confidence: float, jpeg: Optional[bytes] = None) -> None:
        """The same encounter produced a worse severity or a more confident frame: keep the best."""
        with self._lock:
            self._db.execute("UPDATE pins SET severity=?, confidence=? WHERE id=?", (severity, confidence, pin_id))
            if jpeg:
                with open(os.path.join(self.images_dir, f"{pin_id}.jpg"), "wb") as f:
                    f.write(jpeg)
                self._db.execute("UPDATE pins SET image=? WHERE id=?", (f"{pin_id}.jpg", pin_id))
            self._db.commit()

    def all(self) -> List[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM pins ORDER BY id").fetchall()
        return [dict(r) | {"current": r["session"] == self.session} for r in rows]

    def geojson(self) -> dict:
        feats = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},  # GeoJSON is lon, lat
                "properties": {k: p[k] for k in ("id", "ts", "severity", "confidence", "accuracy_m", "session")},
            }
            for p in self.all()
        ]
        return {"type": "FeatureCollection", "features": feats}

    def image_path(self, pin_id: int) -> Optional[str]:
        path = os.path.join(self.images_dir, f"{int(pin_id)}.jpg")
        return path if os.path.isfile(path) else None

    def close(self) -> None:
        with self._lock:
            self._db.close()
