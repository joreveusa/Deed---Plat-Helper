@echo off
setlocal enabledelayedexpansion
title Deed ^& Plat Helper - DEV MODE
cd /d "%~dp0"

echo.
echo  ============================================================
echo    Deed ^& Plat Helper -- DEV MODE
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
    echo  [WARN] .env not found -- copy .env.example to .env and fill in keys.
)

:: ── Dev data dir ─────────────────────────────────────────────────────────────
set DEV_DATA_DIR=%~dp0dev-data\AI DATA CENTER\Survey Data
if exist "%DEV_DATA_DIR%" (
    echo  [OK] Survey Data: %DEV_DATA_DIR%
) else (
    echo  [info] dev-data folder not found -- drive features will be disabled.
)

:: ── Stripe CLI reminder ───────────────────────────────────────────────────────
echo.
echo  ───────────────────────────────────────────────────────────
echo   To enable Stripe webhooks locally, run IN A SEPARATE WINDOW:
echo.
echo     stripe listen --forward-to localhost:5000/api/stripe/webhook
echo.
echo   Then paste the whsec_... secret into .env as STRIPE_WEBHOOK_SECRET.
echo  ───────────────────────────────────────────────────────────
echo.

:: ── Find Python ──────────────────────────────────────────────────────────────
set PY=
if exist ".venv\Scripts\python.exe" (
    set PY=".venv\Scripts\python.exe"
    goto :py_ok
)
for %%U in (User Tina %USERNAME%) do (
    for %%V in (Python313 Python312 Python311 Python310) do (
        if exist "C:\Users\%%U\AppData\Local\Programs\Python\%%V\python.exe" (
            set PY="C:\Users\%%U\AppData\Local\Programs\Python\%%V\python.exe"
            goto :py_ok
        )
    )
)
where python >nul 2>&1
if %errorlevel%==0 ( set PY=python & goto :py_ok )
where py >nul 2>&1
if %errorlevel%==0 ( set PY=py    & goto :py_ok )
echo  [ERROR] Python not found!
pause
exit /b 1

:py_ok
echo  [OK] Python: %PY%
echo.
%PY% app.py
pause
