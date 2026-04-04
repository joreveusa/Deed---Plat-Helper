@echo off
setlocal enabledelayedexpansion
title Deed ^& Plat Helper - PRODUCTION
cd /d "%~dp0"

echo.
echo  ============================================================
echo    Deed ^& Plat Helper -- PRODUCTION SERVER
echo    Powered by Waitress (multi-threaded WSGI)
echo  ============================================================
echo.

:: ── Load .env ────────────────────────────────────────────────────────────────
if exist ".env" (
    echo  [OK] Loading .env ...
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        set _key=%%A
        if not "!_key!"=="" if not "!_key:~0,1!"=="#" (
            set "%%A=%%B"
        )
    )
) else (
    echo  [WARN] .env not found -- copy .env.example to .env and fill in your keys.
    echo.
)

:: ── Find Python ──────────────────────────────────────────────────────────────
set PY=
if exist ".venv\Scripts\python.exe" ( set PY=".venv\Scripts\python.exe" & goto :py_ok )
for %%U in (User Tina %USERNAME%) do (
    for %%V in (Python313 Python312 Python311 Python310) do (
        if exist "C:\Users\%%U\AppData\Local\Programs\Python\%%V\python.exe" (
            set PY="C:\Users\%%U\AppData\Local\Programs\Python\%%V\python.exe"
            goto :py_ok
        )
    )
)
where python >nul 2>&1 && set PY=python && goto :py_ok
where py     >nul 2>&1 && set PY=py    && goto :py_ok
echo  [ERROR] Python not found!
pause & exit /b 1

:py_ok
echo  [OK] Python: %PY%

:: ── Install waitress if missing ───────────────────────────────────────────────
%PY% -c "import waitress" >nul 2>&1
if %errorlevel% neq 0 (
    echo  [INFO] Installing waitress...
    %PY% -m pip install waitress --quiet
)

:: ── Port config ───────────────────────────────────────────────────────────────
if "%DEED_PORT%"=="" set DEED_PORT=5000
if "%DEED_WORKERS%"=="" set DEED_WORKERS=8

echo.
echo  [OK] Starting on http://0.0.0.0:%DEED_PORT%
echo  [OK] Threads: %DEED_WORKERS%
echo.
echo  Press Ctrl+C to stop the server.
echo.

:: ── Launch via waitress ───────────────────────────────────────────────────────
%PY% -c "from waitress import serve; from app import app; serve(app, host='0.0.0.0', port=%DEED_PORT%, threads=%DEED_WORKERS%)"
pause
