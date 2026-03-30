"""
patch_step3.py  — inserts doStep3Search + KML helpers into app.js
Run once from the project folder.
"""
import re, sys

MARKER_START = '/** Skip deed download and jump directly to the plat step */'
MARKER_END   = 'function savePlatByIndex(idx) {'

INJECT = r"""
// ============================================================
// STEP 3: CLIENT PLAT
// ============================================================
async function doStep3Search() {
  const locCards = document.getElementById('s3LocalPlats');
  const kmlCards = document.getElementById('s3KmlPlats');
  const onlCards = document.getElementById('s3OnlinePlats');

  if (!state.selectedDetail) {
    const msg = '<div class="empty-state text-text3">No deed selected in Step 2. Go back and select a deed first.</div>';
    locCards.innerHTML = msg;
    if (kmlCards) kmlCards.innerHTML = msg;
    onlCards.innerHTML = msg;
    return;
  }

  locCards.innerHTML = '<div class="loading-state">Scanning local cabinets...</div>';
  if (kmlCards) kmlCards.innerHTML = '<div class="loading-state">Querying KML parcel index...</div>';
  onlCards.innerHTML  = '<div class="loading-state">Searching 1stnmtitle.com...</div>';

  try {
    const res = await apiFetch('/find-plat', 'POST', { detail: state.selectedDetail });
    if (!res.success) throw new Error(res.error);

    // ── Local Cabinet Hits ──────────────────────────────────────────────
    const cabinetHits = res.local || res.cabinet_hits || [];
    if (!cabinetHits.length) {
      locCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">' +
        '<div class="text-3xl mb-2">\u{1F5C4}\uFE0F</div>No cabinet plats found by deed reference or name.<br><br>' +
        '<button class="btn btn-outline btn-sm" onclick="openGlobalCabinetBrowser()">Browse Cabinets Manually</button></div>';
    } else {
      state._cabinetHits = cabinetHits;
      locCards.innerHTML = cabinetHits.map((f, fi) =>
        '<div class="plat-item">' +
          '<div class="plat-info">' +
            '<span class="plat-name text-xs" title="' + escHtml(f.path) + '">' + escHtml(f.file) + '</span>' +
            '<span class="plat-meta">Cabinet ' + (f.cabinet||'') + ' &nbsp;\u00B7&nbsp; ' + (f.strategy||'match') + '</span>' +
          '</div>' +
          '<button class="btn btn-success btn-sm" onclick="savePlatByIndex(' + fi + ')">\u2B07 Save</button>' +
        '</div>'
      ).join('');
    }

    // ── KML Parcel Hits ─────────────────────────────────────────────────
    if (kmlCards) {
      const kmlHits = res.kml_matches || [];
      if (!kmlHits.length) {
        kmlCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">' +
          '<div class="text-3xl mb-2">\u{1F5FA}\uFE0F</div>No parcel records found in KML index.<br><br>' +
          '<span class="text-xs opacity-60">Use the \u{1F5FA}\uFE0F KML Index button to build the index from county data.</span></div>';
      } else {
        state._kmlHits = kmlHits;
        kmlCards.innerHTML = kmlHits.map((p, pi) => {
          const ct = p.centroid ? 'Lat: ' + p.centroid[1].toFixed(5) + ', Lng: ' + p.centroid[0].toFixed(5) : '';
          const btns = (p.local_files && p.local_files.length)
            ? p.local_files.map((lf, lfi) =>
                '<button class="btn btn-success btn-sm" style="font-size:10px;padding:3px 7px;white-space:nowrap" ' +
                'onclick="saveKmlLocalFile(' + pi + ',' + lfi + ')" title="' + escHtml(lf.file) + '">' +
                '\u2B07 ' + escHtml(lf.cab_ref || (lf.cabinet + '-' + lf.doc)) + '</button>'
              ).join('')
            : '<span class="text-xs text-text3 italic">No local file</span>';
          return '<div class="plat-item kml-parcel-item">' +
            '<div class="plat-info" style="flex:1">' +
              '<span class="plat-name" title="' + escHtml(ct) + '">' + escHtml(p.owner) + '</span>' +
              '<div class="kml-meta-row">' +
                (p.upc   ? '<span class="kml-chip chip-upc">UPC: ' + escHtml(p.upc) + '</span>' : '') +
                (p.book  ? '<span class="kml-chip chip-book">Bk/Pg: ' + escHtml(p.book) + '/' + escHtml(p.page) + '</span>' : '') +
                (p.cab_refs_str ? '<span class="kml-chip chip-cab">' + escHtml(p.cab_refs_str) + '</span>' : '') +
              '</div>' +
              (p.match_reason ? '<span class="plat-meta text-xs" style="color:var(--accent2)">' + escHtml(p.match_reason) + '</span>' : '') +
              (p.plat ? '<span class="plat-meta text-xs" title="' + escHtml(p.plat) + '">' +
                escHtml(p.plat.substring(0,60)) + (p.plat.length > 60 ? '\u2026' : '') + '</span>' : '') +
            '</div>' +
            '<div class="flex-col gap-1" style="min-width:80px;align-items:flex-end">' + btns + '</div>' +
          '</div>';
        }).join('');
      }
    }

    // ── Online Survey Hits ──────────────────────────────────────────────
    const surveyHits = res.online || res.survey_hits || [];
    if (!surveyHits.length) {
      onlCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">No online survey records found for this grantor name.</div>';
    } else {
      onlCards.innerHTML = surveyHits.map(r =>
        '<div class="plat-item">' +
          '<div class="plat-info">' +
            '<span class="plat-name" title="' + escHtml(r.location||'') + '">' +
              escHtml((r.grantor||'').split(',')[0] || r.grantor||'') + '</span>' +
            '<span class="plat-meta">' + escHtml(r.instrument_type||'') +
              ' &nbsp;&nbsp; ' + escHtml(r.recorded_date||r.date||'') +
              ' &nbsp;&nbsp; Doc <span class="text-accent2">' + escHtml(r.doc_no) + '</span></span>' +
          '</div>' +
          '<button class="btn btn-outline btn-sm" ' +
            'onclick="saveClientPlatOnline(\'' + r.doc_no + '\',\'' + escHtml(r.location||'') + '\')">' +
            '\u2B07 Download</button>' +
        '</div>'
      ).join('');
    }

  } catch (e) {
    const errMsg = '<div class="text-danger p-3">Error: ' + e.message + '</div>';
    locCards.innerHTML = errMsg;
    if (kmlCards) kmlCards.innerHTML = errMsg;
    onlCards.innerHTML  = errMsg;
  }
}

async function saveKmlLocalFile(kmlIdx, fileIdx) {
  const rs = state.researchSession;
  if (!rs) { showToast('No active session', 'error'); return; }
  const p  = state._kmlHits && state._kmlHits[kmlIdx];
  if (!p || !p.local_files || !p.local_files[fileIdx]) { showToast('File not found', 'error'); return; }
  const lf = p.local_files[fileIdx];
  const clientSubj = rs.subjects.find(s => s.type === 'client');
  try {
    const res = await apiFetch('/save-plat', 'POST', {
      source: 'local', file_path: lf.path, filename: lf.file,
      job_number: rs.job_number, client_name: rs.client_name, job_type: rs.job_type,
      subject_id: clientSubj ? clientSubj.id : 'client'
    });
    if (res.success) {
      showToast(res.skipped ? 'Plat already exists in project' : 'Plat saved: ' + res.filename, 'success');
      if (clientSubj && !res.skipped) {
        clientSubj.plat_saved = true;
        if (res.saved_path) clientSubj.plat_path = res.saved_path;
        await persistSession();
      }
      setTimeout(() => goToStep(4), 800);
    } else { showToast('Save failed: ' + res.error, 'error'); }
  } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

// ── KML Index Status Modal ───────────────────────────────────────────────────
async function showKmlIndexModal() {
  document.getElementById('kmlIndexOverlay').classList.remove('hidden');
  const body = document.getElementById('kmlIndexBody');
  body.innerHTML = '<div class="loading-state">Loading index status...</div>';
  try {
    const res = await apiFetch('/xml/status');
    if (!res.success) throw new Error(res.error);
    const srcRows = (res.sources || []).map(s =>
      '<tr><td class="text-xs">' + escHtml(s.file) + '</td>' +
      '<td class="text-xs text-accent2 text-right">' + s.records.toLocaleString() + '</td></tr>'
    ).join('');
    const fileRows = (res.xml_files || []).map(f =>
      '<tr><td class="text-xs">' + escHtml(f.name) + '</td>' +
      '<td class="text-xs text-text3">' + f.format.toUpperCase() + '</td>' +
      '<td class="text-xs text-right">' + f.size_mb + ' MB</td></tr>'
    ).join('');
    body.innerHTML =
      '<div class="kml-status-block ' + (res.exists ? 'kml-ok' : 'kml-warn') + '">' +
      (res.exists
        ? '<strong style="color:var(--accent2)">\u2705 Index Ready</strong> &nbsp;\u2014&nbsp; ' + (res.total||0).toLocaleString() + ' parcels<br>' +
          '<span class="text-xs text-text3">Built: ' + (res.built_at||'unknown') + ' &nbsp;\u00B7&nbsp; ' + (res.size_mb||'?') + ' MB</span>'
        : '<strong style="color:#ff7b72">\u26A0 No Index Yet</strong> \u2014 Build it below to enable KML parcel search.') +
      '</div>' +
      (res.exists && srcRows
        ? '<div class="mt-3"><div class="text-xs font-bold uppercase text-text3 mb-1">Indexed Sources</div>' +
          '<table class="data-table"><tbody>' + srcRows + '</tbody></table></div>' : '') +
      (fileRows
        ? '<div class="mt-3"><div class="text-xs font-bold uppercase text-text3 mb-1">KML / KMZ Files Available</div>' +
          '<table class="data-table"><tbody>' + fileRows + '</tbody></table></div>'
        : '<div class="empty-state text-sm mt-3">No KML/KMZ files found in Survey Data\\XML folder.</div>');
  } catch(e) {
    body.innerHTML = '<div class="text-danger p-3">Error: ' + e.message + '</div>';
  }
}

function closeKmlModal() {
  document.getElementById('kmlIndexOverlay').classList.add('hidden');
}

async function buildKmlIndex() {
  const btn = document.getElementById('btnBuildIndex');
  btn.disabled = true; btn.innerHTML = '\u23F3 Building...';
  document.getElementById('kmlIndexBody').innerHTML = '<div class="loading-state">Parsing KML/KMZ files \u2014 this may take a minute.</div>';
  try {
    const res = await apiFetch('/xml/build-index', 'POST', {});
    if (!res.success) throw new Error(res.error);
    showToast('KML index built: ' + (res.total||0).toLocaleString() + ' parcels in ' + res.elapsed_sec + 's', 'success');
    await showKmlIndexModal();
  } catch(e) {
    showToast('Build failed: ' + e.message, 'error');
    document.getElementById('kmlIndexBody').innerHTML = '<div class="text-danger p-3">Error: ' + e.message + '</div>';
  } finally {
    btn.disabled = false; btn.innerHTML = '\u26A1 Build / Rebuild Index';
  }
}

"""

with open('app.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the insertion point: just before "function savePlatByIndex"
idx = content.find('\n' + MARKER_END)
if idx == -1:
    idx = content.find(MARKER_END)
    if idx == -1:
        print('ERROR: marker not found'); sys.exit(1)

# Check if doStep3Search already exists (don't double-inject)
if 'doStep3Search' in content:
    print('doStep3Search already exists — removing old version first')
    # Remove old version between STEP3 marker and savePlatByIndex
    old_start = content.find('// ============================================================\n// STEP 3')
    if old_start == -1:
        old_start = content.find('// \n// STEP 3: CLIENT PLAT\n// \nasync function doStep3Search')
    if old_start != -1:
        old_end = content.find('\nfunction savePlatByIndex', old_start)
        if old_end != -1:
            content = content[:old_start] + content[old_end:]
            print('Removed old Step 3 block')
    idx = content.find('\n' + MARKER_END)
    if idx == -1:
        idx = content.find(MARKER_END)

content = content[:idx] + INJECT + content[idx:]

with open('app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print(f'SUCCESS — app.js patched. Total size: {len(content)} bytes')
