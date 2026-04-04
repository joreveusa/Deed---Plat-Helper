@echo off
setlocal enabledelayedexpansion
title Deed ^& Plat Helper - Weekend Batch Maintenance
cd /d "%~dp0"

:: ═══════════════════════════════════════════════════════════════════════════
::  WEEKEND BATCH MAINTENANCE SCRIPT
::  ─────────────────────────────────
::  Designed to run unattended over the weekend. Performs:
::    1. Health checks (Python, venv, dependencies, drive detection)
::    2. KML/KMZ parcel index rebuild with ArcGIS enrichment
::    3. Cabinet index rebuild (re-scan all cabinet folders)
::    4. Research analytics refresh (scan all completed jobs)
::    5. OCR cache warm-up (pre-OCR unprocessed cabinet plats)
::    6. Log file rotation and cleanup
::    7. Test suite verification
::    8. Git checkpoint (auto-commit current state)
::    9. HTML summary report generation
:: ═══════════════════════════════════════════════════════════════════════════

set TIMESTAMP=%DATE:~10,4%%DATE:~4,2%%DATE:~7,2%_%TIME:~0,2%%TIME:~3,2%
set TIMESTAMP=%TIMESTAMP: =0%
set LOGFILE=logs\weekend_batch_%TIMESTAMP%.log
set ERRCOUNT=0
set WARNCOUNT=0

:: Create logs directory
if not exist "logs" mkdir logs

:: ── Logging helpers ──────────────────────────────────────────────────────
echo ===================================================================>> "%LOGFILE%"
echo   WEEKEND BATCH MAINTENANCE -- Started %DATE% %TIME%>> "%LOGFILE%"
echo ===================================================================>> "%LOGFILE%"
echo.>> "%LOGFILE%"
echo.
echo  ===================================================================
echo    WEEKEND BATCH MAINTENANCE -- Started %DATE% %TIME%
echo  ===================================================================
echo.

:: ══════════════════════════════════════════════════════════════════════════
:: PHASE 1: ENVIRONMENT HEALTH CHECKS
:: ══════════════════════════════════════════════════════════════════════════
call :header "PHASE 1: Environment Health Checks"

:: ── Find Python ──────────────────────────────────────────────────────────
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
if %errorlevel%==0 (
    set PY=python
    goto :py_ok
)
call :error "Python not found! Cannot continue."
goto :final_report

:py_ok
call :ok "Python found: %PY%"

:: ── Check virtual environment ────────────────────────────────────────────
if not exist ".venv\Scripts\python.exe" (
    call :warn "Virtual environment missing -- creating..."
    %PY% -m venv .venv
    if errorlevel 1 (
        call :error "Failed to create virtual environment"
        goto :final_report
    )
    call :ok "Virtual environment created"
)
set PY=".venv\Scripts\python.exe"

:: ── Check dependencies ───────────────────────────────────────────────────
call :info "Checking Python dependencies..."
%PY% -c "import flask, requests, bs4, fitz, pytesseract, PIL, ezdxf" >nul 2>&1
if errorlevel 1 (
    call :warn "Missing dependencies -- installing..."
    .venv\Scripts\pip.exe install -r requirements.txt --quiet
    if errorlevel 1 (
        call :error "Dependency installation failed"
    ) else (
        call :ok "Dependencies installed successfully"
    )
) else (
    call :ok "All core dependencies present"
)

:: ── Detect survey data drive ─────────────────────────────────────────────
call :info "Detecting survey data drive..."
set SURVEY_DRIVE=
for %%L in (E F G H I J K D) do (
    if exist "%%L:\AI DATA CENTER\Survey Data" (
        set SURVEY_DRIVE=%%L
        goto :drive_found
    )
)
call :error "Survey Data drive not found on any drive letter!"
call :warn "Skipping index rebuilds (drive unavailable)"
goto :phase6

:drive_found
call :ok "Survey Data found on %SURVEY_DRIVE%:\"
set SURVEY_PATH=%SURVEY_DRIVE%:\AI DATA CENTER\Survey Data
set CABINET_PATH=%SURVEY_PATH%\00 COUNTY CLERK SCANS Cabs A-B- C-D - E
set XML_PATH=%SURVEY_PATH%\XML

:: Verify subdirectories
if not exist "%CABINET_PATH%" (
    call :warn "Cabinet scans folder not found at: %CABINET_PATH%"
)
if not exist "%XML_PATH%" (
    call :warn "XML/KML folder not found at: %XML_PATH%"
)

:: ══════════════════════════════════════════════════════════════════════════
:: PHASE 2: KML/KMZ PARCEL INDEX REBUILD
:: ══════════════════════════════════════════════════════════════════════════
call :header "PHASE 2: KML/KMZ Parcel Index Rebuild"

