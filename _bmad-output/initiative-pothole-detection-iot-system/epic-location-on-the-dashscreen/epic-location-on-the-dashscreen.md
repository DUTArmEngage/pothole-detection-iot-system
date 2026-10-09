---
type: epic
title: "Location on the Dashscreen"
parent: initiative-pothole-detection-iot-system
covers: [CAP-3, CAP-4, CAP-5, CAP-8]
after: []
assignee: ""
risk: medium
---

# Location on the Dashscreen

## Description

The vehicle's position, from the laptop's own location source, appears as a moving pin and trail on the Pi's 7" Dashscreen and says so when it goes stale. This epic also opens the project: folder layout, the shared module, packaging, fakes and start scripts. The spec owns the capabilities.

## Outcome

The presenter watches the vehicle move on the 7" screen and sees an honest stale state when the laptop stops answering; the spec's CAP-3 to CAP-5 success checks are the signal.

## Requirements

- CAP-3: the laptop serves current location, speed and heading at `GET /gps/current`, from a fake route first, then the laptop's own location source.
- CAP-4 (pin and trail part): the Dashscreen shows the pin and trail on a plain lat/lon canvas; the IMU panel lands in epic 2.
- CAP-5: stale state once the last fix's `ts` is over 3 s old.
- CAP-8 (Pi-side part): the Pi side runs on a fake GPS route replay with the laptop off.

## Done when

1. Both devices import GPS and IMU payloads, the timestamp convention, poll intervals, the stale threshold and host/port config from `shared/`, and the repo runs from a clone on the Pi and on the laptop.
2. `curl` from the Pi to `/gps/current` returns valid JSON with `lat`, `lon`, source `ts`, `speed_kmh` and `heading`, from a fake route replay and from the laptop's own location source.
3. On a known route, pins from the laptop's own location source match real positions.
4. The pin and trail move on the Pi's 7" screen at a 1 s poll.
5. With Pi and laptop clocks NTP-synced, the Dashscreen shows a stale state when the last fix's `ts` is over 3 s old.
6. The Pi side runs on the fake route with the laptop off, and the start scripts launch each device's services.

## Boundaries

The folder layout, `shared/` schemas and config, packaging, the laptop GPS service, the Pi GPS client, the Dashscreen map, `fake_gps`, and start scripts. Not IMU, video, detection or the Dashboard. It follows the capability chain CAP-3, CAP-4 and CAP-5.

## References

- parent — _bmad-output/initiative-pothole-detection-iot-system/initiative-pothole-detection-iot-system.md, covers CAP-3, CAP-4, CAP-5, CAP-8
- spec — _bmad-output/initiative-pothole-detection-iot-system/spec-pothole-detection-iot-system/spec-pothole-detection-iot-system.md, Capabilities and Constraints
- interfaces — spec-pothole-detection-iot-system/interfaces.md, GPS payload and poll intervals
- folder structure — spec-pothole-detection-iot-system/folder-structure.md
- delivery sequence — spec-pothole-detection-iot-system/delivery-sequence.md, phases 1 to 3 and 6

## Notes

- Decision: no separate platform-baseline epic; restructure, shared module, packaging, fakes and start scripts are the first stories of this epic (user's decision, 2026-10-09).
- Decision: the Pi runs from a clone of the whole repo so `shared/` imports from the repo root, with per-target requirements files (user's decision, 2026-10-09).
- Decision: maps are a plain lat/lon canvas with no tiles, so the demo works offline (user's decision, 2026-10-09).
- Decision: `shared/` holds the GPS and IMU payloads, the ISO 8601 UTC timestamp convention, poll intervals, the stale threshold and config; epic 3 adds the detection payload.
- Unknown: how the laptop reads its OS location (Windows or Linux) and whether that works on a network with no internet; the real-source story waits on it, so keep fake-first order.
- Unknown: the fake replay's shape (one module serving `/gps/current` in both modes, who derives speed and heading) and the known route file for the real-position check.
- Open question: epic 3's optional detection `lat`/`lon` needs a short GPS fix history on the laptop; decide at inception whether epic 1 retains one.
