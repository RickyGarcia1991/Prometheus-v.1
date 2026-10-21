@echo off
setlocal
powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0Prometheus-Resources\Tools\Repair-Prometheus-Controller.ps1" -Root "%~dp0." -Launch
exit /b %errorlevel%
