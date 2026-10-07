@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 exit /b 1

set "prometheus_python=py -3"
if exist "%~dp0runtime\python\python.exe" set "prometheus_python=%~dp0runtime\python\python.exe"

if /i "%~1"=="chat" (
  set "prometheus_shutdown=%TEMP%\prometheus-chat-%RANDOM%-%RANDOM%.shutdown"
  if exist "%prometheus_shutdown%" del /q "%prometheus_shutdown%" >nul 2>&1
  %prometheus_python% -X utf8 "%~dp0prometheus.py" --shutdown-request "%prometheus_shutdown%" %*
  set "prometheus_exit=%errorlevel%"
  if exist "%prometheus_shutdown%" del /q "%prometheus_shutdown%" >nul 2>&1
) else (
  %prometheus_python% -X utf8 "%~dp0prometheus.py" %*
  set "prometheus_exit=%errorlevel%"
)

popd
exit /b %prometheus_exit%