if not exist "%XML_PATH%" (
    call :warn "Skipping -- XML folder not found"
    goto :phase3
)

call :info "Rebuilding parcel index with ArcGIS enrichment..."
call :info "This may take 5-15 minutes depending on network speed..."

%PY% scripts\batch_rebuild_parcel_index.py "%SURVEY_PATH%" >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    call :error "Parcel index rebuild failed -- check log for details"
) else (
    call :ok "Parcel index rebuilt successfully"
)

:: ══════════════════════════════════════════════════════════════════════════
:: PHASE 3: CABINET INDEX REBUILD
:: ══════════════════════════════════════════════════════════════════════════
:phase3
call :header "PHASE 3: Cabinet Index Rebuild"

if not exist "%CABINET_PATH%" (
    call :warn "Skipping -- Cabinet folder not found"
    goto :phase4
)

call :info "Scanning all cabinet folders and rebuilding index..."

%PY% scripts\batch_rebuild_cabinet_index.py "%CABINET_PATH%" >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    call :error "Cabinet index rebuild failed"
) else (
    call :ok "Cabinet index rebuilt successfully"
)

:: ══════════════════════════════════════════════════════════════════════════
:: PHASE 4: RESEARCH ANALYTICS REFRESH
:: ══════════════════════════════════════════════════════════════════════════
:phase4
call :header "PHASE 4: Research Analytics Refresh"

call :info "Scanning all job folders for research sessions..."

%PY% scripts\batch_refresh_analytics.py "%SURVEY_PATH%" >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    call :error "Research analytics refresh failed"
) else (
    call :ok "Research analytics refreshed"
)

:: ══════════════════════════════════════════════════════════════════════════
:: PHASE 5: OCR CACHE WARM-UP
:: ══════════════════════════════════════════════════════════════════════════
call :header "PHASE 5: OCR Cache Warm-up"

if not exist "%CABINET_PATH%" (
    call :warn "Skipping -- Cabinet folder not found"
    goto :phase6
)

call :info "Pre-processing unscanned cabinet PDFs (up to 50 per cabinet)..."
call :info "This can take a while -- each PDF requires OCR..."

%PY% scripts\batch_ocr_warmup.py "%CABINET_PATH%" 50 >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    call :error "OCR cache warm-up encountered errors"
) else (
    call :ok "OCR cache warm-up complete"
)

:: ══════════════════════════════════════════════════════════════════════════
:: PHASE 6: LOG ROTATION & CLEANUP
:: ══════════════════════════════════════════════════════════════════════════
:phase6
call :header "PHASE 6: Log Rotation and Cleanup"

:: ── Rotate server logs ───────────────────────────────────────────────────
if exist "server.log" (
    set SLOG_SIZE=0
    for %%A in (server.log) do set SLOG_SIZE=%%~zA
    if !SLOG_SIZE! GTR 1048576 (
        call :info "Rotating server.log (!SLOG_SIZE! bytes)..."
        move /y "server.log" "logs\server_%TIMESTAMP%.log" >nul 2>&1
        type nul > "server.log"
        call :ok "server.log rotated"
    ) else (
        call :info "server.log is small (!SLOG_SIZE! bytes) -- no rotation needed"
    )
)

if exist "server_err.log" (
    for %%A in (server_err.log) do set SERR_SIZE=%%~zA
    if !SERR_SIZE! GTR 524288 (
        call :info "Rotating server_err.log..."
        move /y "server_err.log" "logs\server_err_%TIMESTAMP%.log" >nul 2>&1
        type nul > "server_err.log"
        call :ok "server_err.log rotated"
    )
)

:: ── Purge old batch logs (keep last 8 weekends) ──────────────────────────
call :info "Cleaning up old batch logs (keeping last 8)..."
set LOGCOUNT=0
for /f %%F in ('dir /b /o-d "logs\weekend_batch_*.log" 2^>nul') do (
    set /a LOGCOUNT+=1
    if !LOGCOUNT! GTR 8 (
        del "logs\%%F" >nul 2>&1
    )
)
call :ok "Log cleanup complete (!LOGCOUNT! batch logs found)"

:: ── Clean Python cache ───────────────────────────────────────────────────
call :info "Cleaning __pycache__ directories..."
for /d /r . %%D in (__pycache__) do (
    if exist "%%D" rd /s /q "%%D" >nul 2>&1
)
call :ok "Python cache cleaned"

:: ── Clean pytest cache ───────────────────────────────────────────────────
if exist ".pytest_cache" (
    rd /s /q ".pytest_cache" >nul 2>&1
    call :ok "pytest cache cleaned"
)

