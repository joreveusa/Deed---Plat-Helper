@echo off
setlocal enabledelayedexpansion
title Deed ^& Plat Helper - Launcher
cd /d "E:\AI DATA CENTER\AI Agents\Deed & Plat Helper"

echo.
echo  ==========================================
echo    Deed ^& Plat Helper
echo  ==========================================
echo.

:: Full path to Python launcher (works even if py is not on PATH)
set PY="C:\Users\Tina\AppData\Local\Programs\Python\Launcher\py.exe"

:: Verify Python exists
if not exist %PY% (
    echo  [ERROR] Python launcher not found at:
    echo          %PY%
    echo.
    echo  Please update the PY= line in this batch file.
    pause
    exit /b 1
)

:: Check if server is already running on port 5000
netstat -ano 2>nul | findstr /C:":5000" >nul
if %errorlevel%==0 (
    echo  [OK] Server already running - opening browser...
    timeout /t 1 /nobreak >nul
    start "" "http://localhost:5000"
    goto :done
)

:: Start Flask server in its OWN window (persists after this window closes)
echo  Starting Flask server...
start "Deed & Plat Helper - Server" /d "E:\AI DATA CENTER\AI Agents\Deed & Plat Helper" %PY% app.py

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
