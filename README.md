# Pothole Edge Detection Framework

Reference implementation for the MIoT dissertation *An IoT-Driven Edge Computing
Framework for Real-Time Pothole Detection and Infrastructure Mapping*
(M.M.V. Mthembu, 21959204, Durban University of Technology).

Detects road surface defects on a Raspberry Pi, corroborates each detection
against inertial data, attaches a GNSS position, classifies severity, buffers
through connectivity loss, and publishes to a road-condition database rendered
as a geospatial map.

---

## Why this runs on a bench

Field work is not required to produce most of the Chapter Four measurements.
The system under test — model, fusion logic, buffer, MQTT client — is the real
deployment code. Only its *environment* is replayed: a video stands in for the
camera, a GPX track for the GNSS receiver, and a generated trace for the
accelerometer. This is hardware-in-the-loop evaluation, and it is how embedded
systems are normally assessed.

| Measurement | Bench | Vehicle |
|---|---|---|
| Precision, recall, F1, mAP | yes | — |
| Inference latency, throughput, FPS | yes | — |
| CPU, memory, thermal behaviour | yes | — |
| Power consumption | yes, with a USB meter | — |
| End-to-end latency | yes | — |
| MQTT delivery reliability | yes | — |
| Offline buffer recovery | yes, unplug the cable | — |
| Bandwidth per hour | yes | — |
| Cloud-only vs edge baseline | yes | — |
| Vision-only vs fusion baseline | yes | — |
| **GNSS accuracy vs surveyed truth** | **no** | **required** |

One metric out of eleven needs a vehicle. Report it as a limitation and the
evaluation still stands.

---

## Layout

```
pothole-edge/
├── edge/               runs on the Raspberry Pi
│   ├── detector.py       TFLite / PyTorch inference wrapper
│   ├── fusion.py         inertial gating and corroboration
│   ├── severity.py       composite severity classification
│   ├── buffer.py         SQLite store-and-forward, spatial clustering
│   ├── publisher.py      MQTT QoS 1 with ack-gated release
│   └── pipeline.py       the assembled pipeline
├── harness/
│   └── replay.py         synchronised video + GPX + IMU playback
├── bench/
│   ├── selftest.py       validates logic without a model
│   └── run_experiment.py produces the Chapter 4 tables
├── train/
│   └── train.py          Colab training and INT8 export
├── dashboard/
│   └── app.py            municipal and road-user map views
└── models/               trained weights go here
```

---

## Two-week plan

### Days 1–2 — Verify the logic, get the data

```bash
pip install numpy opencv-python-headless
python bench/selftest.py          # 40 checks, no model needed
```

All forty should pass. That confirms fusion, severity, geolocation, buffering
and clustering are sound before a model exists.

Then obtain a dataset. Two sources, and you want both:

**Public benchmark.** Roboflow Universe hosts several pothole datasets in YOLO
format, and RDD2022 (the Maeda et al. dataset already cited in your literature
review) is publicly available. Either gives you a few thousand annotated
images. Citing RDD is the stronger option — it ties your empirical work
directly to Chapter Two.

**A local test set.** Walk around Berea and Glenwood with a phone for two or
three hours and photograph 200–300 potholes. Vary the time of day. This is not
a vehicle survey; it is a walk. Annotate in Roboflow or LabelImg — roughly four
hours of work. This becomes your South African held-out test set and it is what
lets you claim a response to Gap 5, which no reviewed study addresses.

### Days 3–4 — Train

Upload to Google Colab, select a GPU runtime:

```python
!pip install ultralytics
!python train.py --dataset-root /content/dataset --epochs 80 --model yolov8n.pt
```

Roughly 40–90 minutes. Use `yolov8n` — the nano variant. Larger variants will
not sustain a usable frame rate on a Pi, and demonstrating that constraint is
part of your contribution.

Outputs `models/pothole.pt`, `models/pothole.tflite` and `models/metrics.json`.
The metrics file populates Table 4.2.

### Days 5–6 — Deploy to the Pi

```bash
sudo apt install -y python3-pip python3-opencv mosquitto mosquitto-clients
pip3 install numpy paho-mqtt psutil
pip3 install tflite-runtime      # or: pip3 install tensorflow
```

Copy `models/pothole.tflite` across, then confirm inference runs:

```bash
python3 -c "
import sys; sys.path.insert(0,'edge')
from detector import Detector
import numpy as np, time
d = Detector('models/pothole.tflite'); d.warmup()
t = [d.infer(np.zeros((640,640,3),dtype=np.uint8)).latency_ms for _ in range(20)]
print(f'{sum(t)/len(t):.1f} ms mean  |  {1000/(sum(t)/len(t)):.1f} fps')
"
```

Record that figure. It is your headline system-performance result.

### Days 7–9 — Run the experiment

```bash
python3 bench/run_experiment.py \
  --images data/test_images \
  --model models/pothole.tflite \
  --repeats 5 \
  --publish
```

Writes `results/chapter4_tables.md` — the tables, formatted, ready to paste —
and `results/raw_measurements.json` for your appendix.

Put a USB power meter inline on the Pi supply and record draw at idle and under
sustained inference. That is the power figure your proposal committed to and
which the current dissertation draft omits.

### Days 10–12 — Dashboard and rehearsal

```bash
python3 dashboard/app.py --db data/events.db --port 8000
```

Two views from one database. The municipal view shows everything; the road-user
view withholds low-severity detections. That distinction is your dual-audience
argument made concrete, and it is worth drawing attention to in the
presentation.

### Days 13–14 — Write up

Replace every `[ ]` placeholder in Chapter Four with a recorded value. Every
number now traces to a measurement in `raw_measurements.json`.

---

## Demonstration sequence

Fifteen minutes, four moves:

1. **The problem.** One slide. Municipalities cannot see their networks; road
   users cannot see hazards.
2. **The bench.** Show the architecture diagram. Say plainly that the
   environment is replayed and the system is real. Do not apologise for it —
   name it as hardware-in-the-loop evaluation and move on.
3. **Live run.** Start `run_experiment.py`, let detections appear on the
   dashboard. Then unplug the network cable mid-run. Events keep accumulating
   locally. Plug it back in and watch the buffer drain. That single moment
   demonstrates Gap 2 more convincingly than any table.
4. **The numbers.** Open `chapter4_tables.md`. Real measurements, taken on a
   desk, with a t-test attached.

---

## Configurations

The experiment runs three arrangements over identical replayed input, which is
what permits causal attribution rather than mere observation.

| | Inference | Fusion | Local buffer |
|---|---|---|---|
| **A** proposed | edge | on | yes |
| **B** cloud-only | remote | on | no |
| **C** vision-only | edge | off | yes |

A vs B isolates the contribution of edge processing. A vs C isolates the
contribution of inertial corroboration. Paired t-test at α = 0.05, per the
approved proposal.

---

## What is honest to claim

**Claim:** the framework detects, geolocates, classifies and delivers road
condition events on commodity hardware, with measured performance, and survives
connectivity interruption.

**Do not claim:** field-validated GNSS accuracy, fleet-scale operation,
improved municipal maintenance outcomes, or a production service. None of these
follow from bench evaluation, and asserting them invites the one question in a
viva you cannot answer.

The severity thresholds in `severity.py` and the inertial threshold in
`fusion.py` are starting values. Calibrate them against your own data and report
the calibrated figures — an uncalibrated threshold presented as a finding is a
construct validity problem.
