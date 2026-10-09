---
type: initiative
title: "Live pothole detection from a vehicle, shown on two displays"
parent: none
covers: [CAP-1, CAP-2, CAP-3, CAP-4, CAP-5, CAP-6, CAP-7, CAP-8]
after: []
assignee: ""
risk: medium
---

# Live pothole detection from a vehicle, shown on two displays

## Description

A vehicle-mounted Raspberry Pi and a laptop capture road video, IMU motion and location, detect and grade potholes, and show the results live on the Pi's 7" Dashscreen and the laptop's presentation Dashboard. The spec owns the capabilities, constraints and non-goals; this initiative delivers them as a working prototype.

## Outcome

A presenter drives or replays a route and the audience sees, live, where the vehicle is, what the IMU reads, and each pothole detected with severity and confidence; the spec's success signal is the measure.

## Done when

1. On a dedicated network, the Dashscreen pin and trail move with live IMU while the vehicle moves (CAP-1, CAP-3, CAP-4).
2. The laptop Dashboard on an external screen shows map, live IMU, annotated video with severity and confidence per detection, and the HUD (CAP-2, CAP-6, CAP-7).
3. Pins match real positions on a known route using only the laptop's own location source (CAP-3).
4. The Dashscreen shows a stale state within 3 s of the laptop going silent (CAP-5).
5. Each device runs and demos alone against fake GPS and fake IMU data (CAP-8).

## Boundaries

Follows the spec's capabilities, one owner throughout. The Pi-side services and Dashscreen, the laptop GPS, detection and Dashboard code, and the `shared/` contracts between them. Not websockets or push, the dead GPS module, cloud sync or storage, multi-vehicle, weak-fix flagging, or privacy and distraction requirements; see the spec's non-goals. Tracer path: a replayed route puts a pin on the Dashscreen, then a pothole frame is detected and annotated on the Dashboard.

- Touch point: `motion` daemon on the Pi — configured through `rpi/motion.conf`, no code change; owner: epic-pothole-detection-on-road-video
- Touch point: laptop OS location services — consumed, not modified; owner: epic-location-on-the-dashscreen
- Touch point: NTP on the Pi and laptop — configured so source timestamps align, first needed by the 3 s stale check; owner: epic-location-on-the-dashscreen
- Touch point: `ref/models/pothole.pt` and `ref/edge/severity.py` — reused as the detection and severity starting point; owner: epic-pothole-detection-on-road-video

## References

- spec — _bmad-output/initiative-pothole-detection-iot-system/spec-pothole-detection-iot-system/spec-pothole-detection-iot-system.md, sections Capabilities and Constraints
- constraint — the same spec, section Constraints: HTTP polling only, I2C IMU, detection on the laptop, source timestamps with NTP, shared module
- delivery sequence — _bmad-output/initiative-pothole-detection-iot-system/spec-pothole-detection-iot-system/delivery-sequence.md, the brief's seven phases
- reference code — ref/imu.py, ref/edge/severity.py, ref/edge/detector.py, ref/models/pothole.pt

## Notes

- Decision: an initiative with several epics, in its own initiative folder rather than loose in backlog/ (user's decision, 2026-10-09).
- Decision: four epics along the spec's capability chains, built serially because epics 2, 3 and 4 share the Dashboard module (user's decision, 2026-10-09).
- Decision: no separate platform-baseline epic; its work is the first stories of epic 1 (user's decision, 2026-10-09).
- Decision: the Pi runs from a clone of the whole repo; maps are a plain lat/lon canvas with no tiles; a spike at the start of epic 3 settles how frames get a source time (user's decisions, 2026-10-09).
- Decision: CAP-4 and CAP-8 are shared; epic 1 delivers the pin and trail and the Pi-side fake run, epic 2 the IMU panel and the laptop-side fake run (2026-10-09).
- Assumption: the spec's folder was moved inside this initiative folder so the spec sits inside what it specifies; no content changed.
- Assumption: GPS poll 1 s and IMU poll 100 ms, as in the spec's Assumptions.
- Assumption: detection payload defaults to `ts`, `bbox`, `severity`, `confidence`, optional `lat`/`lon`; the user had no preference.
