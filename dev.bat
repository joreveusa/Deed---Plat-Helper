@echo off
REM ── Deed & Plat Helper — DEV MODE LAUNCHER ──────────────────────────────────
REM Mounts the local dev-data folder as the Survey Data drive.
REM Use this when the office network drive is not available.

set DEV_DATA_DIR=%~dp0dev-data\AI DATA CENTER\Survey Data
echo.
echo  ============================================================
echo   Deed ^& Plat Helper — DEV MODE
echo   Survey Data: %DEV_DATA_DIR%
echo  ============================================================
echo.

python app.py
pause
