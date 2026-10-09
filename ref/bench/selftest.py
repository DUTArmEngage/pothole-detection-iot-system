"""
selftest.py — Validates pipeline logic without a trained model.

Substitutes a stub detector so that fusion, severity classification,
geolocation, buffering and clustering can all be exercised before any model
exists. Run this first: if it passes, the only remaining unknown is the model
itself.

    python bench/selftest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "edge"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))

import numpy as np

from severity import SeverityClassifier, Severity
from fusion import IMUFusion, FusionMode, IMUSample
from buffer import EventBuffer, RoadEvent, cluster_events, haversine_m
from replay import GPXTrack, IMUTrace, ReplaySession, DURBAN_ROUTE

PASS, FAIL = "  PASS", "  FAIL"
results = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond))
    print(f"{PASS if cond else FAIL}  {name}" + (f"  — {detail}" if detail else ""))


# ----------------------------------------------------------------------
print("\n1. Severity classification")
sc = SeverityClassifier(frame_area=640 * 640)

small = sc.classify(box_area=1200, confidence=0.42, peak_impact_g=0.3)
check("small low-impact defect classified low", small.severity is Severity.LOW,
      f"score={small.score}")

big = sc.classify(box_area=60000, confidence=0.95, peak_impact_g=3.6)
check("large high-impact defect classified high", big.severity is Severity.HIGH,
      f"score={big.score}")

mid = sc.classify(box_area=20000, confidence=0.70, peak_impact_g=1.4)
check("intermediate defect classified medium", mid.severity is Severity.MEDIUM,
      f"score={mid.score}")

no_imu = sc.classify(box_area=20000, confidence=0.70, peak_impact_g=None)
check("classifier degrades gracefully without IMU", not no_imu.used_imu,
      f"score={no_imu.score}")

check("score increases monotonically with impact",
      sc.classify(60000, 0.9, 0.5).score < sc.classify(60000, 0.9, 3.5).score)

# ----------------------------------------------------------------------
print("\n2. IMU fusion and gating")
f = IMUFusion(mode=FusionMode.GATE_AND_CORROBORATE,
              trigger_threshold_g=0.45, corroboration_window_s=0.6)

for i in range(100):                      # 1 s of quiet road at 100 Hz
    f.add_sample(IMUSample(t=i / 100.0, ax=0.01, ay=0.01, az=1.0 + 0.02))
check("quiet road does not trigger inference", not f.should_infer(1.0))

f.add_sample(IMUSample(t=1.0, ax=0.3, ay=0.2, az=2.6))   # impact
check("impact triggers inference", f.should_infer(1.1))

d = f.evaluate(1.1)
check("impact corroborates detection", d.corroborated, d.reason)
check("peak magnitude captured", d.peak_g is not None and d.peak_g > 1.0,
      f"peak={d.peak_g:.2f} g")
check("corroboration window expires", not f.evaluate(3.0).corroborated)

f2 = IMUFusion(mode=FusionMode.DISABLED)
check("disabled mode always infers (Config C)", f2.should_infer(0.0))
check("disabled mode always corroborates", f2.evaluate(0.0).corroborated)

f3 = IMUFusion(trigger_threshold_g=0.45, baseline_alpha=0.05)
for i in range(400):                      # rough surface raises the baseline
    f3.add_sample(IMUSample(t=i / 100.0, ax=0, ay=0,
                            az=1.0 + np.random.default_rng(i).normal(0, 0.25)))
check("threshold adapts upward on rough surface",
      f3.effective_threshold_g > 0.45,
      f"{f3.effective_threshold_g:.3f} g vs 0.45 g nominal")

# ----------------------------------------------------------------------
print("\n3. GNSS replay")
track = GPXTrack.synthetic_route(DURBAN_ROUTE, duration_s=60.0, rate_hz=1.0)
check("track generated", len(track.fixes) >= 60, f"{len(track.fixes)} fixes")

a, b = track.at(10.0), track.at(20.0)
moved = haversine_m(a.latitude, a.longitude, b.latitude, b.longitude)
check("vehicle advances along route", 50 < moved < 300, f"{moved:.0f} m in 10 s")
check("speed is physically plausible", 20 < b.speed_kph < 80, f"{b.speed_kph:.1f} km/h")
check("speed derived", b.speed_kph > 0, f"{b.speed_kph:.1f} km/h")
check("heading derived", 0 <= b.heading_deg <= 360, f"{b.heading_deg:.0f}°")
check("interpolation is bounded",
      track.fixes[0].t <= track.at(-5).t and track.at(1e6).t <= track.duration)

# ----------------------------------------------------------------------
print("\n4. IMU trace synthesis")
imu = IMUTrace.synthetic(30.0, rate_hz=100.0,
                         impact_times=[5.0, 12.0, 21.5],
                         impact_magnitudes=[1.0, 2.4, 0.8])
check("trace length correct", len(imu.samples) == 3000, f"{len(imu.samples)} samples")

win = imu.between(11.9, 12.3)
peak = max(abs(s[3] - 1.0) for s in win)
check("impact present in trace", peak > 1.0, f"peak {peak:.2f} g")

quiet = imu.between(16.0, 18.0)
qpeak = max(abs(s[3] - 1.0) for s in quiet)
check("quiet section stays near baseline", qpeak < 0.5, f"peak {qpeak:.2f} g")

# ----------------------------------------------------------------------
print("\n5. Event buffer and store-and-forward")
db = "data/selftest.db"
Path(db).unlink(missing_ok=True)
Path("data").mkdir(exist_ok=True)
buf = EventBuffer(db)

evs = []
for i in range(10):
    e = RoadEvent.new(
        device_id="selftest", latitude=-29.85 + i * 0.0001,
        longitude=31.01 + i * 0.0001, confidence=0.6 + i * 0.03,
        severity=["low", "medium", "high"][i % 3], severity_score=0.3 + i * 0.05,
        defect_class="pothole", box=[10, 10, 90, 90],
    )
    buf.enqueue(e)
    evs.append(e)

check("all events persisted", buf.stats()["total_events"] == 10)
check("all events start unacknowledged", buf.stats()["pending"] == 10)

for e in evs[:6]:
    buf.acknowledge(e.event_id)
s = buf.stats()
check("acknowledgement reduces pending", s["pending"] == 4 and s["acknowledged"] == 6)
check("delivery rate computed", abs(s["delivery_rate"] - 0.6) < 1e-6,
      f"{s['delivery_rate']}")
check("pending returned oldest-first", buf.pending()[0]["event_id"] == evs[6].event_id)

payload = evs[0].payload_bytes
check("payload compact", 150 < payload < 900, f"{payload} bytes/event")

# ----------------------------------------------------------------------
print("\n6. Spatial clustering")
near = [{"event_id": f"n{i}", "latitude": -29.8500 + i * 0.00002,
         "longitude": 31.0100, "confidence": 0.8, "severity": "medium"}
        for i in range(5)]
far = [{"event_id": "f1", "latitude": -29.8600, "longitude": 31.0200,
        "confidence": 0.9, "severity": "high"}]
cl = cluster_events(near + far, radius_m=15.0)
check("nearby observations merge", len(cl) == 2, f"{len(cl)} clusters from 6 events")

main = max(cl, key=lambda c: c["observation_count"])
check("observation count accumulates", main["observation_count"] == 5)
check("cluster centroid averages",
      abs(main["latitude"] - (-29.8500 + 2 * 0.00002)) < 1e-5)

esc = cluster_events([
    {"event_id": "a", "latitude": -29.85, "longitude": 31.01,
     "confidence": 0.7, "severity": "low"},
    {"event_id": "b", "latitude": -29.85, "longitude": 31.01,
     "confidence": 0.9, "severity": "high"},
], radius_m=15.0)
check("cluster takes highest severity", esc[0]["severity"] == "high")

# ----------------------------------------------------------------------
print("\n7. Payload schema")
e = evs[0]
import json
d = json.loads(e.to_json())
required = {"event_id", "device_id", "detected_at", "latitude", "longitude",
            "confidence", "severity", "severity_score", "defect_class", "box"}
check("all required fields present", required.issubset(d.keys()),
      f"{len(d)} fields")
check("schema version declared", d.get("schema") == "roadcond/1.0")
check("timestamp is ISO 8601 UTC", "T" in d["detected_at"] and
      ("+00:00" in d["detected_at"] or d["detected_at"].endswith("Z")))

# ----------------------------------------------------------------------
print("\n8. End-to-end simulation with a stub detector")


class StubDetection:
    def __init__(self, area, conf):
        self.x1, self.y1 = 100.0, 100.0
        side = area ** 0.5
        self.x2, self.y2 = 100.0 + side, 100.0 + side
        self.confidence = conf
        self.class_id = 0
        self.class_name = "pothole"

    @property
    def area(self):
        return (self.x2 - self.x1) * (self.y2 - self.y1)


rng = np.random.default_rng(11)
duration = 40.0
impacts = [6.0, 14.0, 22.0, 31.0]
mags = [1.1, 2.7, 0.7, 2.0]

fus = IMUFusion(mode=FusionMode.GATE_AND_CORROBORATE, trigger_threshold_g=0.45)
sev = SeverityClassifier(frame_area=640 * 640)
trace = IMUTrace.synthetic(duration, 100.0, impacts, mags)
tr = GPXTrack.synthetic_route(DURBAN_ROUTE, duration, rate_hz=1.0)

Path("data/e2e.db").unlink(missing_ok=True)
b2 = EventBuffer("data/e2e.db")

fps, confirmed, inferred, gated = 10.0, 0, 0, 0
for i in range(int(duration * fps)):
    t = i / fps
    for s in trace.between(t - 1 / fps, t + 1 / fps):
        fus.add_sample(IMUSample(t=s[0], ax=s[1], ay=s[2], az=s[3]))
    if not fus.should_infer(t):
        gated += 1
        continue
    inferred += 1
    det = StubDetection(area=rng.uniform(3000, 55000), conf=rng.uniform(0.4, 0.97))
    dec = fus.evaluate(t)
    if not dec.corroborated:
        continue
    sr = sev.classify(det.area, det.confidence, dec.peak_g)
    fix = tr.at(t)
    b2.enqueue(RoadEvent.new(
        device_id="e2e", latitude=fix.latitude, longitude=fix.longitude,
        confidence=det.confidence, severity=sr.severity.value,
        severity_score=sr.score, defect_class="pothole",
        box=[det.x1, det.y1, det.x2, det.y2], peak_impact_g=dec.peak_g,
        speed_kph=fix.speed_kph))
    confirmed += 1

total = int(duration * fps)
reduction = 100 * gated / total
st = b2.stats()

check("gating reduced inference load", reduction > 50,
      f"{reduction:.1f}% of {total} frames gated out")
check("events generated at impacts", confirmed > 0,
      f"{confirmed} events from {len(impacts)} impacts")
check("events persisted", st["total_events"] == confirmed)
check("severity spread across classes", len(st["by_severity"]) >= 2,
      str(st["by_severity"]))

cls = cluster_events(b2.all_events(), radius_m=15.0)
check("clusters no more than events", len(cls) <= confirmed,
      f"{len(cls)} clusters from {confirmed} events")

# ----------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok in results if ok)
print(f"  {passed}/{len(results)} checks passed")
print("=" * 60)

if passed == len(results):
    print("\n  Pipeline logic verified. The only outstanding component is the\n"
          "  trained model — see train/README.md.\n")
else:
    print("\n  Failures listed above.\n")
    sys.exit(1)
