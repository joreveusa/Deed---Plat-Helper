@echo off
setlocal enabledelayedexpansion
title Deed ^& Plat Helper - Launcher
cd /d "%~dp0"

echo.
echo  ==========================================
echo    Deed ^& Plat Helper
echo  ==========================================
echo.

:: ── Find Python ──────────────────────────────────────────────────────────
:: Try the venv first, then well-known install paths, then PATH
set PY=
if exist ".venv\Scripts\python.exe" (
    set PY=".venv\Scripts\python.exe"
    goto :py_ok
)
:: Common install locations (User, Tina, generic)
for %%U in (User Tina %USERNAME%) do (
    for %%V in (Python313 Python312 Python311 Python310) do (
        if exist "C:\Users\%%U\AppData\Local\Programs\Python\%%V\python.exe" (
            set PY="C:\Users\%%U\AppData\Local\Programs\Python\%%V\python.exe"
            goto :py_ok
        )
    )
)
:: Fallback: check PATH
where python >nul 2>&1
if %errorlevel%==0 (
    set PY=python
    goto :py_ok
)
where py >nul 2>&1
if %errorlevel%==0 (
    set PY=py
    goto :py_ok
)
echo  [ERROR] Python not found!
echo          Checked: .venv, common install paths, and PATH
echo          Please install Python 3.10+ from python.org
pause
exit /b 1

:py_ok
echo  [OK] Using Python: %PY%

:: ── Ensure virtual environment exists ─────────────────────────────────────
if not exist ".venv\Scripts\python.exe" (
    echo  Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo  [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
    echo  [OK] Virtual environment created
)

:: Activate the venv for this session
set PY=".venv\Scripts\python.exe"

:: ── Install dependencies if needed ────────────────────────────────────────
%PY% -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo  Installing dependencies from requirements.txt...
    .venv\Scripts\pip.exe install -r requirements.txt
    if errorlevel 1 (
        echo  [ERROR] Dependency installation failed
        pause
        exit /b 1
    )
    echo  [OK] Dependencies installed
)

:: ── Check if server is already running on port 5000 ───────────────────────
netstat -ano 2>nul | findstr /C:":5000" >nul
if %errorlevel%==0 (
    echo  [OK] Server already running - opening browser...
    timeout /t 1 /nobreak >nul
    start "" "http://localhost:5000"
    goto :done
)

:: ── Start Flask server in its OWN window ──────────────────────────────────
echo  Starting Flask server...
start "Deed & Plat Helper - Server" /d "%~dp0" %PY% app.py

:: Poll until port 5000 is open (up to 20 seconds)
echo  Waiting for server to be ready...
set /a tries=0

:waitloop
timeout /t 1 /nobreak >nul
set /a tries+=1
netstat -ano 2>nul | findstr /C:":5000" >nul
if %errorlevel%==0 goto :ready
if !tries! lss 20 goto :waitloop
echo  [WARN] Server taking longer than expected...

:ready
echo  [OK] Server ready ^(took !tries! sec^). Opening browser...
timeout /t 1 /nobreak >nul
start "" "http://localhost:5000"

:done
echo.
echo  ===========================================
echo   App is open at: http://localhost:5000
echo   The server runs in its own window.
echo   Close the "Server" window to stop the app.
echo  ===========================================
echo.
timeout /t 3 /nobreak >nul
exit
