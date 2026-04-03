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
::    7. Git checkpoint (auto-commit current state)
::    8. Summary report generation
:: ═══════════════════════════════════════════════════════════════════════════

set TIMESTAMP=%DATE:~10,4%%DATE:~4,2%%DATE:~7,2%_%TIME:~0,2%%TIME:~3,2%
set TIMESTAMP=%TIMESTAMP: =0%
set LOGFILE=logs\weekend_batch_%TIMESTAMP%.log
set ERRCOUNT=0
set WARNCOUNT=0

:: Create logs directory
if not exist "logs" mkdir logs

:: ── Logging helpers ──────────────────────────────────────────────────────
:: Tee output to both console and log file
call :log "═══════════════════════════════════════════════════════════════"
call :log "  WEEKEND BATCH MAINTENANCE — Started %DATE% %TIME%"
call :log "═══════════════════════════════════════════════════════════════"
call :log ""

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
    call :warn "Virtual environment missing — creating..."
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
    call :warn "Missing dependencies — installing..."
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
    call :warn "Skipping — XML folder not found"
    goto :phase3
)

call :info "Rebuilding parcel index with ArcGIS enrichment..."
call :info "This may take 5-15 minutes depending on network speed..."

%PY% -c "
import sys, json, time
sys.path.insert(0, '.')
import xml_processor

survey_path = r'%SURVEY_PATH%'
print(f'[batch] Starting index build for: {survey_path}')
t0 = time.time()

def progress(current, total, msg):
    pct = round(current/total*100) if total else 0
    print(f'  [{pct:3d}%%] {msg}', flush=True)

try:
    result = xml_processor.build_index(survey_path, progress_callback=progress)
    elapsed = round(time.time() - t0, 1)
    print(f'[batch] Index build complete in {elapsed}s')
    print(f'  Total parcels: {result.get(\"total\", 0)}')
    print(f'  ArcGIS enriched: {result.get(\"arcgis_enriched\", 0)}')
    print(f'  Sources: {len(result.get(\"sources\", []))}')
    for s in result.get('sources', []):
        print(f'    - {s[\"file\"]}: {s[\"records\"]} records')
except Exception as e:
    print(f'[batch] ERROR: Index build failed: {e}', file=sys.stderr)
    sys.exit(1)
" >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    call :error "Parcel index rebuild failed — check log for details"
) else (
    call :ok "Parcel index rebuilt successfully"
)

:: ══════════════════════════════════════════════════════════════════════════
:: PHASE 3: CABINET INDEX REBUILD
:: ══════════════════════════════════════════════════════════════════════════
:phase3
call :header "PHASE 3: Cabinet Index Rebuild"

if not exist "%CABINET_PATH%" (
    call :warn "Skipping — Cabinet folder not found"
    goto :phase4
)

call :info "Scanning all cabinet folders and rebuilding index..."

%PY% -c "
import sys, json, time
sys.path.insert(0, '.')
from helpers.cabinet import (
    CABINET_FOLDERS, _init_index_path, _scan_cabinet_dir,
    _INDEX, _INDEX_LOCK, _save_index_to_disk
)
from pathlib import Path

app_dir = '.'
_init_index_path(app_dir)

cabinet_path = r'%CABINET_PATH%'
t0 = time.time()
total_files = 0

for letter, folder in CABINET_FOLDERS.items():
    cab_dir = Path(cabinet_path) / folder
    if not cab_dir.exists():
        print(f'  [skip] Cabinet {letter}: folder not found ({folder})')
        continue
    print(f'  Scanning Cabinet {letter} ({folder})...', flush=True)
    try:
        mtime = cab_dir.stat().st_mtime
        files = _scan_cabinet_dir(cab_dir)
        with _INDEX_LOCK:
            _INDEX[letter] = {'mtime': mtime, 'files': files}
        count = len(files)
        total_files += count
        print(f'  [OK] Cabinet {letter}: {count} PDFs indexed')
    except Exception as e:
        print(f'  [ERR] Cabinet {letter}: {e}', file=sys.stderr)

_save_index_to_disk()
elapsed = round(time.time() - t0, 1)
print(f'[batch] Cabinet index rebuild complete: {total_files} total PDFs in {elapsed}s')
" >> "%LOGFILE%" 2>&1
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

