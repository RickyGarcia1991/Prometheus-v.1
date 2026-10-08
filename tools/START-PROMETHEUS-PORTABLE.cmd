@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PROMETHEUS_RESOURCE_ROOT=%ROOT%\Prometheus-Resources"
set "PROMETHEUS_PYTHON=%PROMETHEUS_RESOURCE_ROOT%\Python\python.exe"
set "OLLAMA_EXE=%PROMETHEUS_RESOURCE_ROOT%\Ollama\runtime\ollama.exe"
set "OLLAMA_MODELS=%PROMETHEUS_RESOURCE_ROOT%\Ollama\.ollama\models"
set "OLLAMA_HOST=127.0.0.1:11434"
set "OLLAMA_NO_CLOUD=true"
set "PORTABLE_MEMORY=%ROOT%\Prometheus-Data\memory.sqlite3"
set "PACKAGE=%ROOT%\Prometheus-v0.8.0-dev1-8c17eab"
if not exist "%PACKAGE%\prometheus.py" (echo ERROR: Core package missing.& exit /b 1)
if not exist "%PROMETHEUS_PYTHON%" (echo ERROR: Portable Python missing.& exit /b 1)
if not exist "%OLLAMA_EXE%" (echo ERROR: Portable Ollama missing.& exit /b 1)
if not exist "%OLLAMA_MODELS%" (echo ERROR: Models missing.& exit /b 1)
if not exist "%PORTABLE_MEMORY%" (echo ERROR: Portable memory missing; refusing to create empty memory.& exit /b 1)
if /I "%~1"=="doctor" goto run
if /I "%~1"=="sessions" goto run
if /I "%~1"=="history" goto run
if /I "%~1"=="backup-memory" goto run
if /I "%~1"=="restore-memory" goto run
if /I "%~1"=="inspect-host" goto run
if /I "%~1"=="resources" goto run
if /I "%~1"=="sources" goto run
if /I "%~1"=="search" goto run
if /I "%~1"=="research-status" goto run
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PACKAGE%\tools\wait-ollama.ps1"
if errorlevel 1 exit /b 1
:run
call "%PACKAGE%\START_PROMETHEUS.cmd" --memory "%PORTABLE_MEMORY%" %*
exit /b %errorlevel%

