@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PY_CMD="
set "PY_VERSION="
set "DETECTED_PY="

where py >nul 2>nul
if not errorlevel 1 (
  for %%V in (3.13 3.12 3.11 3.10) do (
    py -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 if not defined PY_CMD (
      set "PY_CMD=py -%%V"
      set "PY_VERSION=%%V"
    )
  )
  for /f "delims=" %%i in ('py -0p 2^>nul') do (
    if not defined DETECTED_PY set "DETECTED_PY=%%i"
  )
)

if not defined PY_CMD (
  where python >nul 2>nul
  if not errorlevel 1 (
    for /f "delims=" %%i in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2^>nul') do set "PY_VERSION=%%i"
    if "!PY_VERSION!"=="3.10" set "PY_CMD=python"
    if "!PY_VERSION!"=="3.11" set "PY_CMD=python"
    if "!PY_VERSION!"=="3.12" set "PY_CMD=python"
    if "!PY_VERSION!"=="3.13" set "PY_CMD=python"
  )
)

if not defined PY_CMD (
  echo No supported Python interpreter found.
  if defined PY_VERSION echo Detected unsupported Python version: !PY_VERSION!
  if defined DETECTED_PY echo Installed interpreters: !DETECTED_PY!
  echo Please install Python 3.10-3.13 from:
  echo https://www.python.org/downloads/
  echo.
  echo If Windows shows Microsoft Store alias messages for python.exe:
  echo Settings ^> Apps ^> Advanced app settings ^> App execution aliases
  echo Disable aliases for python.exe and python3.exe
  pause
  exit /b 1
)

echo Using Python !PY_VERSION!

if not exist ".venv\Scripts\python.exe" (
  %PY_CMD% -m venv .venv
  if errorlevel 1 goto :error
)

call .venv\Scripts\activate.bat
if errorlevel 1 goto :error

python -m pip install --upgrade pip
if errorlevel 1 goto :error

python -m pip install -r requirements.txt
if errorlevel 1 goto :error

if not defined APP_HOST set "APP_HOST=127.0.0.1"
if not defined APP_PORT set "APP_PORT=5080"

echo Starting app at http://%APP_HOST%:%APP_PORT%
flask --app app run --host %APP_HOST% --port %APP_PORT%
goto :eof

:error
echo Setup failed.
pause
exit /b 1
