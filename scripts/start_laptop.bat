@echo off
rem Launch the laptop services from the repo root. Add --fake to replay a route.
cd /d "%~dp0.."
python -m main_server.main %*
