# Interfaces

| Interface | Direction | Mechanism | Status |
|---|---|---|---|
| IMU | Sensor to Pi | I2C (MPU-6050) | Fixed; supersedes the brief's UART |
| Video | Pi to laptop | HTTP stream from `motion` | Fixed |
| GPS | Laptop to Pi | Pi polls `GET /gps/current` | Fixed (polling, no websockets) |
| IMU for dashboard | Pi to laptop | Laptop polls `GET /imu/latest`, which returns a short window (about 1 s) of samples | Same pattern as GPS |

## GPS payload

| Field | Type | Note |
|---|---|---|
| `lat`, `lon` | float | Required; from the laptop's OS location services |
| `ts` | ISO 8601 UTC | Required, timestamped at the source |
| `speed_kmh`, `heading` | float | Derived by the laptop from successive fixes; fills the dashboard HUD |
| `accuracy_m` | float | Optional; carried, not acted on by the UI for now |

## IMU sample payload

| Field | Type | Note |
|---|---|---|
| `ts` | ISO 8601 UTC | Source timestamp per sample |
| `ax`, `ay`, `az` | float | Acceleration, g |
| `gx`, `gy`, `gz` | float | Angular rate, deg/s |

## Poll intervals (recommended)

| Data | Interval | Note |
|---|---|---|
| GPS | 1 s | Map needs no faster updates |
| IMU | 100 ms | The 1 s window means each poll overlaps the last |

## Detection output

| Field | Values | Note |
|---|---|---|
| `ts` | ISO 8601 UTC | Frame source time; used to match IMU samples and GPS fixes |
| `bbox` | `x1`,`y1`,`x2`,`y2` floats | Pixels, as the reference `Detection`; drives the video annotation |
| `severity` | `low`, `medium`, `high` | Composite of box size, confidence and IMU impact |
| `confidence` | float | Model confidence, reported separately from severity |
| `lat`, `lon` | float, optional | Matched from the nearest GPS fix by `ts` when one exists |

The `ts`, `bbox` and `lat`/`lon` fields are a default derived from `ref/edge/detector.py`; the user had no preference, so build may adjust them.

## Frame source time (decided in epic 3's spike)

The `motion` MJPEG stream carries no per-frame timestamp. A frame's `ts` is the laptop's clock when the frame arrives, minus `VIDEO_LATENCY_S` (`shared/config.py`, default 0, env override) for capture and network delay. The laptop clock is NTP-synced, as are the Pi's IMU timestamps, so frame time and IMU time are comparable.

- Severity takes the peak vertical deviation `|az - 1|` over IMU samples within +/- `IMPACT_WINDOW_S` (0.3 s) of the frame `ts`. A strike lands after the pothole is seen, so grading of a frame waits (up to 0.6 s) for IMU samples past `ts + 0.3 s`; inference does not wait.
- No IMU samples in the window means severity falls back to the box-size and confidence blend.
- `lat`/`lon` come from the nearest laptop GPS fix within 2 s (`GPS_MATCH_S`); absent otherwise.
- Calibrating `VIDEO_LATENCY_S` (tap the camera in view while tapping the IMU, compare peaks) is a hardware task, still to do.
