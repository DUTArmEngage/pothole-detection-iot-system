# Components

| Device | Component | Responsibility |
|---|---|---|
| Pi | IMU reader | Read the MPU-6050 over I2C and expose a short window of timestamped samples, latest included |
| Pi | Webcam stream | Serve the USB webcam feed to the laptop (`motion`) |
| Pi | GPS client | Poll the laptop for the current fix |
| Pi | Dashscreen | Show the map (GPS pin and trail) and IMU data on the 7" LCD |
| Laptop | GPS service | Provide the latest location over HTTP from the laptop's OS location services, deriving speed and heading from successive fixes |
| Laptop | Image processing | Run pothole detection on the video and output severity (informed by IMU impact) and confidence |
| Laptop | Pi client | Pull IMU and video from the Pi |
| Laptop | Dashboard | Show map, IMU, annotated video and HUD; the window moves to an external screen for presentations |
