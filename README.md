# Pothole detection IoT system

A vehicle-mounted Raspberry Pi and a laptop capture road video, IMU motion and location, detect and
grade potholes, and show the result live on two displays: a 7" **Dashscreen** on the Pi and a
presentation **Dashboard** on the laptop. The spec lives in
`_bmad-output/initiative-pothole-detection-iot-system/`.

```
Pi (rpi/)                                        Laptop (main_server/)
  USB webcam -> motion  --- MJPEG :8081 ------->   detection (ref/models/pothole.pt) + severity
  MPU-6050 -> /imu/latest :8001 --- polled 100 ms ->   Dashboard :8090  (video, map, IMU, HUD)
  Dashscreen :8080  <--- polls 1 s --- /gps/current :8000 <- laptop OS location
```

Transport is HTTP polling only. See `docs/architecture.md` for the data flows and network setup,
and `docs/api.md` for every endpoint and payload.

## Layout

| Folder | Runs on | Contents |
|---|---|---|
| `shared/` | both | payload schemas, config (hosts, ports, poll intervals), shared web JS |
| `rpi/` | Pi | `motion.conf`, IMU reader and `/imu/latest` server, GPS client, Dashscreen |
| `main_server/` | laptop | `/gps/current` service, OS location source, detection pipeline, Dashboard |
| `tests/` | either | unit and integration tests, plus the fakes (`fake_gps.py`, `fake_imu.py`) |
| `scripts/` | either | `start_pi.sh`, `start_laptop.bat` |
| `ref/` | reference | the earlier bench framework, the model `ref/models/pothole.pt`, severity code, sample video |

## Try it on one machine, no hardware

```bash
pip install -r main_server/requirements.txt
python -m main_server.main --fake --fake-imu --video ref/vid_potholes.mp4 --open
```

That replays a fake route, a simulated IMU and the sample road video, and opens the Dashboard at
http://localhost:8090/. To also see the Dashscreen, in a second terminal:

```bash
python -m rpi.server.main --fake          # simulated IMU on :8001
python -m rpi.dashscreen.main             # http://localhost:8080/
```

### Lab demo: webcam on this laptop, no Pi, real location

```bash
python -m main_server.main --video 1 --no-imu --open     # --video 0 for the other camera
```

The map is OpenStreetMap, centred on the laptop. Detections at 80% confidence or more are pinned at the
laptop's position and saved in `data/potholes.db` for later (`--pin-confidence 0.9` to be stricter,
`GET /api/pins.geojson` to export). Detection runs on an Intel GPU through OpenVINO when one is
found (`--device cpu` to opt out).

Tests: `python -m unittest discover -s tests -t .`

## Real hardware

1. **Network.** Put the Pi and laptop on a dedicated Ethernet link or hotspot
   (`docs/architecture.md`, "Network"). Sync both clocks with NTP: severity matches IMU samples to
   video frames by timestamp.
2. **Pi.**
   ```bash
   sudo apt install motion python3-pip
   python3 -m ref.imu --diagnose            # probe the I2C bus first
   LAPTOP_HOST=<laptop ip> scripts/start_pi.sh
   chromium-browser --kiosk http://localhost:8080/
   ```
3. **Laptop.**
   ```bat
   set PI_HOST=<pi ip>
   scripts\start_laptop.bat --open
   ```
   With no `--fake` the laptop uses Windows location services (the first fix can take about 20 s,
   and accuracy is often tens of metres). Press `F` on the Dashboard for fullscreen and drag the
   window to the external screen.

## Honest limits

- Windows location is coarse, so map pins can sit tens of metres off. A pin marks the laptop's position,
  not the pothole's. Speed and heading are derived
  from successive fixes and are noisy.
- The frame timestamp is the laptop's arrival time (the `motion` stream carries none).
  `VIDEO_LATENCY_S` is uncalibrated, and severity thresholds are the reference defaults. Calibrate
  both on your hardware before reporting numbers.
- The Pi side (IMU on I2C, `motion`, the 7" screen) and the end-to-end run on a real network have
  not been verified yet; everything here was exercised with fakes and the sample video.
- The earlier bench-evaluation README is kept at `ref/README.md`.
