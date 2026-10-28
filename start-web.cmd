@echo off
REM confluence-dl Web UI launcher (Windows).
REM
REM Steps:
REM   * kill any stale instance from a previous run
REM   * build the Rust CLI in release mode if needed
REM   * boot the aiohttp web server (PID file under .runtime\web.pid)
REM   * open the UI in the default browser

setlocal ENABLEDELAYEDEXPANSION

set "ROOT=%~dp0"
cd /d "%ROOT%"

if "%CONFLUENCE_WEB_HOST%"=="" set "CONFLUENCE_WEB_HOST=127.0.0.1"
if "%CONFLUENCE_WEB_PORT%"=="" set "CONFLUENCE_WEB_PORT=18770"

set "RUNTIME=%ROOT%.runtime"
set "PID_FILE=%RUNTIME%\web.pid"
set "LOG_FILE=%RUNTIME%\web.log"
if not exist "%RUNTIME%" mkdir "%RUNTIME%"

echo.
echo === confluence-dl Web UI launcher ===

REM Refuse a port conflict rather than stopping an unrelated application.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%CONFLUENCE_WEB_PORT% .*LISTENING"') do (
  echo Port %CONFLUENCE_WEB_PORT% is already in use by PID %%P.
  echo Stop that application or set CONFLUENCE_WEB_PORT to another port.
  pause
  exit /b 1
)

REM --- Build Rust binary (incremental; refreshes stale binaries) -----------

where cargo >NUL 2>&1
if errorlevel 1 (
  echo cargo not found. Install Rust first: https://rustup.rs/
  pause
  exit /b 1
)
echo Checking/building confluence-dl...
set RUSTUP_TOOLCHAIN=stable
cargo +stable --config "source.crates-io.replace-with=\"rsproxy\"" --config "source.rsproxy.registry=\"sparse+https://rsproxy.cn/index/\"" build --release
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
REM --- Ensure Python venv + aiohttp ---------------------------------------

if exist "%ROOT%.venv-web\Scripts\python.exe" goto SKIP_VENV
where uv >NUL 2>&1
if errorlevel 1 (
  echo uv not found. Install uv first: https://docs.astral.sh/uv/
  pause
  exit /b 1
)
echo Creating Python venv and installing aiohttp...
uv venv "%ROOT%.venv-web" --python 3.11
uv pip install --python "%ROOT%.venv-web\Scripts\python.exe" aiohttp
if errorlevel 1 (
  echo Failed to install Python dependencies.
  pause
  exit /b 1
)
:SKIP_VENV

REM --- Launch -------------------------------------------------------------

echo.
echo Starting Web server at http://%CONFLUENCE_WEB_HOST%:%CONFLUENCE_WEB_PORT%/
start "" /B "%ROOT%.venv-web\Scripts\pythonw.exe" "%ROOT%web_server.py" --host %CONFLUENCE_WEB_HOST% --port %CONFLUENCE_WEB_PORT% >"%LOG_FILE%" 2>&1
REM pythonw doesn't echo its PID; fall back to python.exe for visibility.
if errorlevel 1 (
  start "" /B "%ROOT%.venv-web\Scripts\python.exe" "%ROOT%web_server.py" --host %CONFLUENCE_WEB_HOST% --port %CONFLUENCE_WEB_PORT% >"%LOG_FILE%" 2>&1
)

REM Wait for readiness by polling the HTTP port.
set /a TRIES=0
:WAIT_LOOP
set /a TRIES+=1
powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -Uri 'http://%CONFLUENCE_WEB_HOST%:%CONFLUENCE_WEB_PORT%/' -TimeoutSec 1) | Out-Null; exit 0 } catch { exit 1 }" >NUL 2>&1
if not errorlevel 1 goto WAIT_DONE
if %TRIES% GEQ 40 (
  echo Server did not become ready; check %LOG_FILE%.
  pause
  exit /b 1
)
timeout /T 1 /NOBREAK >NUL
goto WAIT_LOOP
:WAIT_DONE

start "" "http://%CONFLUENCE_WEB_HOST%:%CONFLUENCE_WEB_PORT%/"

echo.
echo Web UI is running in the background.
echo To stop it, double-click stop-web.cmd in this project folder.
echo Log file:    %LOG_FILE%
echo.
pause
endlocal
exit /b 0