:: ══════════════════════════════════════════════════════════════════════════
:: PHASE 7: RUN TEST SUITE
:: ══════════════════════════════════════════════════════════════════════════
call :header "PHASE 7: Test Suite"

call :info "Running unit tests..."
%PY% -m pytest tests/ -v --tb=short >> "%LOGFILE%" 2>&1
%PY% -m pytest tests/ --tb=line -q >nul 2>&1
if errorlevel 1 (
    call :warn "Some tests failed -- review log for details"
) else (
    call :ok "All tests passed"
)

:: ══════════════════════════════════════════════════════════════════════════
:: PHASE 8: GIT CHECKPOINT
:: ══════════════════════════════════════════════════════════════════════════
call :header "PHASE 8: Git Checkpoint"

where git >nul 2>&1
if errorlevel 1 (
    call :warn "Git not found in PATH -- skipping checkpoint"
    goto :final_report
)

if not exist ".git" (
    call :warn "Not a git repository -- skipping checkpoint"
    goto :final_report
)

:: Check for uncommitted changes
git status --porcelain 2>nul | findstr /r "." >nul 2>&1
if errorlevel 1 (
    call :info "No uncommitted changes -- nothing to checkpoint"
    goto :final_report
)

call :info "Creating weekend checkpoint commit..."
git add -A >nul 2>&1
git commit -m "Weekend batch maintenance -- %DATE% %TIME%" --quiet >nul 2>&1
if errorlevel 1 (
    call :warn "Git commit failed (may need manual review)"
) else (
    call :ok "Git checkpoint created"
)

:: ══════════════════════════════════════════════════════════════════════════
:: FINAL REPORT
:: ══════════════════════════════════════════════════════════════════════════
:final_report
echo.
echo  ===================================================================
echo    WEEKEND BATCH COMPLETE -- %DATE% %TIME%
echo  ===================================================================
echo.
echo.>> "%LOGFILE%"
echo ===================================================================>> "%LOGFILE%"
echo   WEEKEND BATCH COMPLETE -- %DATE% %TIME%>> "%LOGFILE%"
echo ===================================================================>> "%LOGFILE%"
echo.>> "%LOGFILE%"
if %ERRCOUNT% GTR 0 (
    call :log "  ERRORS:   %ERRCOUNT%"
) else (
    call :log "  ERRORS:   0  (all clear)"
)
if %WARNCOUNT% GTR 0 (
    call :log "  WARNINGS: %WARNCOUNT%"
) else (
    call :log "  WARNINGS: 0  (all clear)"
)
echo.
echo   Full log: %LOGFILE%
echo  ===================================================================
echo.
echo.>> "%LOGFILE%"
echo   Full log: %LOGFILE%>> "%LOGFILE%"
echo ===================================================================>> "%LOGFILE%"
echo.>> "%LOGFILE%"

:: ── Generate HTML summary report ────────────────────────────────────────
call :info "Generating HTML summary report..."
%PY% scripts\generate_batch_report.py "%TIMESTAMP%" %ERRCOUNT% %WARNCOUNT% >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    call :warn "HTML report generation failed"
) else (
    call :ok "HTML report generated"
)

echo.
echo  Opening report...
if exist "logs\weekend_report_%TIMESTAMP%.html" (
    start "" "logs\weekend_report_%TIMESTAMP%.html"
)

echo.
echo  Batch maintenance complete. This window will close in 10 seconds.
timeout /t 10 /nobreak >nul
exit /b 0


:: ══════════════════════════════════════════════════════════════════════════
:: HELPER FUNCTIONS
:: ══════════════════════════════════════════════════════════════════════════

:header
echo.
echo  --------------------------------------------------------
echo    %~1
echo  --------------------------------------------------------
echo [%DATE% %TIME%] == %~1 == >> "%LOGFILE%"
goto :eof

:log
echo %~1
echo %~1 >> "%LOGFILE%"
goto :eof

:ok
echo   [OK]   %~1
echo [%DATE% %TIME%] [OK]   %~1 >> "%LOGFILE%"
goto :eof

:info
echo   [..]   %~1
echo [%DATE% %TIME%] [INFO] %~1 >> "%LOGFILE%"
goto :eof

:warn
echo   [!!]   %~1
echo [%DATE% %TIME%] [WARN] %~1 >> "%LOGFILE%"
set /a WARNCOUNT+=1
goto :eof

:error
echo   [XX]   %~1
echo [%DATE% %TIME%] [ERR]  %~1 >> "%LOGFILE%"
set /a ERRCOUNT+=1
goto :eof