%PY% -c "
import sys, json, time
sys.path.insert(0, '.')
from helpers.research_analytics import scan_all_research, compute_aggregate_stats

survey_path = r'%SURVEY_PATH%'
t0 = time.time()

print('[batch] Scanning research sessions...', flush=True)
sessions = scan_all_research(survey_path)
elapsed = round(time.time() - t0, 1)

if not sessions:
    print(f'[batch] No research sessions found ({elapsed}s)')
    sys.exit(0)

stats = compute_aggregate_stats(sessions)
print(f'[batch] Research analytics complete in {elapsed}s')
print(f'  Total jobs scanned:     {stats.get(\"total_jobs\", 0)}')
print(f'  Total subjects:         {stats.get(\"total_subjects\", 0)}')
print(f'  Total deeds saved:      {stats.get(\"total_deeds\", 0)}')
print(f'  Total plats saved:      {stats.get(\"total_plats\", 0)}')
print(f'  Avg adjoiners/job:      {stats.get(\"avg_adjoiners\", 0)}')
print(f'  Avg completion:         {stats.get(\"avg_completion_pct\", 0)}%%')
print(f'  Date range:             {stats.get(\"date_range\", {}).get(\"oldest\", \"?\")} to {stats.get(\"date_range\", {}).get(\"newest\", \"?\")}')
print()
print('  Jobs by type:')
for jtype, count in stats.get('jobs_by_type', {}).items():
    print(f'    {jtype:8s}: {count}')
print()
print('  Completion tiers:')
tiers = stats.get('completion_tiers', {})
for tier, count in tiers.items():
    bar = '█' * min(count, 40)
    print(f'    {tier:12s}: {count:3d} {bar}')
print()

# ── Identify incomplete jobs for follow-up ──
incomplete = [s for s in sessions if s['completion_pct'] < 50 and s['adjoiner_count'] > 0]
if incomplete:
    print(f'  ⚠ {len(incomplete)} jobs are <50%% complete:')
    for s in incomplete[:10]:
        print(f'    Job #{s[\"job_number\"]} {s[\"client_name\"]:30s} {s[\"completion_pct\"]:5.1f}%% ({s[\"adjoiner_count\"]} adjoiners)')
    if len(incomplete) > 10:
        print(f'    ... and {len(incomplete)-10} more')

# Save analytics snapshot
snapshot = {
    'generated_at': time.strftime('%%Y-%%m-%%dT%%H:%%M:%%S'),
    'stats': stats,
    'incomplete_jobs': [{'job': s['job_number'], 'client': s['client_name'],
                          'pct': s['completion_pct'], 'adj': s['adjoiner_count']}
                         for s in incomplete[:50]],
}
snapshot_path = 'data/analytics_snapshot.json'
import os
os.makedirs('data', exist_ok=True)
with open(snapshot_path, 'w', encoding='utf-8') as f:
    json.dump(snapshot, f, indent=2, ensure_ascii=False)
print(f'  Snapshot saved to: {snapshot_path}')
" >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    call :error "Research analytics refresh failed"
) else (
    call :ok "Research analytics refreshed"
)

:: ══════════════════════════════════════════════════════════════════════════
:: PHASE 5: OCR CACHE WARM-UP
:: ══════════════════════════════════════════════════════════════════════════
call :header "PHASE 5: OCR Cache Warm-up"

call :info "Pre-processing unscanned cabinet PDFs (first 50 per cabinet)..."
call :info "This can take a while — each PDF requires OCR..."

%PY% -c "
import sys, os, time, json
sys.path.insert(0, '.')
from helpers.pdf_extract import setup_tesseract, extract_pdf_text
from helpers.cabinet import CABINET_FOLDERS, _init_index_path, _INDEX, _INDEX_LOCK
from pathlib import Path

setup_tesseract()
_init_index_path('.')

cabinet_path = r'%CABINET_PATH%'
ocr_cache_dir = Path('data') / 'ocr_cache'
ocr_cache_dir.mkdir(parents=True, exist_ok=True)

MAX_PER_CABINET = 50  # Limit to prevent runaway processing
total_processed = 0
total_cached = 0
total_errors = 0

