# Delivery sequence

| Phase | Deliverable | Done when | Capabilities |
|---|---|---|---|
| 1 | Restructure folders and add `shared/schemas.py` | Both devices import the same payload definitions | Constraints |
| 2 | Laptop `/gps/current` endpoint (fake route first) | `curl` from the Pi returns valid JSON | CAP-3, CAP-8 |
| 3 | Pi `gps_client.py` feeds the Dashscreen map | The pin moves on the 7" screen | CAP-4, CAP-5 |
| 4 | Pi `/imu/latest` and the laptop `pi_client.py` | The dashboard shows live IMU | CAP-1, CAP-8 |
| 5 | Webcam stream into detection and the Dashboard | Annotated video plays on the laptop | CAP-2, CAP-6, CAP-7 |
| 6 | Laptop's own location source replaces the fake route | Pins match real positions | CAP-3 |
| 7 | Fix the Dashboard layout, tune detection overlap (de-duplicate overlapping boxes), move to a dedicated network | A full run works end to end | CAP-7, Success signal |
