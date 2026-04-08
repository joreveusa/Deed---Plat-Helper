@echo off
setlocal enabledelayedexpansion
title Deed ^& Plat Helper - Night OCR Batch
cd /d "%~dp0"

:: ═══════════════════════════════════════════════════════════════════════════
::  NIGHT OCR BATCH - Scheduled to run after hours
::  Rebuilds cabinet index on J: drive, then OCRs all unscanned PDFs
:: ═══════════════════════════════════════════════════════════════════════════

set TIMESTAMP=%DATE:~10,4%%DATE:~4,2%%DATE:~7,2%_%TIME:~0,2%%TIME:~3,2%
set TIMESTAMP=%TIMESTAMP: =0%
set LOGFILE=logs\night_ocr_%TIMESTAMP%.log
if not exist "logs" mkdir logs

echo ===================================================================>> "%LOGFILE%"
echo   NIGHT OCR BATCH -- Started %DATE% %TIME%>> "%LOGFILE%"
echo ===================================================================>> "%LOGFILE%"
echo.
echo  ===================================================================
echo    NIGHT OCR BATCH -- Started %DATE% %TIME%
echo  ===================================================================
echo.

:: ── Find Python ──────────────────────────────────────────────────────────
set PY=
if exist ".venv\Scripts\python.exe" (
    set PY=".venv\Scripts\python.exe"
    goto :py_ok
)
echo [XX] Python venv not found!>> "%LOGFILE%"
echo [XX] Python venv not found!
goto :done
:py_ok
echo [OK] Python: %PY%>> "%LOGFILE%"

:: ── Detect J: drive ──────────────────────────────────────────────────────
set CABINET_PATH=
for %%L in (J K I H G F E D) do (
    if exist "%%L:\AI DATA CENTER\Survey Data\00 COUNTY CLERK SCANS Cabs A-B- C-D - E" (
        set CABINET_PATH=%%L:\AI DATA CENTER\Survey Data\00 COUNTY CLERK SCANS Cabs A-B- C-D - E
        echo [OK] Cabinet path: !CABINET_PATH!>> "%LOGFILE%"
        echo [OK] Cabinet path: !CABINET_PATH!
        goto :drive_found
    )
)
echo [XX] Cabinet folder not found on any drive!>> "%LOGFILE%"
echo [XX] Cabinet folder not found on any drive!
goto :done

:drive_found

:: ── Phase 1: Rebuild cabinet index ───────────────────────────────────────
echo.
echo  [1/2] Rebuilding cabinet index...
echo [%TIME%] Phase 1: Rebuilding cabinet index>> "%LOGFILE%"

%PY% scripts\batch_rebuild_cabinet_index.py "%CABINET_PATH%" >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [XX] Cabinet index rebuild FAILED>> "%LOGFILE%"
    echo   [XX] Cabinet index rebuild FAILED
) else (
    echo   [OK] Cabinet index rebuilt
)

:: ── Phase 2: OCR warmup (all PDFs, 4 workers) ───────────────────────────
echo.
echo  [2/2] Starting OCR warmup (all PDFs, 4 workers)...
echo  This will take several hours. Progress logged to: %LOGFILE%
echo [%TIME%] Phase 2: OCR warmup (9999 per cabinet, 4 workers)>> "%LOGFILE%"

%PY% scripts\batch_ocr_warmup.py "%CABINET_PATH%" 9999 16 >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [XX] OCR warmup encountered errors>> "%LOGFILE%"
    echo   [XX] OCR warmup encountered errors
) else (
    echo   [OK] OCR warmup complete
)

:done
echo.>> "%LOGFILE%"
echo ===================================================================>> "%LOGFILE%"
echo   NIGHT OCR BATCH COMPLETE -- %DATE% %TIME%>> "%LOGFILE%"
echo ===================================================================>> "%LOGFILE%"
echo.
echo  ===================================================================
echo    NIGHT OCR BATCH COMPLETE -- %DATE% %TIME%
echo    Log: %LOGFILE%
echo  ===================================================================
echo.
echo  This window will close in 30 seconds.
timeout /t 30 /nobreak >nul
exit /b 0
