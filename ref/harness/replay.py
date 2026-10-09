"""
replay.py — Hardware-in-the-loop replay harness.

Substitutes for the instrumented vehicle. The system under test — model,
fusion logic, buffer, MQTT client — is real and runs on the Raspberry Pi. What
this module replaces is the environment: a video file stands in for the camera,
a GPX track for the GNSS receiver, and a generated or recorded trace for the
inertial unit.

The three streams are played back in synchronised virtual time, so that a frame
at t = 12.4 s is accompanied by the GNSS fix and inertial samples that apply at
t = 12.4 s. Without that alignment, fusion cannot be evaluated meaningfully.

This arrangement is what allows Configurations A, B and C of the experimental
design in Section 3.4.1 to be compared on identical input: the same replay is
fed to each, so any difference in output is attributable to configuration
rather than to variation between runs.
"""

from __future__ import annotations

import json
import math
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import numpy as np


# ----------------------------------------------------------------------
@dataclass
class GNSSFix:
    t: float
    latitude: float
    longitude: float
    speed_kph: float
    heading_deg: float
    fix_type: str = "3D"


@dataclass
class ReplayFrame:
    """One synchronised instant of the replayed environment."""
    index: int
    t: float
    image: Optional[np.ndarray]
    gnss: GNSSFix
    imu: List[Tuple[float, float, float, float]]   # (t, ax, ay, az)


# ----------------------------------------------------------------------
class GPXTrack:
    """A GNSS track, loaded from GPX or generated synthetically."""

    def __init__(self, fixes: List[GNSSFix]) -> None:
        if not fixes:
            raise ValueError("track contains no fixes")
        self.fixes = sorted(fixes, key=lambda f: f.t)
        self.duration = self.fixes[-1].t

    # -- construction ---------------------------------------------------
    @classmethod
    def from_gpx(cls, path: str) -> "GPXTrack":
        ns = {"g": "http://www.topografix.com/GPX/1/1"}
        root = ET.parse(path).getroot()
        pts = root.findall(".//g:trkpt", ns) or root.findall(".//trkpt")
        fixes: List[GNSSFix] = []
        for i, pt in enumerate(pts):
            lat, lon = float(pt.get("lat")), float(pt.get("lon"))
            fixes.append(GNSSFix(t=float(i), latitude=lat, longitude=lon,
                                 speed_kph=0.0, heading_deg=0.0))
        cls._derive_motion(fixes)
        return cls(fixes)

    @classmethod
    def synthetic_route(
        cls,
        waypoints: List[Tuple[float, float]],
        duration_s: float,
        rate_hz: float = 1.0,
        jitter_m: float = 2.5,
        seed: int = 42,
        target_speed_kph: float = 45.0,
    ) -> "GPXTrack":
        """
        Interpolate a route through waypoints with realistic positional noise.

        The vehicle travels at target_speed_kph, covering only as much of the
        waypoint polyline as that speed allows in duration_s. If the route is
        shorter than the distance implied, it wraps. This keeps derived speed
        physically plausible, which matters because speed feeds the severity
        model and appears in the event payload.

        jitter_m applies Gaussian positional error of that standard deviation,
        representing a consumer-grade receiver under open sky.
        """
        rng = random.Random(seed)
        n = max(int(duration_s * rate_hz), 2)

        # Cumulative distance along the polyline
        seg_m: List[float] = []
        for i in range(len(waypoints) - 1):
            (la1, lo1), (la2, lo2) = waypoints[i], waypoints[i + 1]
            dx = (lo2 - lo1) * 111_320.0 * math.cos(math.radians(la1))
            dy = (la2 - la1) * 111_320.0
            seg_m.append(math.hypot(dx, dy))
        total_m = sum(seg_m) or 1.0

        travel_m = (target_speed_kph / 3.6) * duration_s
        fixes: List[GNSSFix] = []

        for i in range(n):
            dist = (i / (n - 1)) * travel_m
            pos = (dist % total_m) if travel_m > total_m else dist

            acc, k, f = 0.0, 0, 0.0
            for j, s in enumerate(seg_m):
                if acc + s >= pos or j == len(seg_m) - 1:
                    k = j
                    f = (pos - acc) / s if s > 0 else 0.0
                    f = min(max(f, 0.0), 1.0)
                    break
                acc += s

            lat = waypoints[k][0] + f * (waypoints[k + 1][0] - waypoints[k][0])
            lon = waypoints[k][1] + f * (waypoints[k + 1][1] - waypoints[k][1])

            dlat = rng.gauss(0, jitter_m) / 111_320.0
            dlon = rng.gauss(0, jitter_m) / (111_320.0 * math.cos(math.radians(lat)))

            fixes.append(GNSSFix(t=i / rate_hz,
                                 latitude=lat + dlat, longitude=lon + dlon,
                                 speed_kph=0.0, heading_deg=0.0))
        cls._derive_motion(fixes)
        return cls(fixes)

    @staticmethod
    def _derive_motion(fixes: List[GNSSFix]) -> None:
        """Fill speed and heading from consecutive positions."""
        for i in range(1, len(fixes)):
            a, b = fixes[i - 1], fixes[i]
            dt = max(b.t - a.t, 1e-6)
            dx = (b.longitude - a.longitude) * 111_320.0 * math.cos(math.radians(a.latitude))
            dy = (b.latitude - a.latitude) * 111_320.0
            b.speed_kph = round(math.hypot(dx, dy) / dt * 3.6, 2)
            b.heading_deg = round((math.degrees(math.atan2(dx, dy)) + 360) % 360, 1)
        if len(fixes) > 1:
            fixes[0].speed_kph = fixes[1].speed_kph
            fixes[0].heading_deg = fixes[1].heading_deg

    # -- query ----------------------------------------------------------
    def at(self, t: float) -> GNSSFix:
        """Linearly interpolated fix at time t."""
        if t <= self.fixes[0].t:
            return self.fixes[0]
        if t >= self.fixes[-1].t:
            return self.fixes[-1]
        lo, hi = 0, len(self.fixes) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if self.fixes[mid].t <= t:
                lo = mid
            else:
                hi = mid
        a, b = self.fixes[lo], self.fixes[hi]
        f = (t - a.t) / max(b.t - a.t, 1e-9)
        return GNSSFix(
            t=t,
            latitude=a.latitude + f * (b.latitude - a.latitude),
            longitude=a.longitude + f * (b.longitude - a.longitude),
            speed_kph=a.speed_kph + f * (b.speed_kph - a.speed_kph),
            heading_deg=b.heading_deg,
        )

    def to_gpx(self, path: str, name: str = "replay route") -> None:
        gpx = ET.Element("gpx", version="1.1", creator="pothole-edge",
                         xmlns="http://www.topografix.com/GPX/1/1")
        trk = ET.SubElement(gpx, "trk")
        ET.SubElement(trk, "name").text = name
        seg = ET.SubElement(trk, "trkseg")
        for f in self.fixes:
            ET.SubElement(seg, "trkpt", lat=f"{f.latitude:.7f}",
                          lon=f"{f.longitude:.7f}")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(gpx).write(path, encoding="utf-8", xml_declaration=True)


