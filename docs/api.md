# API contracts

HTTP polling only. All timestamps are ISO 8601 UTC (`2026-10-11T17:37:48.437Z`), stamped at the
source; Pi and laptop clocks are NTP-synced. Defaults live in `shared/config.py`.

## Laptop — `GET /gps/current` (port 8000)

Served by `main_server/gps.py`. The Pi polls it every 1 s.

```json
{"lat": -29.8506, "lon": 31.0068, "ts": "2026-10-11T17:37:48.437Z",
 "speed_kmh": 12.3, "heading": 90.0, "accuracy_m": 55.0}
```

- `speed_kmh`, `heading` (degrees clockwise from north) are derived on the laptop from successive
  fixes; `null` on the first fix, and `heading` is `null` until the vehicle has moved.
- `503 {"error": "no fix yet"}` until the first fix is available (the OS source can take ~20 s).
- The Dashscreen treats a fix older than 3 s (`STALE_AFTER_S`) as stale.

## Pi — `GET /imu/latest` (port 8001)

Served by `rpi/server/main.py`. The laptop polls it every 100 ms; the Dashscreen polls its own
Pi's server on localhost.

```json
{"window_s": 1.0,
 "samples": [{"ts": "2026-10-11T17:37:48.437Z",
              "ax": 0.01, "ay": -0.02, "az": 1.00, "gx": 0.1, "gy": 0.0, "gz": -0.2}]}
```

- `samples` holds every sample within `window_s` (about 1 s, ~100 samples at 100 Hz) of the newest
  one, oldest first. Because polls are 100 ms apart, consecutive windows overlap, so a short impact
  peak cannot fall between two polls. Clients de-duplicate by `ts`.
- Acceleration is in g, angular rate in deg/s.
- An empty `samples` array means the reader has produced nothing yet.
- If a response starts after the newest sample a client already holds, samples were missed;
  `ImuPoller` counts these as `gaps`.

## Running with fakes

| Device | Command | Replaces |
|---|---|---|
| Pi | `python3 -m rpi.server.main --fake` | the MPU-6050 |
| Laptop | `python -m main_server.main --fake --fake-imu --video ref/vid_potholes.mp4` | OS location, the Pi and the webcam |

The Dashboard (laptop, port 8090) and Dashscreen (Pi, port 8080) serve their pages and
`/api/state` JSON on those ports.

## Pi — `motion` stream (port 8081)

`motion -n -c rpi/motion.conf` serves the USB webcam as MJPEG at `http://<pi>:8081/`. The laptop
opens it with OpenCV. Frames carry no timestamp; see "Frame source time" in
`_bmad-output/.../spec-pothole-detection-iot-system/interfaces.md`.

## Laptop Dashboard (port 8090)

| Path | Returns |
|---|---|
| `/` | the Dashboard page |
| `/video.mjpg` | annotated video, `multipart/x-mixed-replace` JPEG frames (boxes, severity, confidence) |
| `/api/state` | `{"imu": ..., "fake_imu": bool, "gps": {"fix", "trail", "age_s", "stale"}, "video": {"connected", "error", "frames", "fps", "latency_ms", "recent": [Detection], "events": [...]}}` |
| `/api/pins` | every saved pin, all runs: `[{id, ts, lat, lon, accuracy_m, severity, confidence, session, image, current}]` |
| `/api/pins.geojson` | the same pins as a GeoJSON download (`[lon, lat]` coordinates) |
| `/pins/<id>.jpg` | the annotated snapshot saved with a pin |
| `/imu_chart.js`, `/vendor/leaflet.js`, `/vendor/leaflet.css` | page scripts and styles (Leaflet 1.9.4 is vendored) |

### Detection payload (`shared/schemas.py: Detection`)

```json
{"ts": "2026-10-11T18:11:06.558Z", "bbox": [266.4, 95.9, 460.8, 142.0],
 "severity": "medium", "confidence": 0.837, "lat": -29.8533, "lon": 31.0027}
```

`severity` is `low`, `medium` or `high`; `bbox` is `[x1, y1, x2, y2]` in pixels; `lat`/`lon` are
`null` when no GPS fix is within 2 s of `ts`.

### Laptop options

`python -m main_server.main [--video URL|FILE] [--model PATH] [--no-detect] [--fake] [--fake-imu] [--pi URL] [--open]`

Without `--video` the laptop reads the Pi's motion stream. `--video ref/vid_potholes.mp4` replays
the sample road video, looped at its own frame rate, so detection runs with no Pi.

`video.events` is one entry per pinned pothole encounter in this run: detections at or above the pin
confidence, with a position, within 2 s of each other merge into one event that keeps the worst
severity. `video.saving` says whether pins are being written to the database.
`gps.trail` is `[[lat, lon], ...]`, the last 900 fixes (about 15 min).

## Map and saved pins

The Dashboard map is OpenStreetMap (Leaflet, standard tiles from `tile.openstreetmap.org`). It opens
on the laptop's current position, follows it until you drag the map (then "Recenter" appears), and
shows the accuracy circle, the trail and the pins. Tiles need internet; without it the pins and
trail still draw on a blank map.

A detection at or above the pin confidence (default **0.80**, set with `--pin-confidence` or
`PIN_CONFIDENCE`) is pinned **at the laptop's position when the frame was seen**, coloured by
severity, and saved to SQLite (`data/potholes.db`, option `--pins-db`) with an annotated snapshot in
`data/pins/<id>.jpg`. Pins from earlier runs reload on start, drawn fainter, and are never deleted
by the app. `--no-save` shows pins without saving them.

Table `pins`: `id, ts, lat, lon, accuracy_m, severity, confidence, session, image`.

`lat`/`lon` is where the laptop is, not where the pothole is: the OS reports the laptop's position
(often tens of metres off), and a pothole seen by the camera can be several metres ahead.
