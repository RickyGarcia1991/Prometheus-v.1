@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 exit /b 1
if exist "%~dp0runtime\python\python.exe" (
  "%~dp0runtime\python\python.exe" -X utf8 "%~dp0prometheus.py" %*
) else (
  py -3 -X utf8 "%~dp0prometheus.py" %*
)
set "prometheus_exit=%errorlevel%"
popd
exit /b %prometheus_exit%
