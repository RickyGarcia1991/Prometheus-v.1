@echo off
setlocal
set "ROOT=%~dp0"
echo Prometheus migration readiness check. No installation or changes to memory.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%Prometheus-Resources\Tools\portable-migration-diagnostic.ps1" -Root "%ROOT%"
echo.
echo Diagnostic JSON reports are saved under Prometheus-Recovery\Migration-Diagnostics.
pause
