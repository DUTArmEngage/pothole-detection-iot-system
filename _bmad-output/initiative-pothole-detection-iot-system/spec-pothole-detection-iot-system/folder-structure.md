# Folder structure

Organised by deployment target, so each folder maps to one device.

```
project/
├── README.md
├── docs/
│   ├── architecture.md          # diagram + data flows
│   └── api.md                   # endpoint contracts
├── shared/
│   ├── schemas.py               # GPS / IMU / detection payloads
│   └── config.py                # hosts, ports, poll intervals
├── rpi/
│   ├── motion.conf
│   ├── requirements.txt
│   ├── server/
│   │   ├── main.py              # Pi HTTP server
│   │   └── imu.py
│   ├── clients/
│   │   └── gps_client.py        # polls laptop for GPS
│   └── dashscreen/              # 7" LCD UI
│       ├── main.py
│       └── server.py
├── main_server/
│   ├── requirements.txt
│   ├── main.py                  # laptop HTTP server
│   ├── gps.py                   # serves /gps/current
│   ├── image_processing.py
│   ├── clients/
│   │   └── pi_client.py         # pulls IMU + video from Pi
│   └── dashboard/               # presentation UI
│       ├── main.py
│       └── server.py
├── scripts/
│   ├── start_pi.sh
│   └── start_laptop.bat
└── tests/
    ├── fake_gps.py              # replay a recorded route
    └── fake_imu.py              # simulated IMU samples
```

| Change from the brief's current layout | Reason |
|---|---|
| `client/` split into `rpi/clients/` and `main_server/clients/` | Each client runs on exactly one device |
| `ui/dashscreen` moves under `rpi/`, `ui/dashboard` under `main_server/` | The UI runs where it is deployed |
| New `shared/` | One source of truth for payloads and config |
| New `tests/` with fakes | Build and demo each side without the other |
