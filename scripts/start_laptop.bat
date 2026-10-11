@echo off
rem Launch the laptop services (GPS endpoint + Dashboard) from the repo root.
rem   --fake       replay a fake route instead of the OS location source
rem   --fake-imu   simulated IMU, Pi not needed
rem   --pi URL     Pi base URL, e.g. --pi http://192.168.50.2:8001
cd /d "%~dp0.."
python -m main_server.main %*
