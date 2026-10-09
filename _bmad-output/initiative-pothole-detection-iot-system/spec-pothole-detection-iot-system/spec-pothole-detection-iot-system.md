---
id: SPEC-pothole-detection-iot-system
companions:
  - interfaces.md
  - components.md
  - folder-structure.md
  - failure-modes.md
  - delivery-sequence.md
sources: []
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# Pothole detection IoT system

## Why

A vision to realize: a vehicle-mounted system that captures road video, IMU motion data and location, detects potholes, and shows the results live on two displays — a 7" screen on the Raspberry Pi (Dashscreen) and a presentation dashboard on the laptop. The Pi's GPS module is dead, so location must come from the laptop, which splits the system across two devices that must stay aligned in time and position.

## Capabilities

- **CAP-1**
  - **intent:** The vehicle's live IMU motion data is available on both displays without missing impact peaks.
  - **success:** With the Pi reading the IMU, the laptop polling at 100 ms receives timestamped samples covering every interval, and both Dashscreen and Dashboard show them live.
- **CAP-2**
  - **intent:** The laptop receives the road video captured by the Pi's USB webcam.
  - **success:** The laptop opens the Pi's `motion` HTTP stream and plays the live road view.
- **CAP-3**
  - **intent:** The laptop provides the vehicle's current location, speed and heading to the Pi on request.
  - **success:** `curl` from the Pi to `GET /gps/current` returns valid JSON with `lat`, `lon` and a source-stamped `ts`, first from a replayed fake route, then from the laptop's OS location source with pins matching real positions and speed and heading derived from successive fixes.
- **CAP-4**
  - **intent:** The 7" Dashscreen shows where the vehicle is and what the IMU reads.
  - **success:** With the Pi polling GPS at 1 s, the map pin moves and leaves a trail on the 7" screen alongside live IMU data.
- **CAP-5**
  - **intent:** The Dashscreen never presents an outdated position as current.
  - **success:** When the laptop stops answering, the Dashscreen switches to a visible stale state once the last fix's `ts` is more than 3 s old.
- **CAP-6**
  - **intent:** Potholes in the road video are detected and graded, with severity informed by IMU impact.
  - **success:** Annotated video plays on the laptop with a severity (low, medium or high) and a confidence shown for each detection, severity using IMU impact from samples matched by timestamp.
- **CAP-7**
  - **intent:** A presenter can show the whole run on an external screen.
  - **success:** The laptop Dashboard shows map, live IMU, annotated video and HUD (speed, heading), and its window moves to an external screen with layout intact.
- **CAP-8**
  - **intent:** Each device's software can be built and demoed without the other device present.
  - **success:** The Pi side runs against a fake GPS route replay, and the laptop side runs against fake IMU samples, each with the other device powered off.

## Constraints

- Transport is HTTP polling only; no websockets or push.
- The IMU is an MPU-6050 read over I2C, not UART (decided; supersedes the brief).
- The Pi serves a short window (about 1 s) of timestamped IMU samples, not only the single latest one, so the laptop's 100 ms polls cannot miss an impact peak.
- Video is the Pi's `motion` HTTP stream; detection runs on the laptop, not the Pi.
- The laptop's OS location services are the only real location source; the Pi's GPS module, a USB dongle and a phone are not used, and speed and heading are derived on the laptop from successive fixes.
- Every payload is timestamped at its source in ISO 8601 UTC and Pi and laptop clocks are NTP-synced, because severity correlates detections with IMU samples by timestamp.
- Detection starts from `ref/models/pothole.pt` and severity from `ref/edge/severity.py` (low/medium/high from a composite of box size, confidence and IMU impact).
- Video and telemetry share one Pi–laptop link; the final run must work on a dedicated network (Ethernet or dedicated hotspot).
- Code is organised per deployment target, with payload definitions and config imported by both devices from one shared module.

## Non-goals

- Websockets or push transport.
- Using the dead GPS module.
- Cloud sync or persistent storage.
- Multi-vehicle support.
- Flagging weak GPS fixes in the UI; `accuracy_m` may be carried but is not acted on.
- Privacy or driver-distraction requirements; accepted as out of scope for this prototype demo.

## Success signal

- A full run works end to end on a dedicated network: the Dashscreen pin and trail move with live IMU, and the laptop Dashboard on an external screen shows map, live IMU, annotated video with severity and confidence, and HUD, with pins matching real positions.

## Assumptions

- Brief recommendations proceeded under, not confirmed: GPS poll 1 s and IMU poll 100 ms.
- The brief's "current `client/` and `ui/` folders" are not in this repo; phase 1 is treated as creating the layout here, with `ref/` as reference code beyond the model and severity method above.
- Phase 7's "tune detection overlap" means de-duplicating overlapping detection boxes (NMS/IoU).
- Detection payload fields beyond severity and confidence default to a frame source `ts`, a pixel `bbox`, and optional `lat`/`lon` matched from the nearest GPS fix; no preference was given, so build may adjust (see `interfaces.md`).
