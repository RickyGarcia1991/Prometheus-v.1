@echo off
setlocal
chcp 65001 >nul
set "prometheus_shutdown=%LOCALAPPDATA%\Prometheus\active-chat.shutdown"
if not exist "%LOCALAPPDATA%\Prometheus" mkdir "%LOCALAPPDATA%\Prometheus" >nul 2>&1

> "%prometheus_shutdown%" echo shutdown
echo Shutdown requested. Waiting for Prometheus to close cleanly...

for /l %%N in (1,1,100) do (
  if not exist "%prometheus_shutdown%" goto :ready
  ping 127.0.0.1 -n 2 >nul
)

echo ERROR: Prometheus did not acknowledge shutdown within the grace period.
echo Do not eject an external Prometheus drive yet.
exit /b 1

:ready
echo Prometheus stopped cleanly.
echo External Prometheus drive can now be prepared for safe eject.
exit /b 0