# ----------------------------------------------------------------------
class IMUTrace:
    """Inertial trace, recorded or synthesised."""

    def __init__(self, samples: np.ndarray) -> None:
        # columns: t, ax, ay, az
        self.samples = samples

    @classmethod
    def synthetic(
        cls,
        duration_s: float,
        rate_hz: float = 100.0,
        impact_times: Optional[List[float]] = None,
        impact_magnitudes: Optional[List[float]] = None,
        road_roughness_g: float = 0.06,
        seed: int = 7,
    ) -> "IMUTrace":
        """
        Generate a trace with baseline surface roughness and discrete impacts.

        Impacts are modelled as damped oscillations, which is what a wheel
        striking a defect actually produces: a sharp initial deflection
        followed by suspension ringing.
        """
        rng = np.random.default_rng(seed)
        n = int(duration_s * rate_hz)
        t = np.arange(n) / rate_hz

        ax = rng.normal(0, road_roughness_g * 0.5, n)
        ay = rng.normal(0, road_roughness_g * 0.5, n)
        az = 1.0 + rng.normal(0, road_roughness_g, n)      # 1 g gravity

        # Low-frequency undulation of the road surface
        az += 0.03 * np.sin(2 * np.pi * 0.4 * t)

        if impact_times:
            mags = impact_magnitudes or [1.2] * len(impact_times)
            for ti, mg in zip(impact_times, mags):
                idx = int(ti * rate_hz)
                if not (0 <= idx < n):
                    continue
                span = int(0.30 * rate_hz)
                end = min(idx + span, n)
                k = np.arange(end - idx) / rate_hz
                # Damped oscillation: sharp strike then suspension response
                pulse = mg * np.exp(-18 * k) * np.sin(2 * np.pi * 14 * k)
                az[idx:end] += pulse
                ax[idx:end] += pulse * 0.28
                ay[idx:end] += pulse * 0.18

        return cls(np.column_stack([t, ax, ay, az]))

    def between(self, t0: float, t1: float) -> List[Tuple[float, float, float, float]]:
        m = (self.samples[:, 0] >= t0) & (self.samples[:, 0] < t1)
        return [tuple(r) for r in self.samples[m]]

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        np.save(path, self.samples)

    @classmethod
    def load(cls, path: str) -> "IMUTrace":
        return cls(np.load(path))


