---
type: epic
title: "Live IMU on both displays"
parent: initiative-pothole-detection-iot-system
covers: [CAP-1, CAP-4, CAP-8]
after: []
assignee: ""
risk: medium
---

# Live IMU on both displays

## Description

The Pi reads the MPU-6050 over I2C and serves a short window of timestamped samples; the laptop polls it and both the Dashscreen and a Dashboard shell show it live. The spec owns the capabilities.

## Outcome

The presenter sees IMU motion react live on both displays without missing impact peaks; the spec's CAP-1 success check is the signal.

## Requirements

- CAP-1: IMU samples (`ts`, `ax`, `ay`, `az`, `gx`, `gy`, `gz`) reach both displays; the Pi serves about 1 s of samples per poll.
- CAP-4 (IMU part): the IMU panel sits alongside the pin on the Dashscreen.
- CAP-8 (laptop-side part): the laptop side runs on `fake_imu` with the Pi off.

## Done when

1. The I2C bus is probed and the Pi reads the MPU-6050 on the real hardware.
2. `GET /imu/latest` returns a window of about 1 s of timestamped samples, and the laptop `pi_client` polling at 100 ms receives every interval.
3. Live IMU shows alongside the pin on the Dashscreen and in a Dashboard shell panel.
4. The laptop side runs on `fake_imu` with the Pi off.
5. The start scripts launch the Pi server and the Dashboard, and `docs/api.md` describes `/imu/latest`.

## Boundaries

The Pi IMU reader and HTTP server, the laptop `pi_client` IMU path, the Dashboard shell with an IMU panel, the Dashscreen IMU panel, and `fake_imu`. Not video, detection or Dashboard layout.

## References

- parent — _bmad-output/initiative-pothole-detection-iot-system/initiative-pothole-detection-iot-system.md, covers CAP-1, CAP-4, CAP-8
- spec — _bmad-output/initiative-pothole-detection-iot-system/spec-pothole-detection-iot-system/spec-pothole-detection-iot-system.md, Capabilities and Constraints
- interfaces — spec-pothole-detection-iot-system/interfaces.md, IMU sample payload
- failure modes — spec-pothole-detection-iot-system/failure-modes.md, I2C row
- reference code — ref/imu.py, software I2C bus on `/dev/i2c-3` on the prototype

## Notes

- Unknown: whether the I2C bus answers on the current Pi; make the probe the first story so a failure surfaces before anything waits on it.
