---
type: epic
title: "Pothole detection on the road video"
parent: initiative-pothole-detection-iot-system
covers: [CAP-2, CAP-6]
after: []
assignee: ""
risk: high
---

# Pothole detection on the road video

## Description

The laptop pulls the Pi's `motion` stream, detects potholes, grades each with severity from box size, confidence and IMU impact, and shows the annotated video on the Dashboard. The spec owns the capabilities.

## Outcome

The audience sees each pothole boxed and labelled with severity and confidence on live video; the spec's CAP-2 and CAP-6 success checks are the signal.

## Requirements

- CAP-2: the laptop plays the Pi's `motion` HTTP stream of the USB webcam.
- CAP-6: detection starts from `ref/models/pothole.pt`; severity (low, medium, high) with confidence per detection, using IMU impact matched by timestamp.

## Done when

1. A spike has settled how a frame gets a source time, and the choice is written into `interfaces.md`.
2. The `motion` stream plays on the laptop and runs through detection from `ref/models/pothole.pt`, with overlapping boxes de-duplicated.
3. Severity uses box size, confidence and IMU impact from samples matched to the frame's time.
4. Annotated video with severity and confidence plays in the Dashboard, and the detection payload is in `shared/`.
5. The whole path runs on the real Pi and laptop, the start scripts launch `motion` and detection, and `docs/api.md` is current.

## Boundaries

`motion.conf`, the video pull, `image_processing`, severity fusion, the detection payload in `shared/`, and the annotated video panel. Not Dashboard layout or HUD.

## References

- parent — _bmad-output/initiative-pothole-detection-iot-system/initiative-pothole-detection-iot-system.md, covers CAP-2, CAP-6
- spec — _bmad-output/initiative-pothole-detection-iot-system/spec-pothole-detection-iot-system/spec-pothole-detection-iot-system.md, Capabilities and Constraints
- interfaces — spec-pothole-detection-iot-system/interfaces.md, Detection output
- reference code — ref/edge/detector.py, ref/edge/severity.py, ref/models/pothole.pt

## Notes

- Decision: a spike at the start of this epic settles how frames get a source time before the severity story (user's decision, 2026-10-09).
- Unknown: whether `ref/models/pothole.pt` loads under the project's `ultralytics` and meets a usable frame rate on the laptop.
- Unknown: the `motion` stream carries no per-frame source time; the spike answers this and the severity story waits on it.
