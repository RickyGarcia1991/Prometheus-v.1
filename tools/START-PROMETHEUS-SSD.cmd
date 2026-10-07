@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PROMETHEUS_RESOURCE_ROOT=%ROOT%\Prometheus-Resources"
set "OLLAMA_MODELS=%PROMETHEUS_RESOURCE_ROOT%\Ollama\.ollama\models"
set "OLLAMA_NO_CLOUD=true"
set "OLLAMA_HOST=127.0.0.1:11434"
set "OLLAMA_EXE=%PROMETHEUS_RESOURCE_ROOT%\Ollama\runtime\ollama.exe"
set "PROMETHEUS_PYTHON=%PROMETHEUS_RESOURCE_ROOT%\Python\python.exe"
set "PACKAGE=%ROOT%\Prometheus-v0.5.2-dev-5b9a252"

if not exist "%PACKAGE%\prometheus.py" (echo ERROR: Prometheus package missing.& exit /b 1)
if not exist "%PROMETHEUS_PYTHON%" (echo ERROR: Portable SSD Python runtime missing.& exit /b 1)
if not exist "%OLLAMA_EXE%" (echo ERROR: SSD Ollama runtime missing.& exit /b 1)
if not exist "%OLLAMA_MODELS%" (echo ERROR: SSD model library missing.& exit /b 1)

if /I "%~1"=="resources" goto run_prometheus
if /I "%~1"=="sources" goto run_prometheus
if /I "%~1"=="search" goto run_prometheus
if /I "%~1"=="sessions" goto run_prometheus
if /I "%~1"=="history" goto run_prometheus
if /I "%~1"=="backup-memory" goto run_prometheus
if /I "%~1"=="restore-memory" goto run_prometheus

if not defined OLLAMA_VULKAN set "OLLAMA_VULKAN=false"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PACKAGE%\tools\wait-ollama.ps1"
if errorlevel 1 (echo ERROR: Local Ollama readiness failed. See Prometheus logs.& exit /b 1)

:run_prometheus
echo Prometheus SSD bootstrap ready.
echo Prometheus version: development 5b9a252
echo Source checkpoint: 5b9a25228800c800314ff03ad3ed333a7b32c54c
call "%PACKAGE%\START_PROMETHEUS.cmd" %*
exit /b %errorlevel%
