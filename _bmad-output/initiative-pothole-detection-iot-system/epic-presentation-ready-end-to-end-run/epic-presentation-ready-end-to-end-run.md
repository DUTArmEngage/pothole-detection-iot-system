---
type: epic
title: "Presentation-ready end-to-end run"
parent: initiative-pothole-detection-iot-system
covers: [CAP-7]
after: []
assignee: ""
risk: low
---

# Presentation-ready end-to-end run

## Description

The laptop Dashboard becomes the presentation surface: map, live IMU, annotated video and HUD in a layout that survives a move to an external screen, run end to end on a dedicated network. The spec owns the capabilities.

## Outcome

A presenter runs the whole demo on an external screen over a dedicated network; the spec's CAP-7 success check and its success signal are the measure.

## Requirements

- CAP-7: the Dashboard shows map, live IMU, annotated video and HUD (speed, heading), and its window moves to an external screen with layout intact.

## Done when

1. The Dashboard shows the map, live IMU, annotated video and HUD with speed and heading.
2. Its window moves to an external screen with the layout intact.
3. A full run works end to end on real hardware over a dedicated network (Ethernet or dedicated hotspot), with the laptop's own location.
4. `docs/architecture.md`, `docs/api.md` and the README match what was built.

## Boundaries

Dashboard layout and HUD, the map panel, external-screen behaviour, network setup and the docs. Not new data sources.

## References

- parent — _bmad-output/initiative-pothole-detection-iot-system/initiative-pothole-detection-iot-system.md, covers CAP-7
- spec — _bmad-output/initiative-pothole-detection-iot-system/spec-pothole-detection-iot-system/spec-pothole-detection-iot-system.md, CAP-7 and Success signal
- delivery sequence — spec-pothole-detection-iot-system/delivery-sequence.md, phase 7

## Notes

- Unknown: whether OS location works on the dedicated network if it has no internet; epic 1's real-source story reports it first.
