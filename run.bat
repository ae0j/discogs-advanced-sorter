@echo off
setlocal
cd /d "%~dp0"

set "PY_CMD="

where py >nul 2>nul
if not errorlevel 1 (
  py -3.11 -c "import sys" >nul 2>nul && set "PY_CMD=py -3.11"
  if not defined PY_CMD py -3.10 -c "import sys" >nul 2>nul && set "PY_CMD=py -3.10"
  if not defined PY_CMD py -3.12 -c "import sys" >nul 2>nul && set "PY_CMD=py -3.12"
)

if not defined PY_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD (
  echo Python is not installed. Install Python 3.10-3.12 first:
  echo https://www.python.org/downloads/
  pause
  exit /b 1
)

for /f "delims=" %%i in ('%PY_CMD% -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set "PY_VERSION=%%i"

if /i not "%PY_VERSION%"=="3.10" if /i not "%PY_VERSION%"=="3.11" if /i not "%PY_VERSION%"=="3.12" (
  echo Unsupported Python version: %PY_VERSION%
  echo Please use Python 3.10, 3.11, or 3.12.
  pause
  exit /b 1
)

echo Using Python %PY_VERSION%

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

echo Starting app at http://127.0.0.1:5000
flask --app app run
goto :eof

:error
echo Setup failed.
pause
exit /b 1
