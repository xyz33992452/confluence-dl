@echo off
REM Stop only confluence-dl Web server processes launched from this project.
setlocal
set "ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%stop-web.ps1" -ProjectRoot "%ROOT%"
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)
endlocal
