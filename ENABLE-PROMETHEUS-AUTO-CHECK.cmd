@echo off
set "ROOT=%~dp0"
echo Optional: install automatic diagnostics for this Windows account.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%Prometheus-Resources\Tools\install-portable-auto-check.ps1" -Root "%ROOT%" -Mode Install
pause