for letter, folder in CABINET_FOLDERS.items():
    cab_dir = Path(cabinet_path) / folder
    if not cab_dir.exists():
        continue

    # Get indexed files for this cabinet
    with _INDEX_LOCK:
        entry = _INDEX.get(letter, {})
    files = entry.get('files', [])
    if not files:
        continue

    processed_this_cab = 0
    for row in files:
        fname, display, fname_norm, name_norm, doc_num, fpath = row
        if not fpath or not os.path.exists(fpath):
            continue

        # Check if already cached
        cache_key = f'{letter}_{doc_num}_{fname}'.replace(' ', '_').replace('.', '_')
        cache_file = ocr_cache_dir / f'{cache_key}.txt'
        if cache_file.exists():
            total_cached += 1
            continue

        if processed_this_cab >= MAX_PER_CABINET:
            break

        try:
            text, method = extract_pdf_text(fpath)
            if text and len(text.strip()) > 20:
                cache_file.write_text(text[:5000], encoding='utf-8')
                total_processed += 1
                processed_this_cab += 1
                if total_processed %% 10 == 0:
                    print(f'  Processed {total_processed} PDFs...', flush=True)
        except Exception as e:
            total_errors += 1

    if processed_this_cab > 0:
        print(f'  Cabinet {letter}: {processed_this_cab} new PDFs processed')

print(f'[batch] OCR warm-up complete: {total_processed} new, {total_cached} cached, {total_errors} errors')
" >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    call :error "OCR cache warm-up encountered errors"
) else (
    call :ok "OCR cache warm-up complete"
)

:: ══════════════════════════════════════════════════════════════════════════
:: PHASE 6: LOG ROTATION & CLEANUP
:: ══════════════════════════════════════════════════════════════════════════
:phase6
call :header "PHASE 6: Log Rotation ^& Cleanup"

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
        call :info "server.log is small (!SLOG_SIZE! bytes) — no rotation needed"
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
%PY% -m pytest tests/ -v --tb=short 2>&1 | findstr /V "^$" >> "%LOGFILE%"
%PY% -m pytest tests/ --tb=line -q >nul 2>&1
if errorlevel 1 (
    call :warn "Some tests failed — review log for details"
) else (
    call :ok "All tests passed"
)

:: ══════════════════════════════════════════════════════════════════════════
:: PHASE 8: GIT CHECKPOINT
:: ══════════════════════════════════════════════════════════════════════════
call :header "PHASE 8: Git Checkpoint"

where git >nul 2>&1
if errorlevel 1 (
    call :warn "Git not found in PATH — skipping checkpoint"
    goto :final_report
)

if not exist ".git" (
    call :warn "Not a git repository — skipping checkpoint"
    goto :final_report
)

:: Check for uncommitted changes
git status --porcelain 2>nul | findstr /r "." >nul 2>&1
if errorlevel 1 (
    call :info "No uncommitted changes — nothing to checkpoint"
    goto :final_report
)

call :info "Creating weekend checkpoint commit..."
git add -A >nul 2>&1
git commit -m "🔧 Weekend batch maintenance — %DATE% %TIME%" --quiet >nul 2>&1
if errorlevel 1 (
    call :warn "Git commit failed (may need manual review)"
) else (
    call :ok "Git checkpoint created"
)

:: ══════════════════════════════════════════════════════════════════════════
:: FINAL REPORT
:: ══════════════════════════════════════════════════════════════════════════
:final_report
call :log ""
call :log "═══════════════════════════════════════════════════════════════"
call :log "  WEEKEND BATCH COMPLETE — %DATE% %TIME%"
call :log "═══════════════════════════════════════════════════════════════"
call :log ""
if %ERRCOUNT% GTR 0 (
    call :log "  ❌ ERRORS:   %ERRCOUNT%"
) else (
    call :log "  ✅ ERRORS:   0"
)
if %WARNCOUNT% GTR 0 (
    call :log "  ⚠  WARNINGS: %WARNCOUNT%"
) else (
    call :log "  ✅ WARNINGS: 0"
)
call :log ""
call :log "  Full log: %LOGFILE%"
call :log "═══════════════════════════════════════════════════════════════"
call :log ""

:: ── Generate HTML summary report ────────────────────────────────────────
%PY% -c "
import json, os, time
from pathlib import Path
from datetime import datetime

