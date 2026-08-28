@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo  Photo and Signature Extractor - local test
echo ============================================================
echo.

rem --- Find a real Python 3 install. We do NOT trust the "py" or "python"
rem --- commands by name first, because on some PCs another program (MGLTools,
rem --- ArcGIS, an old IDE, etc.) installs its own "py.exe"/"python.exe" that
rem --- sits earlier on PATH and answers to those names instead of the real
rem --- python.org install - even showing a fake/ancient version number.
rem --- Instead we look directly in the folders the official python.org
rem --- installer uses, and only fall back to the "py"/"python" commands if
rem --- that search finds nothing.
set "PYEXE="

for %%G in (
  "%LocalAppData%\Programs\Python\Python3*\python.exe"
  "%ProgramFiles%\Python3*\python.exe"
  "%ProgramFiles(x86)%\Python3*\python.exe"
) do (
  for %%P in (%%~G) do (
    if not defined PYEXE (
      "%%~P" -c "import venv" >nul 2>nul
      if not errorlevel 1 set "PYEXE=%%~P"
    )
  )
)

if defined PYEXE (
    set "PYCMD="%PYEXE%""
    goto :found_python
)

py -3 -c "import venv" >nul 2>nul
if not errorlevel 1 (
    set PYCMD=py -3
    goto :found_python
)

python -c "import venv" >nul 2>nul
if not errorlevel 1 (
    set PYCMD=python
    goto :found_python
)

echo A working Python 3 installation was not found on this computer.
echo.
echo If Python is showing an error above about "No module named venv" or
echo similar, or if "py --version" / "python --version" print a very old
echo version like 3.2, that means the "python"/"py" commands on this PC
echo currently point to a DIFFERENT program's bundled Python (for example
echo MGLTools), not your real Python 3 install - this is a common mix-up
echo and not a problem with this tool. This script already searched the
echo normal python.org install folders and didn't find a usable Python
echo there either.
echo.
echo To fix it, pick ONE of these:
echo   A. If you haven't installed Python yet: get it from
echo      https://www.python.org/downloads/ and on the first install
echo      screen, check "Add python.exe to PATH" before clicking Install.
echo   B. If you already installed Python: open a NEW Command Prompt
echo      window and run:  where python
echo      Send that output (and this window) so the exact folder it's
echo      really installed in can be added directly.
echo.
pause
exit /b 1

:found_python
echo Using: %PYCMD%
echo.

if not exist venv\Scripts\activate.bat (
    if exist venv (
        echo Removing an incomplete previous setup attempt...
        rmdir /s /q venv
    )
    echo First-time setup - this may take a minute or two...
    %PYCMD% -m venv venv
    if errorlevel 1 (
        echo.
        echo Could not create the local environment. Scroll up to see the error.
        pause
        exit /b 1
    )
)

call venv\Scripts\activate.bat

echo Installing/checking required packages...
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo Something went wrong installing packages. Scroll up to see the error.
    pause
    exit /b 1
)

set SECRET_KEY=local-test-secret-key
set ALLOWED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
set DEFAULT_ADMIN_USERNAME=admin
set DEFAULT_ADMIN_PASSWORD=changeme123

echo.
echo Starting the server in a new window - leave that window open while testing.
start "Photo Signature Extractor - server (leave this open)" cmd /k "call venv\Scripts\activate.bat && set SECRET_KEY=%SECRET_KEY% && set ALLOWED_ORIGINS=%ALLOWED_ORIGINS% && set DEFAULT_ADMIN_USERNAME=%DEFAULT_ADMIN_USERNAME% && set DEFAULT_ADMIN_PASSWORD=%DEFAULT_ADMIN_PASSWORD% && uvicorn app.main:app --port 8000"

echo Waiting for it to start...
timeout /t 5 /nobreak >nul

echo Opening the tool in your browser...
start "" http://127.0.0.1:8000/

echo.
echo Done. The tool is open in your browser at http://127.0.0.1:8000/
echo The admin dashboard is at http://127.0.0.1:8000/admin.html
echo   (login: admin / changeme123 - this is local-only, not your live site)
echo.
echo To stop the server, close the other window that says
echo "Photo Signature Extractor - server".
echo.
pause
