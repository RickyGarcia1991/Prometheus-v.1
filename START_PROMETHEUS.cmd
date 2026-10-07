@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 exit /b 1

set "prometheus_runner=py -3"
if exist "%~dp0runtime\python\python.exe" set prometheus_runner="%~dp0runtime\python\python.exe"
if defined PROMETHEUS_PYTHON (
  if not exist "!PROMETHEUS_PYTHON!" goto missing_runtime
  set prometheus_runner="!PROMETHEUS_PYTHON!"
)

rem Prefer an explicitly configured Prometheus resource drive when present.
if not defined PROMETHEUS_RESOURCE_ROOT for /f "tokens=2,*" %%A in ('reg query HKCU\Environment /v PROMETHEUS_RESOURCE_ROOT 2^>nul ^| find "PROMETHEUS_RESOURCE_ROOT"') do set "PROMETHEUS_RESOURCE_ROOT=%%B"
if defined PROMETHEUS_RESOURCE_ROOT if exist "!PROMETHEUS_RESOURCE_ROOT!\Ollama\.ollama\models" set "OLLAMA_MODELS=!PROMETHEUS_RESOURCE_ROOT!\Ollama\.ollama\models"

set "prometheus_chat="
for %%A in (%*) do (
  if /i "%%~A"=="chat" set "prometheus_chat=1"
)

if defined prometheus_chat (
  if not exist "%LOCALAPPDATA%\Prometheus" mkdir "%LOCALAPPDATA%\Prometheus" >nul 2>&1
  set "prometheus_shutdown=%LOCALAPPDATA%\Prometheus\active-chat.shutdown"
  if exist "!prometheus_shutdown!" del /q "!prometheus_shutdown!" >nul 2>&1
  %prometheus_runner% -X utf8 "%~dp0prometheus.py" --shutdown-request "!prometheus_shutdown!" %*
  set "prometheus_exit=!errorlevel!"
  if exist "!prometheus_shutdown!" del /q "!prometheus_shutdown!" >nul 2>&1
) else (
  %prometheus_runner% -X utf8 "%~dp0prometheus.py" %*
  set "prometheus_exit=!errorlevel!"
)

popd
exit /b %prometheus_exit%

:missing_runtime
echo ERROR: Configured Python runtime missing.
popd
exit /b 1