errs = %ERRCOUNT%
warns = %WARNCOUNT%
status_emoji = '✅' if errs == 0 else '❌'
status_text = 'All Clear' if errs == 0 and warns == 0 else f'{errs} Errors, {warns} Warnings'

# Load analytics snapshot if available
analytics = {}
snap_path = Path('data/analytics_snapshot.json')
if snap_path.exists():
    try:
        analytics = json.loads(snap_path.read_text(encoding='utf-8'))
    except: pass

stats = analytics.get('stats', {})
incomplete = analytics.get('incomplete_jobs', [])

html = f'''<!DOCTYPE html>
<html lang=\"en\">
<head>
<meta charset=\"UTF-8\">
<title>Weekend Batch Report — {datetime.now().strftime('%%B %%d, %%Y')}</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0d1117; color: #c9d1d9; margin: 0; padding: 24px; }}
  .container {{ max-width: 720px; margin: 0 auto; }}
  h1 {{ color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 12px; font-size: 22px; }}
  h2 {{ color: #8b949e; font-size: 15px; text-transform: uppercase; letter-spacing: 1px; margin-top: 28px; }}
  .status {{ display: inline-block; padding: 6px 16px; border-radius: 20px; font-weight: 600; font-size: 14px;
             background: {'#238636' if errs==0 else '#da3633'}; color: #fff; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin: 8px 0; }}
  .stat {{ display: inline-block; width: 140px; text-align: center; margin: 8px; }}
  .stat-val {{ font-size: 28px; font-weight: 700; color: #58a6ff; }}
  .stat-label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; }}
  table {{ width: 100%%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; color: #8b949e; padding: 6px 8px; border-bottom: 1px solid #30363d; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #21262d; }}
  .pct {{ color: #f0883e; font-weight: 600; }}
  .footer {{ text-align: center; color: #484f58; font-size: 11px; margin-top: 32px; }}
</style>
</head>
<body>
<div class=\"container\">
  <h1>{status_emoji} Weekend Batch Report</h1>
  <p class=\"status\">{status_text}</p>
  <p style=\"color:#8b949e;font-size:13px\">Generated {datetime.now().strftime('%%A, %%B %%d, %%Y at %%I:%%M %%p')}</p>

  <h2>Research Analytics</h2>
  <div class=\"card\" style=\"text-align:center\">
    <div class=\"stat\"><div class=\"stat-val\">{stats.get('total_jobs', 0)}</div><div class=\"stat-label\">Jobs Scanned</div></div>
    <div class=\"stat\"><div class=\"stat-val\">{stats.get('total_deeds', 0)}</div><div class=\"stat-label\">Deeds Saved</div></div>
    <div class=\"stat\"><div class=\"stat-val\">{stats.get('total_plats', 0)}</div><div class=\"stat-label\">Plats Saved</div></div>
    <div class=\"stat\"><div class=\"stat-val\">{stats.get('avg_completion_pct', 0)}%%</div><div class=\"stat-label\">Avg Completion</div></div>
  </div>
'''

if incomplete:
    html += '''
  <h2>Incomplete Jobs (follow-up needed)</h2>
  <div class=\"card\">
    <table>
      <tr><th>Job #</th><th>Client</th><th>Completion</th><th>Adjoiners</th></tr>
'''
    for j in incomplete[:15]:
        html += f'      <tr><td>{j[\"job\"]}</td><td>{j[\"client\"]}</td><td class=\"pct\">{j[\"pct\"]}%%</td><td>{j[\"adj\"]}</td></tr>\\n'
    html += '''    </table>
  </div>
'''

html += f'''
  <div class=\"footer\">Deed &amp; Plat Helper — Weekend Batch Maintenance</div>
</div>
</body>
</html>'''

report_path = r'logs\weekend_report_%TIMESTAMP%.html'
with open(report_path, 'w', encoding='utf-8') as f:
    f.write(html)
print(f'  Report saved: {report_path}')
" >> "%LOGFILE%" 2>&1

echo.
echo  Report generated. Opening...
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
echo  ┌────────────────────────────────────────────────────────────┐
echo  │  %~1
echo  └────────────────────────────────────────────────────────────┘
echo [%DATE% %TIME%] ── %~1 ── >> "%LOGFILE%"
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
echo   [··]   %~1
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