# ----------------------------------------------------------------------
class ReplaySession:
    """
    Synchronised playback of video, GNSS and inertial streams.

    Iterating the session yields ReplayFrame objects in virtual time. Playback
    is deterministic: the same session replayed twice produces identical input,
    which is what permits fair comparison between configurations.
    """

    def __init__(
        self,
        video_path: Optional[str] = None,
        image_dir: Optional[str] = None,
        track: Optional[GPXTrack] = None,
        imu: Optional[IMUTrace] = None,
        fps: float = 10.0,
        max_frames: Optional[int] = None,
    ) -> None:
        if not video_path and not image_dir:
            raise ValueError("provide either video_path or image_dir")

        self.video_path = video_path
        self.image_dir = image_dir
        self.fps = fps
        self.max_frames = max_frames

        self._images: List[Path] = []
        if image_dir:
            exts = {".jpg", ".jpeg", ".png", ".bmp"}
            self._images = sorted(
                q for q in Path(image_dir).iterdir() if q.suffix.lower() in exts
            )
            if not self._images:
                raise ValueError(f"no images found in {image_dir}")
            self.frame_count = len(self._images)
        else:
            import cv2
            cap = cv2.VideoCapture(video_path)
            self.frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            native = cap.get(cv2.CAP_PROP_FPS) or fps
            cap.release()
            self.fps = fps or native

        if max_frames:
            self.frame_count = min(self.frame_count, max_frames)
        self.duration = self.frame_count / self.fps

        self.track = track or GPXTrack.synthetic_route(
            DURBAN_ROUTE, self.duration, rate_hz=1.0)
        self.imu = imu or IMUTrace.synthetic(self.duration)

    # ------------------------------------------------------------------
    def __iter__(self) -> Iterator[ReplayFrame]:
        if self.image_dir:
            yield from self._iter_images()
        else:
            yield from self._iter_video()

    def _iter_images(self) -> Iterator[ReplayFrame]:
        import cv2
        dt = 1.0 / self.fps
        for i, q in enumerate(self._images[: self.frame_count]):
            t = i * dt
            img = cv2.imread(str(q))
            if img is None:
                continue
            yield ReplayFrame(i, t, img, self.track.at(t),
                              self.imu.between(t - dt, t + dt))

    def _iter_video(self) -> Iterator[ReplayFrame]:
        import cv2
        cap = cv2.VideoCapture(self.video_path)
        dt = 1.0 / self.fps
        i = 0
        try:
            while i < self.frame_count:
                ok, img = cap.read()
                if not ok:
                    break
                t = i * dt
                yield ReplayFrame(i, t, img, self.track.at(t),
                                  self.imu.between(t - dt, t + dt))
                i += 1
        finally:
            cap.release()

    # ------------------------------------------------------------------
    def summary(self) -> dict:
        return {
            "source": self.image_dir or self.video_path,
            "frames": self.frame_count,
            "fps": self.fps,
            "duration_s": round(self.duration, 2),
            "gnss_fixes": len(self.track.fixes),
            "imu_samples": len(self.imu.samples),
            "route_start": (round(self.track.fixes[0].latitude, 6),
                            round(self.track.fixes[0].longitude, 6)),
            "route_end": (round(self.track.fixes[-1].latitude, 6),
                          round(self.track.fixes[-1].longitude, 6)),
        }


# ----------------------------------------------------------------------
# A route through central Durban, used as the default synthetic track.
# Berea -> Umbilo -> Durban CBD -> Victoria Embankment.
DURBAN_ROUTE: List[Tuple[float, float]] = [
    (-29.8500, 31.0100),
    (-29.8547, 31.0182),
    (-29.8601, 31.0244),
    (-29.8655, 31.0290),
    (-29.8712, 31.0331),
    (-29.8768, 31.0295),
    (-29.8801, 31.0234),
    (-29.8760, 31.0175),
]
