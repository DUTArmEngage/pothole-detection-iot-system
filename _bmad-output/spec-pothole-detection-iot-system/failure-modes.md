# Failure modes

| Risk | Impact | Mitigation |
|---|---|---|
| Laptop location accuracy (Windows location services are often coarse) | Pins land in the wrong place | None adopted: OS location only (dongle and phone declined); coarse fixes accepted and not flagged |
| Speed and heading derived from coarse 1 s fixes | Noisy or jumpy HUD values | Accepted; smoothing is a build decision |
| Clock drift between Pi and laptop | Detections misaligned with IMU and GPS, corrupting impact-based severity | Timestamp at the source; sync both with NTP |
| Single WiFi link carries video and telemetry | Lag or drops during the demo | Ethernet or a dedicated hotspot |
| Stale GPS if the laptop stops responding | Map shows a wrong position | Pi shows a "stale" state when `ts` is over 3 s old (CAP-5) |
| IMU bus unresponsive (I2C; the reference prototype needed a software bus on `/dev/i2c-3`) | No usable data | Probe the bus on the hardware before phase 4 |
| Latest-sample IMU polling misses the impact peak | Severity understates a pothole | Pi serves a window (about 1 s) of samples per poll |
