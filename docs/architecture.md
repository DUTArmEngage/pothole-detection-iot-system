# Architecture

## Components and data flows

| Device | Component | Entry point | Port |
|---|---|---|---|
| Pi | webcam stream | `motion -n -c rpi/motion.conf` | 8081 |
| Pi | IMU reader + server | `python3 -m rpi.server.main` | 8001 |
| Pi | Dashscreen (GPS client, 7" UI) | `python3 -m rpi.dashscreen.main` | 8080 |
| Laptop | GPS service + Dashboard | `python -m main_server.main` | 8000, 8090 |

```
 MPU-6050 --I2C--> rpi.server.imu --1 s window--> GET /imu/latest ----+--> laptop pi_client (100 ms poll)
                                                                      '--> Dashscreen (localhost poll)
 USB webcam --> motion --MJPEG--> laptop FrameSource --> detection --> severity --> Dashboard /video.mjpg
 Windows location --> main_server.location --> GpsService --> GET /gps/current --> Pi gps_client (1 s poll)
                                                          '--> Dashboard map, HUD, detection lat/lon
```

Every payload is timestamped at its source in ISO 8601 UTC. Severity joins three streams by time:
a frame's `ts`, the IMU samples around it, and the nearest GPS fix. Pi and laptop clocks must be
NTP-synced; clock drift directly corrupts impact-based severity.

Frame time: the laptop's clock on frame arrival minus `VIDEO_LATENCY_S`
(see `interfaces.md` in the spec folder).

## Failure behaviour

| Condition | What the user sees |
|---|---|
| Laptop stops answering `/gps/current` | Dashscreen shows a red STALE banner once the last fix is over 3 s old |
| Pi unreachable from the laptop | Dashboard IMU chip turns red; video chip shows "connecting" and retries every second |
| No GPS fix yet | Dashboard GPS chip reads "waiting for fix"; detections carry no `lat`/`lon` and are not plotted |
| `motion` not installed on the Pi | `start_pi.sh` warns and skips the stream |

## Network

Video and telemetry share one link, so the final run should use a dedicated network.

**Ethernet cable, no router (simplest).** Give each end a static address on one subnet:

- Laptop (Windows, run as admin; adapter name from `ipconfig`):
  `netsh interface ip set address "Ethernet" static 192.168.50.1 255.255.255.0`
- Pi: `sudo nmcli con mod "Wired connection 1" ipv4.method manual ipv4.addresses 192.168.50.2/24`
  then `sudo nmcli con up "Wired connection 1"`

Then `LAPTOP_HOST=192.168.50.1` on the Pi and `PI_HOST=192.168.50.2` on the laptop.

**Dedicated hotspot.** Join both devices to the same phone or router hotspot and use the addresses
it assigns.

NTP needs a time source. With no internet on the link, either run both devices against one
reference (for example `chrony` on the laptop as the server and the Pi pointing at it) or sync
both from the internet before disconnecting and accept a small drift over the demo. Windows
location services may also need internet; check the fix before the presentation.

Check the link from the laptop: `curl http://192.168.50.2:8001/imu/latest` and
`curl http://192.168.50.2:8081/` (the stream), and from the Pi:
`curl http://192.168.50.1:8000/gps/current`.

## Presenting

Open `http://localhost:8090/` on the laptop (or start with `--open`), drag the window to the
external screen and press `F` for fullscreen. The page fills the window without scrolling, rescales
when the window or the screen's pixel ratio changes, and stacks into one column below 900 px wide.
