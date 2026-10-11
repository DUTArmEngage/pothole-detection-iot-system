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
| Laptop | `python -m main_server.main --fake --fake-imu` | OS location and the Pi |

The Dashboard (laptop, port 8090) and Dashscreen (Pi, port 8080) serve their pages and
`/api/state` JSON on those ports.
