// @ts-nocheck
const API = "/api";  // Use relative URL to avoid CORS issues


const state = {
  currentStep: 1,
  loggedIn: false,
  nextJobNum: null,
  researchSession: null,
  selectedDoc: null,
  selectedDetail: null,
  discoveredAdjoiners: [],
  parsedCalls: [],
  adjoinParcels: [],
  searchResults: [],
  _dirty: false,           // tracks unsaved changes
  _pcpCollapsed: false,    // property context panel collapse state
  _pcpGeomCache: {},       // UPC → polygon rings for mini-map
  // ── Profile state ──
  activeProfile: null,     // currently active profile object
  profiles: [],            // all available profiles
};

// ── Profile System ────────────────────────────────────────────────────────────

/** Cookie helpers */
function _setCookie(name, value, days = 365) {
  const d = new Date();
  d.setTime(d.getTime() + days * 86400000);
  document.cookie = `${name}=${encodeURIComponent(value)};expires=${d.toUTCString()};path=/;SameSite=Lax`;
}
function _getCookie(name) {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : null;
}
function _deleteCookie(name) {
  document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/;SameSite=Lax`;
}

/** Generate initials from a display name (up to 2 characters) */
function _profileInitials(name) {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

/** Pick a deterministic gradient color for a profile based on its id */
const _AVATAR_GRADIENTS = [
  ['#2d8a6e', '#1f6b54'],  // green (default)
  ['#7a4f9a', '#5c3876'],  // purple
  ['#c9a227', '#9a7c1e'],  // gold
  ['#3b5e99', '#2a4470'],  // blue
  ['#da3633', '#a02a28'],  // red
  ['#2ea043', '#1d7a32'],  // emerald
  ['#e08050', '#bb5a30'],  // rust
  ['#5ba8c8', '#3d7a9a'],  // teal
];
function _avatarGradient(profileId) {
  let hash = 0;
  for (let i = 0; i < (profileId || '').length; i++) hash = (hash * 31 + profileId.charCodeAt(i)) | 0;
  const idx = Math.abs(hash) % _AVATAR_GRADIENTS.length;
  return _AVATAR_GRADIENTS[idx];
}

/** Update the topbar badge with the current profile */
function _updateProfileBadge() {
  const avatarEl = document.getElementById('profileAvatar');
  const nameEl = document.getElementById('profileName');
  if (!avatarEl || !nameEl) return;

  if (state.activeProfile) {
    const p = state.activeProfile;
    const [c1, c2] = _avatarGradient(p.id);
    avatarEl.textContent = _profileInitials(p.display_name);
    avatarEl.style.background = `linear-gradient(135deg, ${c1}, ${c2})`;
    nameEl.textContent = p.display_name;
  } else {
    avatarEl.textContent = '?';
    avatarEl.style.background = '';
    nameEl.textContent = 'Select Profile';
  }
}

/** Fetch all profiles from backend and populate state.profiles */
async function _loadProfiles() {
  try {
    const res = await apiFetch('/profiles');
    if (res.success) {
      state.profiles = res.profiles || [];
    }
  } catch (e) {
    console.error('Failed to load profiles:', e);
  }
  return state.profiles;
}

/** Activate a profile: set cookie, update state, refresh UI */
async function switchProfile(profileId) {
  const profile = state.profiles.find(p => p.id === profileId);
  if (!profile) {
    showToast('Profile not found', 'error');
    return;
  }

  // Set cookie so backend can identify the user
  _setCookie('profile_id', profileId);
  state.activeProfile = profile;
  _updateProfileBadge();
  closeProfileSelector();

  // Load this profile's credentials into the config form
  try {
    const res = await apiFetch('/config');
    if (res.success && res.config) {
      const cfgUser = document.getElementById('cfgUser');
      const cfgPass = document.getElementById('cfgPass');
      if (cfgUser) cfgUser.value = res.config.firstnm_user || '';
      if (cfgPass) cfgPass.value = res.config.firstnm_pass || '';
    }
  } catch (_) {}

  // Restore this profile's last session if present
  state.lastSession = profile.last_session || null;
  if (state.lastSession) {
    const jobNum = document.getElementById('setupJobNum');
    const client = document.getElementById('setupClient');
    const jobType = document.getElementById('setupJobType');
    if (jobNum) jobNum.value = state.lastSession.job_number || '';
    if (client) client.value = state.lastSession.client_name || '';
    if (jobType) jobType.value = state.lastSession.job_type || 'BDY';
  }

  // Reload recent jobs for context
  loadRecentJobs();

  // Re-check login with this profile's credentials
  checkLogin();

  showToast(`Switched to ${profile.display_name}`, 'success');
}

/** Show the profile selector modal */
async function showProfileSelector() {
  const overlay = document.getElementById('profileOverlay');
  const grid = document.getElementById('profileGrid');
  if (!overlay || !grid) return;

  overlay.classList.remove('hidden');

  // Refresh profiles from server
  await _loadProfiles();

  const activeId = state.activeProfile?.id || _getCookie('profile_id') || '';

  let html = '';
  for (const p of state.profiles) {
    const isActive = p.id === activeId;
    const [c1, c2] = _avatarGradient(p.id);
    html += `
      <div class="profile-card ${isActive ? 'profile-card-active' : ''}"
           onclick="switchProfile('${p.id}')" title="Switch to ${escHtml(p.display_name)}">
        ${isActive ? '<div class="profile-card-current">Current</div>' : ''}
        <div class="profile-card-avatar" style="background:linear-gradient(135deg,${c1},${c2})">
          ${_profileInitials(p.display_name)}
        </div>
        <div class="profile-card-name">${escHtml(p.display_name)}</div>
      </div>`;
  }

  // "New Profile" card
  html += `
    <div class="profile-card profile-card-new" onclick="createNewProfile()" title="Create a new profile">
      <div class="profile-card-avatar" style="font-size:24px;color:var(--text3)">+</div>
      <div class="profile-card-name" style="color:var(--text3)">New Profile</div>
    </div>`;

  grid.innerHTML = html;
}

function closeProfileSelector() {
  const overlay = document.getElementById('profileOverlay');
  if (overlay) overlay.classList.add('hidden');
}

/** Prompt for a name and create a new profile */
async function createNewProfile() {
  const name = prompt('Enter display name for the new profile:');
  if (!name || !name.trim()) return;

  try {
    const res = await apiFetch('/profiles', 'POST', { display_name: name.trim() });
    if (res.success && res.profile) {
      showToast(`Profile "${name.trim()}" created!`, 'success');
      state.profiles.push(res.profile);
      await switchProfile(res.profile.id);
    } else {
      showToast('Failed to create profile: ' + (res.error || 'Unknown error'), 'error');
    }
  } catch (e) {
    showToast('Error creating profile: ' + e.message, 'error');
  }
}

/** Initialize profiles on page load — restore last active or prompt */
async function _initProfiles() {
  await _loadProfiles();

  const savedId = _getCookie('profile_id');
  if (savedId) {
    const match = state.profiles.find(p => p.id === savedId);
    if (match) {
      state.activeProfile = match;
      _updateProfileBadge();
      return; // profile restored successfully
    }
  }

  // No saved profile — if profiles exist, auto-show the selector
  if (state.profiles.length > 0) {
    _updateProfileBadge();
    // Brief delay to let the page render first
    setTimeout(() => showProfileSelector(), 600);
  }
}


// ── Global Keyboard Shortcuts ─────────────────────────────────────────────────
function _handleGlobalKeyboard(e) {
  // Ignore events when an input/textarea/select is focused (let users type normally)
  const tag = (e.target.tagName || '').toLowerCase();
  const isInputFocused = tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable;

  // ── Escape: close any open modal ──────────────────────────────────────────
  if (e.key === 'Escape') {
    const modals = document.querySelectorAll('.modal-overlay:not(.hidden)');
    if (modals.length) {
      // Close the topmost (last) visible modal
      const top = modals[modals.length - 1];
      const closeBtn = top.querySelector('.close-btn');
      if (closeBtn) closeBtn.click();
      else top.classList.add('hidden');
      e.preventDefault();
      return;
    }
    // Close plat preview panel if open
    const preview = document.getElementById('platPreviewPanel');
    if (preview && preview.classList.contains('open')) {
      closePlatPreview();
      e.preventDefault();
      return;
    }
  }

  // ── Ctrl+Enter: primary action for current step ───────────────────────────
  if (e.ctrlKey && e.key === 'Enter') {
    e.preventDefault();
    switch (state.currentStep) {
      case 1: startSession(); break;
      case 2: {
        // If a deed is selected, save it; otherwise run search
        if (state.selectedDetail) {
          const saveBtn = document.querySelector('#s2DetailContainer .btn-success');
          if (saveBtn) saveBtn.click();
        } else {
          doStep2Search();
        }
        break;
      }
      case 4: runAdjoinerDiscovery(); break;
      case 5: goToStep(6); break;
      case 6: doGenerateDxf(); break;
    }
    return;
  }

  // ── Ctrl+F: focus the search field for current step ───────────────────────
  if (e.ctrlKey && e.key === 'f') {
    let searchEl = null;
    switch (state.currentStep) {
      case 2: searchEl = document.getElementById('s2SearchName'); break;
      case 3: searchEl = document.getElementById('s3CabinetSelect'); break;
      case 4: searchEl = document.getElementById('s4ManualName'); break;
    }
    if (searchEl) {
      e.preventDefault();
      searchEl.focus();
      searchEl.select?.();
      return;
    }
  }

  // ── Number keys 1-6: jump to step (only when not typing) ─────────────────
  if (!isInputFocused && !e.ctrlKey && !e.altKey && !e.metaKey) {
    const num = parseInt(e.key);
    if (num >= 1 && num <= 6) {
      goToStep(num);
      e.preventDefault();
      return;
    }
  }
}

// ── Unsaved-changes guard ─────────────────────────────────────────────────────
window.addEventListener('beforeunload', (e) => {
  if (state._dirty && state.researchSession) {
    e.preventDefault();
    // Modern browsers show a generic message; returnValue is required for compat
    e.returnValue = 'You have unsaved research — are you sure you want to leave?';
  }
});

// Active AbortControllers by operation key — used to cancel stale requests
const _abortControllers = {};
function _getAbortSignal(key) {
  // Abort any previous request for this operation
  if (_abortControllers[key]) _abortControllers[key].abort();
  _abortControllers[key] = new AbortController();
  return _abortControllers[key].signal;
}

// 
// INIT & BOOTSTRAP
// 
document.addEventListener("DOMContentLoaded", async () => {
  // Initialize profile system — restores saved profile or prompts user
  await _initProfiles();

  // Load config and recent jobs immediately  do NOT await checkLogin first
  // checkLogin hits 1stnmtitle.com which can block for up to 30s
  await loadConfig();
  loadRecentJobs(); // fire immediately, no await
  refreshAiInsights(); // populate AI Insights panel in background
  checkLogin();     // fire in background, no await

  // Restore last session fields
  if (state.lastSession) {
    document.getElementById("setupJobNum").value = state.lastSession.job_number || "";
    document.getElementById("setupClient").value = state.lastSession.client_name || "";
    document.getElementById("setupJobType").value = state.lastSession.job_type || "BDY";
  } else {
    apiFetch("/next-job-number").then(r => {
      if (r.success) {
        state.nextJobNum = r.next_job_number;
        document.getElementById("setupJobNum").placeholder = "Auto: " + r.next_job_number;
      }
    }).catch(() => { });
  }

  updateStepUI();

  // ── Keyboard Shortcuts ──────────────────────────────────────────────────
  document.addEventListener('keydown', _handleGlobalKeyboard);
});

async function loadConfig() {
  try {
    const res = await apiFetch("/config");
    if (res.success) {
      if (res.config.firstnm_user) document.getElementById("cfgUser").value = res.config.firstnm_user;
      if (res.config.firstnm_pass) document.getElementById("cfgPass").value = res.config.firstnm_pass;
      if (res.config.firstnm_url) document.getElementById("cfgUrl").value = res.config.firstnm_url;
      state.lastSession = res.config.last_session;
    }
  } catch (e) {
    console.error("Config load failed", e);
  }
}

// 
// WORKFLOW STEPPER LOGIC
// 
function goToStep(step) {
  if (step > 1 && !state.researchSession) {
    showToast("Please start a research session first", "warn");
    return;
  }
  state.currentStep = step;
  updateStepUI();
}

function updateStepUI() {
  // Update nav buttons
  document.querySelectorAll(".step-btn").forEach(btn => {
    const s = parseInt(btn.dataset.step);
    btn.classList.toggle("active", s === state.currentStep);
    btn.classList.toggle("completed", s < state.currentStep);

    // Unlock logic
    if (state.researchSession) {
      btn.disabled = false;
    }
  });

  // Update progress bar
  const pct = ((state.currentStep - 1) / 5) * 100;
  document.getElementById("stepProgressFill").style.width = pct + "%";

  // Show active panel
  for (let i = 1; i <= 6; i++) {
    const panel = document.getElementById(`step${i}Panel`);
    if (panel) {
      if (i === state.currentStep) {
        panel.classList.remove("hidden");
        // small delay to let display:block apply before opacity transition
        requestAnimationFrame(() => panel.classList.add("active"));
      } else {
        panel.classList.remove("active");
        panel.classList.add("hidden");
      }
    }
  }

  // Trigger step-specific logic
  if (state.currentStep === 2 && state.researchSession) {
    const nameField = document.getElementById("s2SearchName");
    if (!nameField.value) {
      nameField.value = state.researchSession.client_name;
    }
    // Auto-fire search if name is pre-populated (avoid redundant searches)
    if (nameField.value && nameField.value.length >= 2 && !state._step2Searched) {
      state._step2Searched = true;
      setTimeout(() => doStep2Search(), 400);
    }
  }
  if (state.currentStep === 3) {
    doStep3Search();
  }
  if (state.currentStep === 4 && state.researchSession) {
    // Auto-run discovery scan if the client deed has been saved
    const clientSubj = state.researchSession.subjects.find(s => s.type === 'client');
    if (clientSubj && clientSubj.deed_saved && !state._adjDiscoveryRan) {
      state._adjDiscoveryRan = true;
      setTimeout(() => runAdjoinerDiscovery(true), 600);
    }
  }
  if (state.currentStep === 5) {
    renderResearchBoard();
  }
  if (state.currentStep === 6) {
    switchS6Tab('calls');
    // Auto-import calls from deed if not already done
    if (state.selectedDetail && !state.parsedCalls.length) {
      setTimeout(() => reparseClientCallsFromSession(true), 400);
    }
    // Auto-populate adjoiner parcels from board
    if (state.researchSession && !state.adjoinParcels.length) {
      setTimeout(() => autoPopulateAdjoiners(true), 600);
    }
  }

  // ── Refresh the property context panel on every step change ──
  renderPropertyContextPanel();
}

function updateJobContext() {
  const bar = document.getElementById("jobContextBar");
  if (!state.researchSession) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  document.getElementById("ctxJobNum").textContent = state.researchSession.job_number;
  document.getElementById("ctxClient").textContent = state.researchSession.client_name;
  document.getElementById("ctxType").textContent = state.researchSession.job_type;
  updateFileBadges();
}

// ─────────────────────────────────────────────────────────────────────────────
// PROPERTY CONTEXT PANEL  (color-coded mini-map + subject list — steps 2–6)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Renders (or hides) the persistent property context panel.
 * Shows a mini-map with client (gold) and adjoiners (purple) plus a
 * subject list with deed/plat status badges.
 */
function renderPropertyContextPanel() {
  const panel = document.getElementById('propertyContextPanel');
  const reopenTab = document.getElementById('pcpReopenTab');
  if (!panel) return;

  // Only show on steps 2–6 when a session is active
  const show = state.currentStep >= 2 && state.researchSession && !state._pcpCollapsed;
  panel.classList.toggle('visible', !!show);

  // Show re-open tab when collapsed
  if (reopenTab) {
    reopenTab.classList.toggle('hidden', !!show || state.currentStep < 2 || !state.researchSession);
  }

  if (!state.researchSession || state.currentStep < 2) return;

  // ── Render subject list ──
  _pcpRenderSubjectList();

  // ── Render mini-map ──
  _pcpRenderMiniMap();
}

/** Toggle collapse of the property context panel */
function togglePropertyContextPanel() {
  state._pcpCollapsed = !state._pcpCollapsed;
  const panel = document.getElementById('propertyContextPanel');
  const reopenTab = document.getElementById('pcpReopenTab');
  const isActive = !state._pcpCollapsed && state.currentStep >= 2 && !!state.researchSession;
  if (panel) panel.classList.toggle('visible', isActive);
  if (reopenTab) {
    reopenTab.classList.toggle('hidden', isActive || state.currentStep < 2 || !state.researchSession);
  }
}

// ── Subject list renderer ────────────────────────────────────────────────────
function _pcpRenderSubjectList() {
  const container = document.getElementById('pcpSubjects');
  if (!container) return;
  const subjects = state.researchSession?.subjects || [];

  // Count badge
  const countEl = document.getElementById('pcpSubjectCount');
  if (countEl) countEl.textContent = subjects.length;

  // Build subject rows
  let html = `<div class="pcp-subjects-header">
    <span class="pcp-subjects-title">Subjects</span>
    <span class="pcp-subject-count" id="pcpSubjectCount">${subjects.length}</span>
  </div>`;

  for (const s of subjects) {
    const isClient = s.type === 'client';
    const isComplete = s.deed_saved && s.plat_saved;
    const dotClass = isClient ? 'dot-client' : isComplete ? 'dot-complete' : 'dot-adjoiner';
    const rowClass = isClient ? 'pcp-client' : isComplete ? 'pcp-complete' : '';
    const nameClass = isClient ? 'pcp-client-name' : '';

    // Cardinal direction (from discovered adjoiners or ArcGIS spatial data)
    const dir = _pcpGetDirection(s);
    const dirTag = dir ? `<span class="pcp-direction-tag">${dir}</span>` : '';

    // Document status badges
    const deedBadge = s.deed_saved
      ? '<span class="pcp-doc-badge pcp-saved">📄 ✓</span>'
      : '<span class="pcp-doc-badge pcp-missing">📄 ✗</span>';
    const platBadge = s.plat_saved
      ? '<span class="pcp-doc-badge pcp-saved">📐 ✓</span>'
      : '<span class="pcp-doc-badge pcp-missing">📐 ✗</span>';

    html += `<div class="pcp-subject-row ${rowClass}">
      <span class="pcp-color-dot ${dotClass}"></span>
      <span class="pcp-subject-name ${nameClass}">${isClient ? '★ ' : ''}${escHtml(s.name)}</span>
      ${dirTag}
      <span class="pcp-doc-badges">${deedBadge}${platBadge}</span>
    </div>`;
  }

  container.innerHTML = html;
}

// ── Direction helper: compute cardinal direction of adjoiner relative to client ──
function _pcpGetDirection(subject) {
  if (subject.type === 'client') return '';
  const clientParcel = state.researchSession?.client_parcel || _propPicker?.confirmedParcel;
  if (!clientParcel?.centroid) return '';

  // Try to find adjoiner centroid from discovered adjoiners or KML data
  const adjData = (state.discoveredAdjoiners || []).find(
    d => d.name?.toLowerCase() === subject.name?.toLowerCase()
  );

  // Check if we have cached geometry centroid
  const adjUpc = subject.upc || adjData?.upc || '';
  const geom = state._pcpGeomCache?.[adjUpc];
  if (!geom?.centroid) {
    // Try to get centroid from KML GeoJSON
    if (_propPicker.geojsonData?.features) {
      const feature = _propPicker.geojsonData.features.find(f => {
        const fo = (f.properties?.owner || '').toLowerCase();
        return fo === subject.name?.toLowerCase();
      });
      if (feature?.properties?._centroid) {
        const [cLon, cLat] = clientParcel.centroid;
        const [aLon, aLat] = feature.properties._centroid;
        return _pcpCardinal(cLat, cLon, aLat, aLon);
      }
    }
    return '';
  }

  const [cLon, cLat] = clientParcel.centroid;
  const [aLon, aLat] = geom.centroid;
  return _pcpCardinal(cLat, cLon, aLat, aLon);
}

function _pcpCardinal(lat1, lon1, lat2, lon2) {
  const dLat = lat2 - lat1;
  const dLon = lon2 - lon1;
  const angle = Math.atan2(dLon, dLat) * 180 / Math.PI; // 0=N, 90=E
  if (angle >= -22.5 && angle < 22.5) return 'N';
  if (angle >= 22.5 && angle < 67.5) return 'NE';
  if (angle >= 67.5 && angle < 112.5) return 'E';
  if (angle >= 112.5 && angle < 157.5) return 'SE';
  if (angle >= 157.5 || angle < -157.5) return 'S';
  if (angle >= -157.5 && angle < -112.5) return 'SW';
  if (angle >= -112.5 && angle < -67.5) return 'W';
  if (angle >= -67.5 && angle < -22.5) return 'NW';
  return '';
}

// ── Mini-map renderer (Canvas) ──────────────────────────────────────────────

/**
 * Draws a schematic mini-map on the canvas showing client parcel (gold)
 * and adjoiner parcels (purple). Uses cached ArcGIS geometry or KML GeoJSON.
 */
function _pcpRenderMiniMap() {
  const canvas = document.getElementById('pcpMiniCanvas');
  const wrap = document.getElementById('pcpMinimapWrap');
  if (!canvas || !wrap) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width;
  const H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  // Collect all polygons to render
  const polys = []; // {rings, color, fillOpacity, isClient, label}

  const clientParcel = state.researchSession?.client_parcel || _propPicker?.confirmedParcel;
  const clientUpc = state.researchSession?.client_upc || clientParcel?.upc || '';

  // Try to load from cached geometry
  if (clientUpc && state._pcpGeomCache[clientUpc]?.rings) {
    polys.push({
      rings: state._pcpGeomCache[clientUpc].rings,
      color: '#e3c55a',
      fillColor: 'rgba(227,197,90,0.25)',
      label: '★',
      isClient: true
    });
  }

  // Add adjoiners
  const subjects = state.researchSession?.subjects || [];
  for (const s of subjects) {
    if (s.type === 'client') continue;
    const upc = s.upc || '';
    if (upc && state._pcpGeomCache[upc]?.rings) {
      const complete = s.deed_saved && s.plat_saved;
      polys.push({
        rings: state._pcpGeomCache[upc].rings,
        color: complete ? '#56d3a0' : '#b080e0',
        fillColor: complete ? 'rgba(86,211,160,0.15)' : 'rgba(176,128,224,0.15)',
        label: '',
        isClient: false
      });
    }
  }

  // If no geometry, try to build from KML GeoJSON (fallback)
  if (polys.length === 0 && _propPicker.geojsonData?.features) {
    _pcpBuildFromGeoJSON(polys, subjects, clientUpc);
  }

  // If still no geometry, show fallback
  if (polys.length === 0) {
    _pcpDrawNoGeometry(ctx, W, H);
    // Try to fetch geometry from ArcGIS if we have a UPC
    if (clientUpc && !state._pcpGeomCache[clientUpc]) {
      _pcpFetchGeometry(clientUpc).then(() => _pcpRenderMiniMap());
    }
    return;
  }

  // Compute bounding box across ALL polygons
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const p of polys) {
    for (const ring of p.rings) {
      for (const pt of ring) {
        if (pt[0] < minX) minX = pt[0];
        if (pt[0] > maxX) maxX = pt[0];
        if (pt[1] < minY) minY = pt[1];
        if (pt[1] > maxY) maxY = pt[1];
      }
    }
  }

  const dx = maxX - minX || 0.001;
  const dy = maxY - minY || 0.001;
  const pad = 20;
  const scaleX = (W - pad * 2) / dx;
  const scaleY = (H - pad * 2) / dy;
  const scale = Math.min(scaleX, scaleY);
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;

  // Transform: lon/lat → canvas pixel
  const toPixel = (lon, lat) => [
    W / 2 + (lon - cx) * scale,
    H / 2 - (lat - cy) * scale  // flip Y (lat increases up)
  ];

  // Draw dark background
  ctx.fillStyle = 'rgba(13, 17, 23, 0.9)';
  ctx.fillRect(0, 0, W, H);

  // Draw grid lines
  ctx.strokeStyle = 'rgba(100, 110, 120, 0.08)';
  ctx.lineWidth = 0.5;
  for (let gx = 0; gx <= W; gx += 40) {
    ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, H); ctx.stroke();
  }
  for (let gy = 0; gy <= H; gy += 40) {
    ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(W, gy); ctx.stroke();
  }

  // Draw polygons
  for (const p of polys) {
    for (const ring of p.rings) {
      ctx.beginPath();
      let first = true;
      for (const pt of ring) {
        const [px, py] = toPixel(pt[0], pt[1]);
        if (first) { ctx.moveTo(px, py); first = false; }
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.fillStyle = p.fillColor;
      ctx.fill();
      ctx.strokeStyle = p.color;
      ctx.lineWidth = p.isClient ? 2.5 : 1.5;
      ctx.stroke();
    }

    // Draw label at centroid
    if (p.label && p.rings[0]?.length) {
      let lx = 0, ly = 0;
      for (const pt of p.rings[0]) { lx += pt[0]; ly += pt[1]; }
      lx /= p.rings[0].length;
      ly /= p.rings[0].length;
      const [px, py] = toPixel(lx, ly);
      ctx.font = 'bold 16px Outfit, sans-serif';
      ctx.fillStyle = p.color;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(p.label, px, py);
    }
  }

  // Compass rose in top-right
  ctx.font = 'bold 10px JetBrains Mono, monospace';
  ctx.fillStyle = 'rgba(255,255,255,0.3)';
  ctx.textAlign = 'center';
  ctx.fillText('N', W - 16, 14);
  ctx.fillText('↑', W - 16, 24);
}

/** Build polygon data from KML GeoJSON (fallback when no ArcGIS cache) */
function _pcpBuildFromGeoJSON(polys, subjects, clientUpc) {
  const features = _propPicker.geojsonData?.features || [];
  const subjectNames = new Set(subjects.map(s => s.name?.toLowerCase()));

  for (const f of features) {
    const p = f.properties || {};
    const geom = f.geometry;
    if (!geom) continue;

    const owner = (p.owner || '').toLowerCase();
    const isClient = p.highlight || (p.upc && p.upc === clientUpc);
    const isAdjoiner = !isClient && subjectNames.has(owner);

    if (!isClient && !isAdjoiner) continue;

    let rings = [];
    if (geom.type === 'Polygon' && geom.coordinates) {
      rings = geom.coordinates;
    } else if (geom.type === 'MultiPolygon' && geom.coordinates) {
      rings = geom.coordinates[0] || [];
    } else {
      continue;
    }

    const subj = subjects.find(s => s.name?.toLowerCase() === owner);
    const complete = subj?.deed_saved && subj?.plat_saved;

    polys.push({
      rings: rings,
      color: isClient ? '#e3c55a' : complete ? '#56d3a0' : '#b080e0',
      fillColor: isClient ? 'rgba(227,197,90,0.25)'
        : complete ? 'rgba(86,211,160,0.15)' : 'rgba(176,128,224,0.15)',
      label: isClient ? '★' : '',
      isClient: isClient
    });
  }
}

/** Draw a "no geometry" placeholder on the canvas */
function _pcpDrawNoGeometry(ctx, W, H) {
  ctx.fillStyle = 'rgba(13, 17, 23, 0.9)';
  ctx.fillRect(0, 0, W, H);
  ctx.font = '28px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = 'rgba(255,255,255,0.15)';
  ctx.fillText('🗺️', W / 2, H / 2 - 12);
  ctx.font = '9px Inter, sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,0.25)';
  ctx.fillText('LOADING MAP…', W / 2, H / 2 + 16);
}

/**
 * Fetch parcel polygon geometry from ArcGIS by UPC and cache it for the mini-map.
 * @param {string} upc
 */
async function _pcpFetchGeometry(upc) {
  if (!upc || state._pcpGeomCache[upc]) return;
  try {
    const params = new URLSearchParams({
      where: `UPC='${upc}'`,
      outFields: 'UPC,OWNER',
      returnGeometry: 'true',
      outSR: '4326',
      f: 'json',
    });
    const url = `https://gis.ose.nm.gov/server_s/rest/services/Parcels/County_Parcels_2025/MapServer/29/query?${params}`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(10000) });
    const data = await resp.json();
    if (data.features?.[0]?.geometry?.rings) {
      const rings = data.features[0].geometry.rings;
      // Compute centroid
      let cx = 0, cy = 0, n = 0;
      for (const pt of rings[0]) { cx += pt[0]; cy += pt[1]; n++; }
      state._pcpGeomCache[upc] = {
        rings: rings,
        centroid: [cx / n, cy / n]
      };
    }
  } catch (e) {
    console.warn('[pcp] Geometry fetch failed for', upc, e.message);
  }
}

/**
 * Fetch geometry for all subjects that have UPCs but no cached geometry.
 * Called after session load / adjoiner discovery.
 */
async function _pcpFetchAllGeometry() {
  const subjects = state.researchSession?.subjects || [];
  const clientUpc = state.researchSession?.client_upc || '';
  const upcs = new Set();
  if (clientUpc) upcs.add(clientUpc);
  for (const s of subjects) {
    if (s.upc) upcs.add(s.upc);
  }
  // Also check discovered adjoiners
  for (const d of (state.discoveredAdjoiners || [])) {
    if (d.upc) upcs.add(d.upc);
  }

  const toFetch = [...upcs].filter(u => !state._pcpGeomCache[u]);
  if (!toFetch.length) return;

  // Fetch in parallel (max 8 concurrent)
  const batches = [];
  for (let i = 0; i < toFetch.length; i += 8) {
    batches.push(toFetch.slice(i, i + 8));
  }
  for (const batch of batches) {
    await Promise.allSettled(batch.map(u => _pcpFetchGeometry(u)));
  }
  // Re-render after all fetches
  renderPropertyContextPanel();
}

// 
// STEP 1: JOB SETUP
// 
async function startSession() {
  const numInput = document.getElementById("setupJobNum").value;
  const num = parseInt(numInput) || state.nextJobNum;
  const client = document.getElementById("setupClient").value.trim();
  const type = document.getElementById("setupJobType").value;

  if (!client) { showToast("Client name is required", "error"); return; }
  if (!num) { showToast("Job number is required", "error"); return; }

  const btn = document.getElementById("btnStartSession");
  btn.disabled = true;
  btn.innerHTML = "Loading...";

  try {
    const res = await apiFetch(`/research-session?job_number=${num}&client_name=${encodeURIComponent(client)}&job_type=${type}`);
    if (res.success) {
      state.researchSession = res.session;

      // Clear any stale deed/plat state from a previous session so Step 3
      // doesn't use the prior client's deed detail when searching for this client.
      state.selectedDoc = null;
      state.selectedDetail = null;
      state._kmlHits = null;
      state._cabinetHits = null;
      // Reset per-session automation flags
      state._step2Searched = false;
      state._adjDiscoveryRan = false;

      // If the user selected a property from the KML map in Step 1, pre-seed
      // the client_upc in the session. Steps 3 & 4 will use it for parcel matching.
      if (_propPicker.confirmedParcel) {
        state.researchSession.client_upc = _propPicker.confirmedParcel.upc || '';
        state.researchSession.client_parcel = _propPicker.confirmedParcel;
        // Pre-seed _kmlHits so Step 3 KML search already has the client's parcel
        state._kmlHits = [{
          owner: _propPicker.confirmedParcel.owner || client,
          upc: _propPicker.confirmedParcel.upc || '',
          plat: _propPicker.confirmedParcel.plat || '',
          book: _propPicker.confirmedParcel.book || '',
          page: _propPicker.confirmedParcel.page || '',
          cab_refs: _propPicker.confirmedParcel.cab_refs || [],
          cab_refs_str: (_propPicker.confirmedParcel.cab_refs || []).join(', '),
          centroid: _propPicker.confirmedParcel.centroid,
          match_reason: 'Selected from KML Map Picker',
          source: 'kml',
          local_files: [],
        }];
      }

      // Ensure client is in the subjects list
      let cSubj = state.researchSession.subjects.find(s => s.type === "client");
      if (!cSubj) {
        state.researchSession.subjects.unshift({
          id: "client_" + Date.now(),
          type: "client",
          name: client,
          deed_saved: false, plat_saved: false, status: "pending", notes: ""
        });
        await persistSession();
      }

      // ── Flush any adjoiners that were queued from the map picker ────────
      // The user may have picked adjoiners on the map BEFORE starting
      // the session. Now that the session exists, add them as subjects.
      if (_propPicker.mapAddedNames.length) {
        let flushed = 0;
        for (const entry of _propPicker.mapAddedNames) {
          let adjName = cleanOwnerName(typeof entry === 'object' ? entry.name : entry);
          const upc = typeof entry === 'object' ? (entry.upc || '') : '';
          // If the name is just a UPC code, try to resolve it
          if (isUpcCode(adjName) && upc) {
            const resolved = await resolveUpcToOwner(upc);
            if (resolved) adjName = resolved;
          }
          if (!adjName || adjName.length < 2) continue;
          const exists = state.researchSession.subjects.some(
            s => s.type === 'adjoiner' && s.name.toLowerCase() === adjName.toLowerCase()
          );
          if (!exists) {
            const plat = typeof entry === 'object' ? (entry.plat || '') : '';
            state.researchSession.subjects.push({
              id: 'adj_' + Date.now() + '_' + Math.random().toString(36).substr(2, 5),
              type: 'adjoiner',
              name: adjName,
              upc: upc,
              plat: plat,
              deed_saved: false, plat_saved: false, status: 'pending', notes: ''
            });
            flushed++;
          }
        }
        if (flushed > 0) {
          await persistSession();
          showToast(`✓ ${flushed} map-picked adjoiner${flushed > 1 ? 's' : ''} added to board`, 'success');
        }
      }

      updateJobContext();
      showToast(`Session loaded for Job #${num}`, "success");

      // Fire AI prediction in background (don't block navigation)
      _autoPredict(type, client).catch(() => {});

      // Save last session
      apiFetch("/config", "POST", {
        last_session: { job_number: num, client_name: client, job_type: type }
      }).catch(() => { });

      // Move to Step 2
      goToStep(2);

      // Fetch geometry for mini-map (background, non-blocking)
      _pcpFetchAllGeometry().catch(() => {});
    } else {
      showToast(res.error, "error");
    }
  } catch (e) {
    showToast("Failed to load session: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "Start Research Session &rarr;";
  }
}

async function loadRecentJobs() {
  const container = document.getElementById("setupRecentJobs");
  try {
    const res = await apiFetch("/recent-jobs");
    if (!res.success || !res.jobs.length) {
      container.innerHTML = `<div class="empty-state">No recent jobs found.</div>`;
      return;
    }

    let html = '';

    // Quick-resume banner for the last session
    if (state.lastSession && state.lastSession.job_number) {
      const ls = state.lastSession;
      html += `
        <div class="resume-banner" onclick="quickLoadJob(${ls.job_number}, '${escHtml(ls.client_name).replace(/'/g, "\\'")}','${ls.job_type}')">
          <div class="resume-banner-icon">⚡</div>
          <div class="resume-banner-info">
            <div class="resume-banner-title">Resume Last Session</div>
            <div class="resume-banner-meta">Job #${ls.job_number} — ${escHtml(ls.client_name)} <span class="resume-banner-type">${ls.job_type}</span></div>
          </div>
          <div class="resume-banner-arrow">→</div>
        </div>`;
    }

    html += res.jobs.map(j => `
      <div class="recent-job-row" onclick="quickLoadJob(${j.job_number}, '${escHtml(j.client_name).replace(/'/g, "\\'")}','${j.job_type}')">
        <div><strong class="text-accent2">#${j.job_number}</strong> &nbsp; ${escHtml(j.client_name)}</div>
        <div class="job-type">${j.job_type}</div>
      </div>
    `).join("");

    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = `<div class="text-danger">Failed to load recent jobs.</div>`;
  }
}

function quickLoadJob(num, client, type) {
  document.getElementById("setupJobNum").value = num;
  document.getElementById("setupClient").value = client;
  document.getElementById("setupJobType").value = type;
  startSession();
}

// ── AI Insights Panel ─────────────────────────────────────────────────────────
/** Safely set textContent on an element by ID */
function _setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

/**
 * Populate the AI Insights card on Step 1.
 * @param {boolean} [full=false] - true when user clicks Refresh (runs data-conflicts too)
 * Fires independent requests so one failure doesn't blank the panel.
 */
async function refreshAiInsights(full = false) {
  // Helper: apiFetch with a timeout
  function apiFetchTimeout(path, timeoutMs = 15000) {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), timeoutMs);
    return apiFetch(path, 'GET', null, { signal: ctrl.signal })
      .finally(() => clearTimeout(tid));
  }

  // ── Index Health ──────────────────────────────────────────────────────────
  apiFetchTimeout("/index-health").then(h => {
    if (!h.success) return;
    _setText("aiTotalParcels", h.total_parcels?.toLocaleString() ?? "0");
    _setText("aiArcgisPct", h.pct_with_arcgis != null ? h.pct_with_arcgis + "%" : "—");

    // Stale-index warning
    const staleRow = document.getElementById("aiStaleWarning");
    if (staleRow) {
      if (h.stale_warning || (h.newer_xml_files && h.newer_xml_files.length)) {
        staleRow.classList.remove("hidden");
        let msg = `Index is ${h.index_age_days} days old`;
        if (h.newer_xml_files && h.newer_xml_files.length) {
          msg += ` · ${h.newer_xml_files.length} XML file(s) newer than index`;
        }
        _setText("aiStaleText", msg);
      } else {
        staleRow.classList.add("hidden");
      }
    }
  }).catch(e => {
    if (e.name !== 'AbortError') console.warn("[AI Insights] index-health failed:", e);
  });

  // ── Data Conflicts — only on explicit Refresh (slow with 134K parcels) ───
  if (full) {
    _setText("aiConflictCount", "…");
    apiFetchTimeout("/data-conflicts?max_conflicts=0", 30000).then(c => {
      if (!c.success) { _setText("aiConflictCount", "—"); return; }
      // Exclude missing_enrichment (info-level) — show only real cross-source conflicts
      const total = (c.summary?.owner_mismatches || 0) +
                    (c.summary?.area_mismatches || 0) +
                    (c.summary?.trs_mismatches || 0);
      _setText("aiConflictCount", total.toLocaleString());
    }).catch(e => {
      _setText("aiConflictCount", "—");
      if (e.name !== 'AbortError') console.warn("[AI Insights] data-conflicts failed:", e);
    });
  }

  // ── Research Analytics ────────────────────────────────────────────────────
  apiFetchTimeout("/research-analytics").then(a => {
    if (!a.success) return;
    // API returns: { stats, predictions, scanned_jobs }
    _setText("aiJobsScanned", (a.scanned_jobs ?? a.stats?.total_jobs ?? 0).toLocaleString());

    // Complexity prediction (default BDY) — key is `predictions`
    const pred = a.predictions;
    if (pred) {
      const row = document.getElementById("aiPredictionRow");
      if (row) row.classList.remove("hidden");
      _setText("aiPredComplexity", pred.predicted_complexity || "moderate");
      const cpxEl = document.getElementById("aiPredComplexity");
      if (cpxEl) cpxEl.dataset.level = pred.predicted_complexity || "moderate";
      _setText("aiPredAdjoiners", pred.predicted_adjoiners ?? "—");
      const rangeEl = document.getElementById("aiPredRange");
      if (rangeEl && pred.adjoiner_range) {
        rangeEl.textContent = `(${pred.adjoiner_range.p25}–${pred.adjoiner_range.p75})`;
      }
      _setText("aiPredCabinets", (pred.likely_cabinets || []).join(", ") || "—");
      _setText("aiPredConfidence", pred.confidence || "—");
      _setText("aiPredSimilar", pred.similar_jobs_count ?? "0");
    }
  }).catch(e => {
    if (e.name !== 'AbortError') console.warn("[AI Insights] research-analytics failed:", e);
  });
}

async function persistSession() {
  if (!state.researchSession) return false;
  const { job_number, client_name, job_type } = state.researchSession;
  try {
    // Deep-clone the session, stripping any non-serializable data
    // (Leaflet layer refs, DOM elements, circular structures) that would
    // cause JSON.stringify to throw and silently kill the persist.
    const safeSession = _safeCloneSession(state.researchSession);
    await apiFetch("/research-session", "POST", {
      job_number, client_name, job_type,
      session: safeSession
    });
    updateGlobalProgress();
    updateFileBadges();
    renderPropertyContextPanel();  // refresh context panel on every save
    state._dirty = false;  // session saved successfully
    return true;
  } catch (e) {
    console.error("Session persist failed", e);
    showToast("⚠ Session save failed — check console", "error");
    return false;
  }
}

/**
 * Create a JSON-safe deep clone of the research session.
 * Strips any keys whose values are non-serializable (functions,
 * DOM elements, Leaflet layers, circular references).
 */
function _safeCloneSession(session) {
  try {
    return JSON.parse(JSON.stringify(session));
  } catch (_) {
    // Fallback: manually pick known-safe keys, preserving as much data as possible
    const safe = {
      job_number: session.job_number,
      client_name: session.client_name,
      job_type: session.job_type,
      subjects: (session.subjects || []).map(s => {
        const subj = {
          id: s.id,
          type: s.type,
          name: s.name,
          deed_saved: !!s.deed_saved,
          plat_saved: !!s.plat_saved,
          status: s.status || 'pending',
          notes: s.notes || '',
          deed_path: s.deed_path || '',
          plat_path: s.plat_path || '',
        };
        // Preserve deed detail & description if serializable
        if (s.detail) try { subj.detail = JSON.parse(JSON.stringify(s.detail)); } catch(_) {}
        if (s.description) subj.description = String(s.description);
        if (s.doc_no) subj.doc_no = s.doc_no;
        if (s.plat_refs) try { subj.plat_refs = JSON.parse(JSON.stringify(s.plat_refs)); } catch(_) {}
        return subj;
      }),
      client_upc: session.client_upc || '',
      progress: session.progress || {},
    };
    // Preserve chain-of-title data
    if (session.chain) try { safe.chain = JSON.parse(JSON.stringify(session.chain)); } catch(_) {}
    if (session.client_detail) try { safe.client_detail = JSON.parse(JSON.stringify(session.client_detail)); } catch(_) {}
    console.warn('[persistSession] Used fallback safe clone — session had non-serializable data');
    return safe;
  }
}
// 
// STEP 2: CLIENT DEED
// 
async function doStep2Search(sortBy) {
  const name = document.getElementById("s2SearchName").value.trim();
  const op = document.getElementById("s2SearchOp").value;
  const sort = sortBy || (document.getElementById("s2SortBy")?.value) || "relevance";

  if (!state.loggedIn) {
    showToast("Not connected to 1stNMTitle — click Settings to log in", "warn");
    await checkLogin();
    if (!state.loggedIn) return;
  }
  if (!name || name.length < 2) { showToast("Enter a longer name", "warn"); return; }

  const btn = document.getElementById("btnS2Search");
  const tbody = document.getElementById("s2ResultsBody");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner" style="width:12px;height:12px;display:inline-block;vertical-align:middle;margin-right:4px"></span>Searching…`;
  tbody.innerHTML = `<tr><td colspan="7" class="empty-cell"><div class="loading-state">Searching records for <strong>${escHtml(name)}</strong>…</div></td></tr>`;
  document.getElementById("s2ResultCount").textContent = "0";

  // Show TRS context bar if available
  const trsBar = document.getElementById("s2TrsContext");
  if (trsBar) trsBar.innerHTML = '';

  try {
    const signal = _getAbortSignal('step2search');

    // Use enriched search when we have client context
    const hasContext = state.researchSession && state.researchSession.client_upc;
    let res;
    if (hasContext) {
      // Gather adjoiner names from session
      const adjNames = (state.researchSession.subjects || [])
        .filter(s => s.type === 'adjoiner')
        .map(s => s.name);

      res = await apiFetch("/search-enriched", "POST", {
        name,
        operator: op,
        client_upc: state.researchSession.client_upc || '',
        client_name: state.researchSession.client_name || '',
        adjoiner_names: adjNames,
        sort_by: sort,
      }, { signal });
    } else {
      res = await apiFetch("/search", "POST", { name, operator: op }, { signal });
    }

    if (!res.success) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty-cell text-danger">Error: ${res.error}</td></tr>`;
      return;
    }

    if (!res.results.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty-cell text-text3">No records found for "${escHtml(name)}"</td></tr>`;
      return;
    }

    // Show TRS context if enriched
    if (trsBar && res.client_trs) {
      let ctx = `<span class="relevance-context-label">🏠 Client TRS:</span> <strong>${escHtml(res.client_trs)}</strong>`;
      if (res.client_subdivision) {
        ctx += ` &middot; <span class="relevance-context-label">📍 Subdivision:</span> <strong>${escHtml(res.client_subdivision)}</strong>`;
      }
      trsBar.innerHTML = `<div class="relevance-context-bar">${ctx}</div>`;
    }

    document.getElementById("s2ResultCount").textContent = res.results.length;
    state.searchResults = res.results;
    tbody.innerHTML = res.results.map((r, i) => {
      const tags = r.relevance_tags || [];
      const score = r.relevance_score || 0;
      const rowClass = score >= 40 ? 'result-row-high-relevance'
                     : score >= 20 ? 'result-row-medium-relevance'
                     : '';
      // Build relevance badges
      let badges = '';
      if (tags.includes('trs_match'))        badges += '<span class="relevance-badge badge-trs" title="Same TRS section as client">🏠</span>';
      if (tags.includes('same_subdivision')) badges += '<span class="relevance-badge badge-subdiv" title="Same subdivision">📍</span>';
      if (tags.includes('client_name'))      badges += '<span class="relevance-badge badge-client" title="Client name match">👤</span>';
      if (tags.includes('adjoiner'))         badges += '<span class="relevance-badge badge-adj" title="Adjoiner name match">🏘️</span>';

      return `
      <tr class="row-${getTypeClass(r.instrument_type)} ${rowClass}" onclick="loadS2Detail('${r.doc_no}', ${i}, this)">
        <td class="mono font-bold text-accent2">${r.doc_no || ''}</td>
        <td title="${escHtml(r.grantor || '')}">${escHtml((r.grantor || '').split(",")[0] || r.grantor || '')}</td>
        <td title="${escHtml(r.grantee || '')}" style="font-size:11px;color:var(--text2)">${escHtml((r.grantee || '').split(",")[0] || r.grantee || '')}</td>
        <td><span class="badge ${getTypeClass(r.instrument_type)}">${r.instrument_type || 'Deed'}</span></td>
        <td class="text-xs text-text3">${escHtml(r.location || '')}</td>
        <td class="text-xs text-text3">${(r.recorded_date || r.instrument_date || '').split("-")[0] || r.date || ''}</td>
        <td class="relevance-badges-cell">${badges}${score > 0 ? `<span class="relevance-score" title="Relevance score">${score}</span>` : ''}</td>
      </tr>`;
    }).join("");

    // Auto-select if only one result
    if (res.results.length === 1) {
      const onlyRow = tbody.querySelector('tr');
      if (onlyRow) {
        showToast(`1 record found — loading automatically`, 'info');
        setTimeout(() => loadS2Detail(res.results[0].doc_no, 0, onlyRow), 300);
      }
    }

  } catch (e) {
    if (e.name === 'AbortError') return;  // cancelled by newer search
    tbody.innerHTML = `<tr><td colspan="7" class="text-danger p-3">Search error: ${e.message}</td></tr>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = "Search";
  }
}

async function loadS2Detail(docNo, idx, trEl) {
  // Highlight row
  document.querySelectorAll("#s2ResultsBody tr").forEach(tr => tr.classList.remove("selected"));
  if (trEl) trEl.classList.add("selected");

  const container = document.getElementById("s2DetailContainer");
  container.innerHTML = `<div class="loading-state flex-col gap-2"><div class="spinner"></div> Loading ${docNo}...</div>`;

  try {
    // Set selectedDoc before fetching so we have the search row immediately
    state.selectedDoc = state.searchResults?.[idx] || { doc_no: docNo };

    // POST the search result so the backend can merge it into the detail
    const res = await apiFetch(`/document/${encodeURIComponent(docNo)}`, 'POST', {
      search_result: state.selectedDoc
    });
    if (!res.success) {
      container.innerHTML = `<div class="empty-state text-danger">Error: ${res.error}</div>`;
      return;
    }

    state.selectedDetail = res.detail;
    state._analysisLoaded = false; // reset so analysis tab re-fetches for new deed

    const d = res.detail;
    const extracted = extractDeedData(d, docNo, state.selectedDoc);
    const pdfUrl = d.pdf_url || '';

    container.innerHTML = `
      <div class="deed-viewer-header">
        <div style="display:flex;justify-content:space-between;align-items:flex-start">
          <div>
            <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:var(--text3);margin-bottom:4px">Document</div>
            <div style="font-size:22px;font-weight:800;font-family:'JetBrains Mono',monospace;color:#e3c55a;letter-spacing:-0.5px">${escHtml(docNo)}</div>
            <div style="font-size:12px;color:var(--text2);margin-top:3px">
              ${escHtml(extracted.instrumentType)} &nbsp;&middot;&nbsp; ${escHtml(extracted.recordedDate)}
            </div>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            ${pdfUrl ? `<a href="${escHtml(pdfUrl)}" target="_blank" class="btn btn-outline btn-sm">&#128279; View PDF</a>` : ''}
          </div>
        </div>
      </div>

      <div class="deed-viewer-tabs">
        <button class="deed-viewer-tab active" onclick="switchDeedTab('summary')" id="dtab-summary">&#128203; Summary</button>
        <button class="deed-viewer-tab" onclick="switchDeedTab('fields')" id="dtab-fields">&#128194; All Fields</button>
        ${pdfUrl ? `<button class="deed-viewer-tab" onclick="switchDeedTab('pdf')" id="dtab-pdf">&#128196; PDF</button>` : ''}
        <button class="deed-viewer-tab" onclick="switchDeedTab('analysis')" id="dtab-analysis">&#128269; Analysis</button>
      </div>

      <div class="deed-viewer-body" id="deedTabSummary">
        <div id="deedPlatHintArea"></div>
        <div id="deedPropertyDescArea"></div>
        ${buildDeedSummaryTab(extracted, d)}
      </div>

      <div class="deed-viewer-body hidden" id="deedTabFields">
        ${buildDeedAllFieldsTab(d)}
      </div>

      ${pdfUrl ? `
      <div class="deed-viewer-body hidden" id="deedTabPdf" style="display:flex;flex-direction:column;padding:0;min-height:420px">
        <div class="pdf-preview-bar">
          &#128196; <span style="font-family:monospace;color:var(--accent2)">${escHtml(docNo)}.pdf</span>
          <a href="${escHtml(pdfUrl)}" target="_blank" class="btn btn-outline btn-sm ml-auto">Open in Tab &#8599;</a>
        </div>
        <iframe src="${escHtml(pdfUrl)}" class="pdf-preview-frame" title="Deed PDF"></iframe>
      </div>` : ''}

      <div class="deed-viewer-body hidden" id="deedTabAnalysis" style="padding:0;overflow-y:auto">
        <div class="loading-state flex-col gap-2" id="analysisLoading">
          <div class="spinner"></div>
          Analyzing deed health...
        </div>
      </div>

      <div class="detail-actions">
        <button class="btn btn-primary flex-1" id="btnS2Save" onclick="saveClientDeed('${docNo}')">
          &#11015; Save Client Deed &rarr;</button>
        <button class="btn btn-accent2" onclick="downloadDeedToBrowser('${docNo}', state.selectedDetail)" title="Download this deed PDF to your own computer">
          ⬇ Download to My PC</button>
        <button class="btn btn-outline" onclick="extractPropertyDescription('${docNo}', '')" title="Extract property description from deed PDF">
          📜 Get Description</button>
        <button class="btn btn-outline" onclick="findSimilarDescriptions()" title="Find parcels with similar legal descriptions">
          🔍 Find Similar</button>
        <button class="btn btn-outline" onclick="runChainOfTitle()" title="Trace deed ownership backward">
          &#128279; Chain Back</button>
      </div>
    `;

    // Async: extract plat hints from deed in background — don't block UI
    extractPlatHintsFromDeed(res.detail);

    // Auto-extract property description from the online PDF
    extractPropertyDescription(docNo, '');

  } catch (e) {
    container.innerHTML = `<div class="empty-state text-danger">Error: ${e.message}</div>`;
  }
}

/** Extract well-known fields from deed detail object */

/**
 * Called immediately after a deed is loaded in Step 2.
 * Calls /find-plat to extract cabinet refs and plat hints from the deed text,
 * then injects a highlighted insight card at the top of the summary tab.
 */
async function extractPlatHintsFromDeed(detail) {
  const hintArea = document.getElementById('deedPlatHintArea');
  if (!hintArea) return;

  try {
    // Call the fast (zero I/O) deed parser
    const res = await apiFetch('/find-plat', 'POST', { detail });
    const cabRefs = (res && res.cabinet_refs) || [];

    // Also scan legal description client-side for any plat/book/page mentions
    const legalText = [
      detail['Legal Description'] || '',
      detail['Other Legal'] || '',
      detail['Legal'] || '',
      detail['Comments'] || '',
      detail['Remarks'] || '',
    ].join(' ');

    // Match patterns like: Cabinet C-191A, C-191-A, Book 5 Page 12, Plat Book, Cab. D-22
    const bookPageMatches = [...legalText.matchAll(/(?:book|bk)[.\s]*?(\d+)[\s,]*(?:page|pg)[.\s]*?(\d+[A-Z]?)/gi)]
      .map(m => `Book ${m[1]} Page ${m[2]}`);

    const platNameMatches = [...legalText.matchAll(/(?:plat\s+(?:of|entitled?|called?|named?)[:\s]+)([^,;\n]{4,60})/gi)]
      .map(m => m[1].trim());

    const surveyorRefs = [...legalText.matchAll(/(?:survey(?:ed)?\s+by|surveyor)[:\s]+([A-Z][a-zA-Z\s.&,]{3,50})/gi)]
      .map(m => m[1].trim());

    // Prior owners = grantor names from the deed (the plat may be filed under their name)
    const grantorRaw = detail['Grantor'] || state.selectedDoc?.grantor || '';
    // Split on common multi-grantor separators: " and ", " & ", ";"
    const priorOwners = grantorRaw
      .split(/\s*(?:;|\band\b|&)\s*/i)
      .map(n => n.trim())
      .filter(n => n.length > 2);

    // Store all hints in state for Step 3 to use
    state._platHint = {
      cabRefs,
      bookPageMatches: [...new Set(bookPageMatches)],
      platNameMatches: [...new Set(platNameMatches)],
      surveyorRefs: [...new Set(surveyorRefs)],
      priorOwners,
    };

    const hasAny = cabRefs.length || bookPageMatches.length || platNameMatches.length || priorOwners.length;
    if (!hasAny) {
      hintArea.innerHTML = `<div class="plat-hint-card plat-hint-none">
        <span style="opacity:.6;font-size:12px">&#128270; No plat references found directly in this deed.</span>
      </div>`;
      return;
    }

    // Build the hint card
    let rows = [];

    if (cabRefs.length) {
      rows.push(`<div class="plat-hint-row">
        <span class="plat-hint-label">&#128230; Cabinet Refs</span>
        <span class="plat-hint-values">${cabRefs.map(r =>
        `<span class="badge badge-local" style="cursor:pointer" onclick="jumpToPlat('${r.cabinet}','${r.doc}')" title="Search Cabinet ${r.cabinet} for ${r.doc}">` +
        `${escHtml('C-' + r.cabinet + '-' + r.doc)} <span style="opacity:.6;font-size:9px">▶ Plat</span></span>`
      ).join(' ')}</span>
      </div>`);
    }

    if (bookPageMatches.length) {
      rows.push(`<div class="plat-hint-row">
        <span class="plat-hint-label">&#128213; Book / Page</span>
        <span class="plat-hint-values">${bookPageMatches.map(b =>
        `<span class="badge badge-online">${escHtml(b)}</span>`
      ).join(' ')}</span>
      </div>`);
    }

    if (platNameMatches.length) {
      rows.push(`<div class="plat-hint-row">
        <span class="plat-hint-label">&#128196; Plat Name</span>
        <span class="plat-hint-values">${platNameMatches.map(p =>
        `<span class="badge" style="background:rgba(121,168,224,.15);color:#79a8e0">${escHtml(p)}</span>`
      ).join(' ')}</span>
      </div>`);
    }

    if (surveyorRefs.length) {
      rows.push(`<div class="plat-hint-row">
        <span class="plat-hint-label">&#9998; Surveyor</span>
        <span class="plat-hint-values">${surveyorRefs.map(s =>
        `<span class="badge" style="background:rgba(108,71,255,.15);color:#a78bfa">${escHtml(s)}</span>`
      ).join(' ')}</span>
      </div>`);
    }

    if (priorOwners.length) {
      rows.push(`<div class="plat-hint-row">
        <span class="plat-hint-label">&#128100; Prior Owners</span>
        <span class="plat-hint-values">${priorOwners.map(n =>
        `<span class="badge" style="background:rgba(227,197,90,.12);color:#e3c55a;cursor:pointer"
            onclick="jumpToPlatByOwner('${escHtml(n).replace(/'/g, '&#39;')}')"
            title="Search plat under prior owner: ${escHtml(n)}">` +
        `${escHtml(n.split(',')[0])} <span style="opacity:.5;font-size:9px">▶ Search</span></span>`
      ).join(' ')}</span>
      </div>`);
    }

    hintArea.innerHTML = `
      <div class="plat-hint-card">
        <div class="plat-hint-header">
          <span>&#128269; Plat Info Found in Deed</span>
          <button class="btn btn-success btn-sm" onclick="saveClientDeedAndGoToPlat()" style="font-size:11px;padding:3px 10px">
            Save &amp; Find Plat &rarr;
          </button>
        </div>
        ${rows.join('')}
      </div>`;

  } catch (e) {
    // Non-fatal — just don't show the hint
    console.warn('extractPlatHintsFromDeed failed:', e.message);
  }
}

/**
 * Save the client deed and immediately jump to Step 3 plat search.
 * Used by the "Save & Find Plat →" button in the plat hint card.
 */
async function saveClientDeedAndGoToPlat() {
  const detail = state.selectedDetail;
  const docNo = detail?.doc_no || state.selectedDoc?.doc_no;
  if (!docNo) { showToast('No deed selected', 'warn'); return; }
  await saveClientDeed(docNo);
  // saveClientDeed already calls goToStep(3) after 800ms — we're done
}

/**
 * Jump to plat search pre-targeted at a specific cabinet + doc from the deed.
 */
function jumpToPlat(cabinet, doc) {
  // Pre-set the cabinet override so Step 3 auto-targets it
  const sel = document.getElementById('s3CabinetSelect');
  if (sel) sel.value = cabinet;
  showToast(`Opening plat search — targeting Cabinet ${cabinet} for ${doc}`, 'info');
  goToStep(3);
}

/**
 * Jump to plat search using a prior owner name as the primary search token.
 * Sets state._platHint.activeOwnerSearch so doStep3Search picks it up.
 */
function jumpToPlatByOwner(ownerName) {
  if (!state._platHint) state._platHint = {};
  state._platHint.activeOwnerSearch = ownerName;
  showToast(`Searching plat under prior owner: ${ownerName.split(',')[0]}`, 'info');
  goToStep(3);
}

// ── BLM GLO Records URL builders ──────────────────────────────────────────
// Build a URL to the BLM General Land Office survey plat viewer.
// Input: TRS object { trs, township, range, section } or string "T26N R13E S12"
// Returns: URL to glorecords.blm.gov survey plat search, or null if unparseable.
function buildGloUrl(trsInput) {
  try {
    let twp, tDir, rng, rDir, sec;
    if (typeof trsInput === 'object' && trsInput !== null) {
      const tm = (trsInput.township || '').match(/T\.?(\d+)([NS])/i);
      const rm = (trsInput.range || '').match(/R\.?(\d+)([EW])/i);
      if (!tm || !rm) return null;
      twp = tm[1]; tDir = tm[2].toUpperCase();
      rng = rm[1]; rDir = rm[2].toUpperCase();
      sec = trsInput.section || '';
    } else {
      const m = String(trsInput).match(/T\.?\s*(\d+)\s*([NS])\s*R\.?\s*(\d+)\s*([EW])(?:\s*S(?:ec)?\s*(\d+))?/i);
      if (!m) return null;
      twp = m[1]; tDir = m[2].toUpperCase();
      rng = m[3]; rDir = m[4].toUpperCase();
      sec = m[5] || '';
    }
    // NM Principal Meridian
    const params = new URLSearchParams({
      state: 'NM', survey_type: 'RR',
      township: twp, township_dir: tDir, range: rng, range_dir: rDir,
    });
    if (sec) params.set('section', sec);
    return `https://glorecords.blm.gov/results/default.aspx?${params.toString()}#tabIndex=0&SurveyState=NM`;
  } catch { return null; }
}

// Build a URL to search GLO land patents by TRS.
function buildGloPatentUrl(trsInput) {
  try {
    let twp, tDir, rng, rDir, sec;
    if (typeof trsInput === 'object' && trsInput !== null) {
      const tm = (trsInput.township || '').match(/T\.?(\d+)([NS])/i);
      const rm = (trsInput.range || '').match(/R\.?(\d+)([EW])/i);
      if (!tm || !rm) return null;
      twp = tm[1]; tDir = tm[2].toUpperCase();
      rng = rm[1]; rDir = rm[2].toUpperCase();
      sec = trsInput.section || '';
    } else {
      const m = String(trsInput).match(/T\.?\s*(\d+)\s*([NS])\s*R\.?\s*(\d+)\s*([EW])(?:\s*S(?:ec)?\s*(\d+))?/i);
      if (!m) return null;
      twp = m[1]; tDir = m[2].toUpperCase();
      rng = m[3]; rDir = m[4].toUpperCase();
      sec = m[5] || '';
    }
    const params = new URLSearchParams({
      state: 'NM', searchType: 'Patent',
      township: twp, township_dir: tDir, range: rng, range_dir: rDir,
    });
    if (sec) params.set('section', sec);
    return `https://glorecords.blm.gov/results/default.aspx?${params.toString()}#tabIndex=1&SurveyState=NM`;
  } catch { return null; }
}

function extractDeedData(d, docNo, searchRow) {
  const docNumbers = [{ label: 'Doc #', value: docNo, type: 'docnum' }];
  ['Document Number', 'Document No', 'Instrument Number', 'Instrument No'].forEach(k => {
    if (d[k] && d[k] !== docNo) docNumbers.push({ label: k, value: d[k], type: 'docnum' });
  });
  ['GF Number', 'GF#', 'GF No', 'File Number'].forEach(k => {
    if (d[k]) docNumbers.push({ label: k, value: d[k], type: 'docnum' });
  });
  if (searchRow?.doc_no && searchRow.document_no && searchRow.document_no !== docNo)
    docNumbers.push({ label: 'Instrument No', value: searchRow.document_no, type: 'docnum' });
  if (searchRow?.gf_number) docNumbers.push({ label: 'GF#', value: searchRow.gf_number, type: 'docnum' });

  const locationSources = [];
  ['Location', 'Book/Page', 'Recorded Book', 'Reception No'].forEach(k => {
    if (d[k]) locationSources.push({ label: k, value: d[k], type: 'location' });
  });
  if (searchRow?.location && !locationSources.find(l => l.value === searchRow.location))
    locationSources.push({ label: 'Location', value: searchRow.location, type: 'location' });

  const partySeen = new Set();
  const parties = [];
  const addParty = (label, val) => {
    if (!val) return;
    const key = label + ':' + val;
    if (partySeen.has(key)) return;
    partySeen.add(key);
    parties.push({ label, value: val, type: 'person' });
  };
  addParty('Grantor', d['Grantor']);
  addParty('Grantee', d['Grantee']);
  if (searchRow) { addParty('Grantor', searchRow.grantor); addParty('Grantee', searchRow.grantee); }

  const dateSeen = new Set();
  const dates = [];
  const addDate = (label, val) => {
    if (!val || dateSeen.has(val)) return;
    dateSeen.add(val);
    dates.push({ label, value: val, type: 'date' });
  };
  ['Recorded Date', 'Record Date', 'Instrument Date', 'Filed Date'].forEach(k => addDate(k, d[k]));
  if (searchRow) { addDate('Recorded', searchRow.recorded_date); addDate('Instrument', searchRow.instrument_date); }

  const trsRefs = (d._trs || []).map(t => ({ label: 'TRS', value: t.trs, type: 'trs' }));
  // Build GLO link pills for each TRS reference
  const gloPills = (d._trs || []).map(t => {
    const url = buildGloUrl(t);
    const patentUrl = buildGloPatentUrl(t);
    return url ? { label: '📜 GLO Plat', value: t.trs, type: 'glo', url, patentUrl } : null;
  }).filter(Boolean);

  const money = [];
  ['Consideration', 'Amount', 'Sale Price', 'Value'].forEach(k => {
    if (d[k] && d[k] !== '$0' && d[k] !== '0') money.push({ label: k, value: d[k], type: 'money' });
  });

  const legalKeys = ['Legal Description', 'Other Legal', 'Other_Legal', 'Subdivision Legal', 'Subdivision_Legal', 'Legal', 'Section', 'Comments', 'Remarks'];
  const legalText = legalKeys.map(k => d[k]).filter(Boolean).join('\n\n');

  const instrumentType = searchRow?.instrument_type || d['Document Type'] || d['Type'] || d['Instrument Type'] || 'Deed';
  const recordedDate = searchRow?.recorded_date || d['Recorded Date'] || d['Record Date'] || d['Instrument Date'] || '';

  return { docNumbers, locationSources, parties, dates, trsRefs, gloPills, money, legalText, instrumentType, recordedDate };
}

function deedPill(item) {
  return `<div class="data-pill pill-${item.type}">
    <div class="data-pill-label">${escHtml(item.label)}</div>
    <div class="data-pill-value">${escHtml(item.value)}</div>
  </div>`;
}

function buildDeedSummaryTab(ex, d) {
  let html = '';
  if (ex.docNumbers.length) html += `<div class="extracted-section">
    <div class="extracted-section-title">&#128290; Document Numbers</div>
    <div class="data-pills">${ex.docNumbers.map(deedPill).join('')}</div>
  </div>`;

  if (ex.locationSources.length) html += `<div class="extracted-section">
    <div class="extracted-section-title">&#128205; Location (Book / Page)</div>
    <div class="data-pills">${ex.locationSources.map(deedPill).join('')}</div>
  </div>`;

  if (ex.parties.length) html += `<div class="extracted-section">
    <div class="extracted-section-title">&#128100; Parties</div>
    <div class="data-pills">${ex.parties.map(deedPill).join('')}</div>
  </div>`;

  if (ex.dates.length) html += `<div class="extracted-section">
    <div class="extracted-section-title">&#128197; Dates</div>
    <div class="data-pills">${ex.dates.map(deedPill).join('')}</div>
  </div>`;

  if (ex.trsRefs.length) html += `<div class="extracted-section">
    <div class="extracted-section-title">&#128506; Township / Range / Section</div>
    <div class="data-pills">${ex.trsRefs.map(deedPill).join('')}</div>
    ${ex.gloPills && ex.gloPills.length ? `<div class="data-pills" style="margin-top:6px">${ex.gloPills.map(p =>
      `<a href="${p.url}" target="_blank" rel="noopener" class="data-pill pill-glo" title="View original GLO survey plat for ${escHtml(p.value)}">
        <div class="data-pill-label">📜 Survey Plat</div>
        <div class="data-pill-value">${escHtml(p.value)} →</div>
      </a>
      <a href="${p.patentUrl}" target="_blank" rel="noopener" class="data-pill pill-glo-patent" title="Search GLO land patents for ${escHtml(p.value)}">
        <div class="data-pill-label">📰 Patents</div>
        <div class="data-pill-value">${escHtml(p.value)} →</div>
      </a>`
    ).join('')}</div>` : ''}
  </div>`;

  if (ex.money.length) html += `<div class="extracted-section">
    <div class="extracted-section-title">&#128176; Consideration</div>
    <div class="data-pills">${ex.money.map(deedPill).join('')}</div>
  </div>`;

  if (ex.legalText) html += `<div class="extracted-section">
    <div class="extracted-section-title">&#128212; Legal Description</div>
    <div class="legal-block">${escHtml(ex.legalText)}</div>
  </div>`;

  return html || `<div class="empty-state"><div class="empty-icon">&#128196;</div><p>No structured data found.<br>Check the All Fields tab.</p></div>`;
}

function buildDeedAllFieldsTab(d) {
  const skip = ['doc_no', '_trs', 'pdf_url'];
  let html = `<div class="detail-grid">`;
  Object.entries(d).forEach(([k, v]) => {
    if (skip.includes(k) || !v) return;
    const isLoc = k === 'Location';
    html += `<div class="detail-label">${escHtml(k)}</div>
      <div class="detail-val" style="${isLoc ? 'color:var(--accent2);font-family:monospace' : ''}">${escHtml(String(v))}</div>`;
  });
  if (d._trs && d._trs.length) {
    const gloLinks = d._trs.map(t => {
      const url = buildGloUrl(t);
      return url ? ` <a href="${url}" target="_blank" rel="noopener" style="color:#d4a44a;font-size:10px;text-decoration:none" title="View GLO survey plat">📜 Plat→</a>` : '';
    });
    html += `<div class="detail-label">TRS Refs</div>
      <div class="detail-val" style="color:#79a8e0;font-family:monospace">${d._trs.map((t, i) => escHtml(t.trs) + (gloLinks[i] || '')).join(' | ')}</div>`;
  }
  html += `</div>`;
  return html;
}

function switchDeedTab(name) {
  document.querySelectorAll('.deed-viewer-tab').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('dtab-' + name);
  if (btn) btn.classList.add('active');
  const map = { summary: 'deedTabSummary', fields: 'deedTabFields', pdf: 'deedTabPdf', analysis: 'deedTabAnalysis' };
  Object.entries(map).forEach(([n, id]) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (n === name) { el.classList.remove('hidden'); if (n === 'pdf') el.style.display = 'flex'; }
    else { el.classList.add('hidden'); if (n === 'pdf') el.style.display = ''; }
  });
  // Lazy-load analysis on first open
  if (name === 'analysis' && !state._analysisLoaded) {
    state._analysisLoaded = true;
    runDeedAnalysis();
  }
}

async function saveClientDeed(docNo) {
  const rs = state.researchSession;
  if (!rs) { showToast("No active session", "error"); return; }

  const clientSubj = rs.subjects.find(s => s.type === "client");
  if (!clientSubj) { showToast("Client subject disconnected", "error"); return; }

  const btn = document.getElementById("btnS2Save");
  if (btn) { btn.disabled = true; btn.innerHTML = "Saving..."; }

  try {
    const res = await apiFetch("/download", "POST", {
      doc_no: docNo,
      grantor: state.selectedDetail["Grantor"] || "",
      grantee: state.selectedDetail["Grantee"] || "",
      location: state.selectedDetail["Location"] || "",
      job_number: rs.job_number,
      client_name: rs.client_name,
      job_type: rs.job_type,
      create_project: true,
      is_adjoiner: false,
      subject_id: clientSubj.id,
    });

    if (res.success) {
      showToast(res.skipped ? "Client deed already exists (skipped)" : "Client deed saved!", "success");
      clientSubj.deed_saved = true;
      if (res.saved_to) clientSubj.deed_path = res.saved_to;
      // Store deed detail for reference table
      if (state.selectedDetail) clientSubj.detail = state.selectedDetail;
      if (docNo) clientSubj.doc_no = docNo;
      await persistSession();

      // Auto-open if configured
      if (!res.skipped && res.saved_to && document.getElementById("optAutoOpen")?.checked) {
        setTimeout(() => {
          apiFetch("/open-file", "POST", { path: res.saved_to }).catch(() => { });
        }, 500);
      }

      // ── Extract property description from the saved deed PDF ──
      extractPropertyDescription(docNo, res.saved_to);

      // ── Auto QA check in background ──
      _autoQaCheck();

      // Automatically move to Step 3
      setTimeout(() => goToStep(3), 800);
    } else {
      showToast("Save failed: " + res.error, "error");
    }
  } catch (e) {
    showToast("Error saving: " + e.message, "error");
  } finally {
    // Always re-enable the button so the user is never stuck
    if (btn) { btn.disabled = false; btn.innerHTML = "&#11015; Save Client Deed &rarr;"; }
  }
}

// ── PROPERTY DESCRIPTION EXTRACTION ─────────────────────────────────────────

/**
 * Extract the full property description from the deed PDF.
 * Called automatically after saving the client deed, or manually from the UI.
 * Fires in the background — doesn't block the UI.
 *
 * @param {string} docNo   - The document number
 * @param {string} pdfPath - Path to the saved PDF (optional — falls back to online download)
 */
async function extractPropertyDescription(docNo, pdfPath) {
  const descArea = document.getElementById('deedPropertyDescArea');

  // Show loading indicator in the deed panel if visible
  if (descArea) {
    descArea.innerHTML = `
      <div class="prop-desc-card prop-desc-loading">
        <div class="spinner" style="width:16px;height:16px"></div>
        <span>Extracting property description from deed PDF…</span>
      </div>`;
  }

  try {
    const res = await apiFetch('/extract-deed-description', 'POST', {
      pdf_path: pdfPath || '',
      detail: state.selectedDetail || {},
      doc_no: docNo || '',
    });

    if (!res.success) {
      console.warn('[desc] Extraction failed:', res.error);
      if (descArea) {
        descArea.innerHTML = `<div class="prop-desc-card prop-desc-error">
          <span>⚠ Could not extract property description: ${escHtml(res.error)}</span>
        </div>`;
      }
      return;
    }

    const desc = res.description;
    state._propertyDescription = desc;

    // Save to session for downstream use
    const rs = state.researchSession;
    if (rs) {
      const clientSubj = rs.subjects.find(s => s.type === 'client');
      if (clientSubj) {
        clientSubj.property_description = desc.legal_description || desc.full_text;
        clientSubj.desc_type = desc.desc_type;
        clientSubj.trs_refs = desc.trs_refs;
        clientSubj.area_acres = desc.area_acres;
        clientSubj.calls_count = desc.calls_count;
        persistSession(); // fire-and-forget
      }
    }

    // Render the property description card
    renderPropertyDescriptionCard(desc);

    // ── Silently index this description into the AI embeddings store ──
    // Builds up the /api/ai/similar database passively as deeds are opened.
    // Fire-and-forget — totally silent on failure.
    _indexDescriptionEmbedding(desc, docNo).catch(() => {});


    // Show a success toast
    const descTypeLabels = {
      metes_and_bounds: 'Metes & Bounds',
      lot_block: 'Lot/Block',
      tract: 'Tract',
      trs_only: 'TRS Only',
      unknown: 'General',
    };
    showToast(`📜 Property description acquired — ${descTypeLabels[desc.desc_type] || desc.desc_type}`, 'success');

  } catch (e) {
    console.warn('[desc] Extraction error:', e.message);
    if (descArea) {
      descArea.innerHTML = `<div class="prop-desc-card prop-desc-error">
        <span>⚠ Error extracting description: ${escHtml(e.message)}</span>
      </div>`;
    }
  }
}

/**
 * Render the property description card in the deed summary panel.
 */
function renderPropertyDescriptionCard(desc) {
  const descArea = document.getElementById('deedPropertyDescArea');
  if (!descArea) return;

  const descTypeLabels = {
    metes_and_bounds: 'Metes & Bounds',
    lot_block: 'Lot / Block',
    tract: 'Tract Reference',
    trs_only: 'TRS Only',
    unknown: 'General',
  };
  const descTypeIcons = {
    metes_and_bounds: '🧭',
    lot_block: '🏘️',
    tract: '📋',
    trs_only: '📍',
    unknown: '📄',
  };
  const sourceLabels = { text: 'PDF text layer', ocr: 'OCR scan', none: 'metadata only', paste: 'Manual paste' };

  const typeLabel = descTypeLabels[desc.desc_type] || desc.desc_type;
  const typeIcon = descTypeIcons[desc.desc_type] || '📄';
  const sourceLabel = sourceLabels[desc.source] || desc.source;

  let html = `<div class="prop-desc-card">`;

  // Header
  html += `<div class="prop-desc-header">
    <div class="prop-desc-title">
      <span class="prop-desc-icon">${typeIcon}</span>
      <span>Property Description</span>
      <span class="prop-desc-type-badge">${typeLabel}</span>
    </div>
    <div class="prop-desc-source">Source: ${sourceLabel}</div>
  </div>`;

  // Quick stats bar
  const stats = [];
  if (desc.calls_count) stats.push(`<span class="pds-stat"><span class="pds-icon">🧭</span>${desc.calls_count} calls</span>`);
  if (desc.area_acres) stats.push(`<span class="pds-stat"><span class="pds-icon">📐</span>${desc.area_acres} ac</span>`);
  if (desc.perimeter_ft) stats.push(`<span class="pds-stat"><span class="pds-icon">📏</span>${desc.perimeter_ft.toLocaleString()} ft</span>`);
  if (desc.pob_found) stats.push(`<span class="pds-stat pds-ok"><span class="pds-icon">✅</span>POB</span>`);
  if (desc.monuments?.length) stats.push(`<span class="pds-stat"><span class="pds-icon">📍</span>${desc.monuments.join(', ')}</span>`);
  if (desc.trs_refs?.length) stats.push(`<span class="pds-stat"><span class="pds-icon">🗺️</span>${desc.trs_refs.join(' · ')}</span>`);

  if (stats.length) {
    html += `<div class="prop-desc-stats">${stats.join('')}</div>`;
  }

  // Legal description text with color-coded bearing/distance cross-links
  const legalText = desc.legal_description || desc.full_text || '';
  if (legalText) {
    const segColors = [
      '#4fc3f7','#81c784','#ffb74d','#e57373','#ba68c8','#4dd0e1',
      '#aed581','#ff8a65','#f06292','#7986cb','#a1887f','#90a4ae'
    ];
    let displayText = escHtml(legalText);
    if (desc.calls?.length) {
      // Color-coded: match each call's bearing in text, wrap with interactive span
      let callIdx = 0;
      for (const call of desc.calls) {
        if (!call.bearing) { callIdx++; continue; }
        const color = segColors[callIdx % segColors.length];
        const bearingEsc = call.bearing.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s*');
        const bearingRe = new RegExp('(' + bearingEsc + ')', 'i');
        const match = displayText.match(bearingRe);
        if (match) {
          const idx = displayText.indexOf(match[0]);
          if (idx >= 0) {
            const afterBearing = displayText.substring(idx + match[0].length, idx + match[0].length + 80);
            const distMatch = afterBearing.match(/(\d+[\d,]*\.?\d*)\s*(?:feet|foot|ft|')/i);
            const bearingSpan = `<span class="bearing-highlight call-text-link" data-call-idx="${callIdx}" style="color:${color};border-bottom:2px solid ${color}" onmouseenter="highlightBoundarySegment(${callIdx})" onmouseleave="clearBoundaryHighlight()">${match[0]}</span>`;
            displayText = displayText.substring(0, idx) + bearingSpan + displayText.substring(idx + match[0].length);
            if (distMatch) {
              const distSpan = `<span class="distance-highlight call-text-link" data-call-idx="${callIdx}" style="color:${color}" onmouseenter="highlightBoundarySegment(${callIdx})" onmouseleave="clearBoundaryHighlight()">${distMatch[0]}</span>`;
              const insertPos = displayText.indexOf(distMatch[0], idx + bearingSpan.length);
              if (insertPos >= 0) {
                displayText = displayText.substring(0, insertPos) + distSpan + displayText.substring(insertPos + distMatch[0].length);
              }
            }
          }
        }
        callIdx++;
      }
    } else {
      displayText = displayText.replace(
        /([NS]\s*\d{1,3}[°\s]\d{0,2}[\'\s]\d{0,2}[\"\s]*[EW])/gi,
        '<span class="bearing-highlight">$1</span>'
      );
      displayText = displayText.replace(
        /(\d+\.?\d*)\s*(?:feet|foot|ft|&#39;)/gi,
        '<span class="distance-highlight">$1 ft</span>'
      );
    }

    html += `<div class="prop-desc-text-wrap">
      <div class="prop-desc-text" id="propDescText">${displayText}</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px">
        <button class="btn btn-outline btn-sm prop-desc-toggle" onclick="togglePropDescExpand()" id="propDescToggleBtn">
          Show Full Text ▾
        </button>
        <button class="btn btn-outline btn-sm" onclick="summarizeLegalDesc()"
          style="border-color:rgba(121,168,224,.3);color:#79a8e0;font-size:11px"
          title="Summarize this legal description with AI (requires Ollama)">
          ✨ AI Summarize
        </button>
        <button class="btn btn-outline btn-sm" onclick="aiSimilarDescriptions()"
          style="border-color:rgba(176,128,224,.3);color:#b080e0;font-size:11px"
          title="Find similar legal descriptions using AI embeddings">
          🔗 AI Similar
        </button>
      </div>
      <div id="legalDescSummary" class="hidden" style="margin-top:8px;padding:10px 12px;background:rgba(121,168,224,.06);border:1px solid rgba(121,168,224,.2);border-radius:8px;font-size:12px;color:var(--text2);line-height:1.6"></div>
      <div id="aiSimilarResults" class="hidden" style="margin-top:8px"></div>
    </div>`;
  } else {
    html += `<div class="prop-desc-empty">
      <div style="margin-bottom:8px">⚠ This PDF is a scanned image — no selectable text could be extracted automatically.</div>
      <div style="font-size:12px;color:var(--text3);margin-bottom:10px">
        <strong>Paste the deed text below</strong> (copy from your PDF viewer) and click "Extract" to parse the legal description, bearings, and property data.
      </div>
      <textarea id="manualDeedDescInput" class="inp" rows="6" style="font-size:12px;font-family:'JetBrains Mono',monospace;width:100%;resize:vertical"
        placeholder="Paste the full deed text here (including BEGINNING, thence, bearings, distances, etc.)..."></textarea>
      <div style="display:flex;gap:8px;margin-top:8px">
        <button class="btn btn-primary btn-sm flex-1" onclick="submitManualDeedDescription()">📜 Extract from Pasted Text</button>
      </div>
    </div>`;
  }

  // Adjoiners found
  if (desc.adjoiners?.length) {
    html += `<div class="prop-desc-adjoiners">
      <div class="prop-desc-adj-label">👥 Adjoiners Referenced:</div>
      <div class="prop-desc-adj-list">${desc.adjoiners.map(a =>
      `<span class="prop-desc-adj-chip">${escHtml(a)}</span>`
    ).join('')}</div>
    </div>`;
  }

  // Cabinet refs
  if (desc.cab_refs?.length) {
    html += `<div class="prop-desc-cab-refs">
      <div class="prop-desc-adj-label">🗄️ Cabinet References:</div>
      <div class="prop-desc-adj-list">${desc.cab_refs.map(r =>
      `<span class="prop-desc-cab-chip" onclick="jumpToPlat('${r.cabinet}','${r.doc}')" title="Search Cabinet ${r.cabinet}">
          C-${escHtml(r.cabinet)}-${escHtml(r.doc)} ▶
        </span>`
    ).join('')}</div>
    </div>`;
  }

  // GLO Records button (when TRS refs are available)
  if (desc.trs_refs?.length) {
    const trsStr = desc.trs_refs[0];  // Use first TRS ref
    const gloUrl = buildGloUrl(trsStr);
    const gloPatentUrl = buildGloPatentUrl(trsStr);
    if (gloUrl) {
      html += `<div class="prop-desc-glo-section" style="margin-top:10px;padding:10px 12px;background:rgba(212,164,74,.08);border:1px solid rgba(212,164,74,.25);border-radius:8px">
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#d4a44a;margin-bottom:6px">📜 BLM General Land Office Records</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <a href="${gloUrl}" target="_blank" rel="noopener" class="btn btn-outline btn-sm" style="font-size:11px;border-color:rgba(212,164,74,.4);color:#d4a44a;text-decoration:none">
            🗺️ View Original Survey Plat
          </a>
          <a href="${gloPatentUrl}" target="_blank" rel="noopener" class="btn btn-outline btn-sm" style="font-size:11px;border-color:rgba(212,164,74,.4);color:#d4a44a;text-decoration:none">
            📰 Search Land Patents
          </a>
        </div>
        <div style="font-size:10px;color:var(--text3);margin-top:4px">Original government survey records for ${escHtml(trsStr)} — Township plats, field notes, and land patents.</div>
      </div>`;
    }
  }

  // ── Boundary Plot + Calls Table (combined section) ───────────────────
  if (desc.calls?.length && desc.coords?.length > 1) {
    // Color palette for segments (12 alternating colors)
    const segColors = [
      '#4fc3f7','#81c784','#ffb74d','#e57373','#ba68c8','#4dd0e1',
      '#aed581','#ff8a65','#f06292','#7986cb','#a1887f','#90a4ae'
    ];

    html += `<div class="boundary-plot-section">
      <div class="boundary-plot-header">
        <div class="boundary-plot-title">
          <span class="boundary-plot-icon">🗺️</span>
          <span>Boundary Plot</span>
          <span class="boundary-plot-badge">${desc.calls.length} calls</span>
          ${desc.closure_err !== undefined ? `<span class="boundary-plot-closure ${desc.closure_err <= 1 ? 'closure-ok' : desc.closure_err <= 5 ? 'closure-warn' : 'closure-err'}">
            ${desc.closure_err <= 0.5 ? '✅' : desc.closure_err <= 5 ? '⚠️' : '❌'} Closure: ${desc.closure_err.toFixed(2)} ft${desc.closure_ratio ? ' (' + desc.closure_ratio + ')' : ''}
          </span>` : ''}
          <button class="btn btn-outline btn-sm" onclick="generateClosureReport()" style="margin-left:auto;font-size:10px;padding:3px 10px">📊 Closure Report</button>
        </div>
      </div>
      <div class="boundary-plot-body">
        <div class="boundary-plot-canvas" id="boundaryPlotCanvas"></div>
        <div class="boundary-plot-calls">
          <table class="data-table calls-table calls-table-editable" style="font-size:11px" id="boundaryCallsTable">
            <thead><tr><th>#</th><th>Bearing</th><th>Distance</th></tr></thead>
            <tbody>${desc.calls.map((c, i) =>
      `<tr data-call-idx="${i}" class="call-row${c.curve ? ' row-curve' : ''}" onclick="highlightBoundarySegment(${i})" style="cursor:pointer">
              <td class="mono text-text3"><span class="call-color-dot" style="background:${segColors[i % segColors.length]}"></span>${i + 1}</td>
              <td class="mono"><input type="text" class="call-edit-input call-bearing-input" data-idx="${i}" data-field="bearing" value="${escHtml(c.bearing)}" onchange="handleCallEdit(${i},'bearing',this.value)" onfocus="highlightBoundarySegment(${i})" spellcheck="false"></td>
              <td class="mono"><input type="text" class="call-edit-input call-dist-input" data-idx="${i}" data-field="distance" value="${c.distance}" onchange="handleCallEdit(${i},'distance',this.value)" onfocus="highlightBoundarySegment(${i})" spellcheck="false"><span class="call-unit-label">ft</span></td>
            </tr>`
    ).join('')}</tbody>
          </table>
          <div class="calls-edit-hint">✏️ Edit bearings or distances to see the plot update live</div>
        </div>
      </div>
    </div>`;
  } else if (desc.calls?.length) {
    // Fallback: calls table only (no coords for plotting)
    html += `<div class="prop-desc-calls-section">
      <button class="prop-desc-calls-toggle" onclick="togglePropDescCalls()">
        🧭 ${desc.calls.length} Bearing/Distance Calls
        <span id="propDescCallsChevron" class="cat-chevron">▸</span>
      </button>
      <div class="prop-desc-calls-table hidden" id="propDescCallsTable">
        <table class="data-table calls-table" style="font-size:11px">
          <thead><tr><th>#</th><th>Bearing</th><th>Distance</th></tr></thead>
          <tbody>${desc.calls.map((c, i) =>
      `<tr${c.curve ? ' class="row-curve"' : ''}>
              <td class="mono text-text3">${i + 1}</td>
              <td class="mono">${escHtml(c.bearing)}</td>
              <td class="mono text-accent2">${c.distance.toLocaleString()} ft</td>
            </tr>`
    ).join('')}</tbody>
        </table>
      </div>
    </div>`;
  }

  html += `</div>`;
  descArea.innerHTML = html;

  // Store desc for live editing
  state._boundaryDesc = desc;

  // ── Render the SVG boundary plot after DOM is ready ──────────────────
  if (desc.coords?.length > 1) {
    requestAnimationFrame(() => renderBoundaryPlot(desc));
  }
}

/** Toggle expand/collapse of the property description text */
function togglePropDescExpand() {
  const el = document.getElementById('propDescText');
  const btn = document.getElementById('propDescToggleBtn');
  if (!el) return;
  el.classList.toggle('expanded');
  if (btn) {
    btn.innerHTML = el.classList.contains('expanded') ? 'Collapse ▴' : 'Show Full Text ▾';
  }
}

/** Toggle expand/collapse of the calls table */
function togglePropDescCalls() {
  const el = document.getElementById('propDescCallsTable');
  const chev = document.getElementById('propDescCallsChevron');
  if (!el) return;
  el.classList.toggle('hidden');
  if (chev) chev.textContent = el.classList.contains('hidden') ? '▸' : '▾';
}

// ── INTERACTIVE SVG BOUNDARY PLOTTER ──────────────────────────────────────────

/**
 * Render an interactive SVG boundary plot from parsed deed coordinates.
 * Inspired by DeedReaderPro's visual plotting — rendered inline in the browser.
 */
function renderBoundaryPlot(desc) {
  const container = document.getElementById('boundaryPlotCanvas');
  if (!container || !desc.coords || desc.coords.length < 2) return;

  const coords = desc.coords;
  const calls = desc.calls || [];
  const segColors = [
    '#4fc3f7','#81c784','#ffb74d','#e57373','#ba68c8','#4dd0e1',
    '#aed581','#ff8a65','#f06292','#7986cb','#a1887f','#90a4ae'
  ];

  // ── Compute bounding box ──────────────────────────────────────────────
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const [x, y] of coords) {
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }

  const rangeX = maxX - minX || 1;
  const rangeY = maxY - minY || 1;
  const padding = 40;
  const svgW = container.clientWidth || 400;
  const svgH = Math.max(280, Math.min(svgW * 0.75, 500));
  const plotW = svgW - padding * 2;
  const plotH = svgH - padding * 2;
  const scale = Math.min(plotW / rangeX, plotH / rangeY);

  // Transform: survey coords (Y=North) to SVG coords (Y=down)
  const tx = (x) => padding + (x - minX) * scale + (plotW - rangeX * scale) / 2;
  const ty = (y) => padding + (maxY - y) * scale + (plotH - rangeY * scale) / 2;

  // ── Build SVG ─────────────────────────────────────────────────────────
  let svg = `<svg class="boundary-svg" viewBox="0 0 ${svgW} ${svgH}" width="${svgW}" height="${svgH}" xmlns="http://www.w3.org/2000/svg">`;

  // Defs: arrow marker, glow filter
  svg += `<defs>
    <marker id="plotArrow" markerWidth="6" markerHeight="4" refX="5" refY="2" orient="auto">
      <polygon points="0 0, 6 2, 0 4" fill="rgba(255,255,255,0.5)"/>
    </marker>
    <filter id="plotGlow">
      <feGaussianBlur stdDeviation="2" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>`;

  // ── Grid lines (subtle) ───────────────────────────────────────────────
  const gridStep = _niceGridStep(Math.max(rangeX, rangeY));
  if (gridStep > 0) {
    svg += `<g class="plot-grid">`;
    for (let gx = Math.ceil(minX / gridStep) * gridStep; gx <= maxX; gx += gridStep) {
      const sx = tx(gx);
      svg += `<line x1="${sx}" y1="${padding}" x2="${sx}" y2="${svgH - padding}" stroke="rgba(255,255,255,0.05)" stroke-width="0.5"/>`;
      svg += `<text x="${sx}" y="${svgH - padding + 12}" fill="rgba(255,255,255,0.15)" font-size="8" text-anchor="middle">${Math.round(gx)}'</text>`;
    }
    for (let gy = Math.ceil(minY / gridStep) * gridStep; gy <= maxY; gy += gridStep) {
      const sy = ty(gy);
      svg += `<line x1="${padding}" y1="${sy}" x2="${svgW - padding}" y2="${sy}" stroke="rgba(255,255,255,0.05)" stroke-width="0.5"/>`;
      svg += `<text x="${padding - 4}" y="${sy + 3}" fill="rgba(255,255,255,0.15)" font-size="8" text-anchor="end">${Math.round(gy)}'</text>`;
    }
    svg += `</g>`;
  }

  // ── Boundary segments ─────────────────────────────────────────────────
  for (let i = 0; i < coords.length - 1; i++) {
    const [x1, y1] = coords[i];
    const [x2, y2] = coords[i + 1];
    const color = segColors[i % segColors.length];
    const call = calls[i] || {};
    const tooltip = `${call.bearing || ''} ${call.distance ? call.distance.toLocaleString() : ''} ft`.trim();

    svg += `<line class="plot-segment" data-idx="${i}"
      x1="${tx(x1)}" y1="${ty(y1)}" x2="${tx(x2)}" y2="${ty(y2)}"
      stroke="${color}" stroke-width="2.5" stroke-linecap="round"
      marker-end="url(#plotArrow)"
      onclick="highlightBoundarySegment(${i})"
      style="cursor:pointer">
      <title>${escHtml(tooltip)}</title>
    </line>`;

    // Midpoint label — only show if segment is long enough on screen
    const mx = (tx(x1) + tx(x2)) / 2;
    const my = (ty(y1) + ty(y2)) / 2;
    const segLenPx = Math.hypot(tx(x2) - tx(x1), ty(y2) - ty(y1));

    if (segLenPx > 50) {
      const angle = Math.atan2(ty(y2) - ty(y1), tx(x2) - tx(x1)) * (180 / Math.PI);
      const rot = (angle > 90 || angle < -90) ? angle + 180 : angle;
      const perpX = -(ty(y2) - ty(y1)) / segLenPx * 10;
      const perpY = (tx(x2) - tx(x1)) / segLenPx * 10;

      svg += `<text class="plot-label" data-idx="${i}"
        x="${mx + perpX}" y="${my + perpY}"
        fill="${color}" font-size="8" text-anchor="middle"
        transform="rotate(${rot.toFixed(1)}, ${mx + perpX}, ${my + perpY})"
        opacity="0.8">${escHtml(call.bearing || '')}</text>`;
      svg += `<text class="plot-dist-label" data-idx="${i}"
        x="${mx - perpX}" y="${my - perpY}"
        fill="rgba(255,255,255,0.5)" font-size="7" text-anchor="middle"
        transform="rotate(${rot.toFixed(1)}, ${mx - perpX}, ${my - perpY})"
        opacity="0.7">${call.distance ? call.distance.toLocaleString() : ''}'</text>`;
    }
  }

  // ── Closure gap (red dashed) ──────────────────────────────────────────
  if (desc.closure_err && desc.closure_err > 0.1 && coords.length >= 3) {
    const [lx, ly] = coords[coords.length - 1];
    const [fx, fy] = coords[0];
    svg += `<line class="plot-closure-gap"
      x1="${tx(lx)}" y1="${ty(ly)}" x2="${tx(fx)}" y2="${ty(fy)}"
      stroke="#ff1744" stroke-width="1.5" stroke-dasharray="5 3" opacity="0.8">
      <title>Closure gap: ${desc.closure_err.toFixed(2)} ft</title>
    </line>`;
  }

  // ── Vertex dots ───────────────────────────────────────────────────────
  for (let i = 0; i < coords.length; i++) {
    const [x, y] = coords[i];
    const color = i === 0 ? '#00e676' : segColors[(i - 1) % segColors.length];
    const r = i === 0 ? 5 : 3;
    svg += `<circle cx="${tx(x)}" cy="${ty(y)}" r="${r}" fill="${color}" stroke="rgba(0,0,0,0.5)" stroke-width="1"
      ${i === 0 ? 'filter="url(#plotGlow)"' : ''}>
      <title>${i === 0 ? 'POB (Point of Beginning)' : 'Point ' + (i + 1)}</title>
    </circle>`;
  }

  // ── POB label ─────────────────────────────────────────────────────────
  if (coords.length > 0) {
    const [px, py] = coords[0];
    svg += `<text x="${tx(px) + 8}" y="${ty(py) - 8}"
      fill="#00e676" font-size="9" font-weight="700" filter="url(#plotGlow)">POB</text>`;
  }

  // ── North arrow ───────────────────────────────────────────────────────
  const naX = svgW - 25, naY = 30;
  svg += `<g transform="translate(${naX}, ${naY})">
    <line x1="0" y1="12" x2="0" y2="-12" stroke="rgba(255,255,255,0.4)" stroke-width="1.5"/>
    <polygon points="-4,0 0,-12 4,0" fill="rgba(255,255,255,0.4)"/>
    <text x="0" y="-16" fill="rgba(255,255,255,0.5)" font-size="9" text-anchor="middle" font-weight="700">N</text>
  </g>`;

  svg += `</svg>`;
  container.innerHTML = svg;
}

/**
 * Highlight a boundary segment, its table row, and its text span.
 */
function highlightBoundarySegment(idx) {
  // Clear previous highlights
  document.querySelectorAll('.plot-segment.active').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.call-row.active').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.call-text-link.call-text-active').forEach(el => el.classList.remove('call-text-active'));

  // SVG segment
  const seg = document.querySelector(`.plot-segment[data-idx="${idx}"]`);
  if (seg) {
    seg.classList.add('active');
    seg.setAttribute('stroke-width', '5');
    document.querySelectorAll('.plot-segment:not(.active)').forEach(el => el.setAttribute('stroke-width', '2.5'));
  }

  // Table row
  const row = document.querySelector(`.call-row[data-call-idx="${idx}"]`);
  if (row) {
    row.classList.add('active');
    row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  // Text spans in legal description
  document.querySelectorAll(`.call-text-link[data-call-idx="${idx}"]`).forEach(el => {
    el.classList.add('call-text-active');
  });

  // Dim non-active plot labels
  document.querySelectorAll('.plot-label, .plot-dist-label').forEach(el => {
    el.setAttribute('opacity', el.dataset.idx == idx ? '1' : '0.4');
  });
}

/** Clear all boundary cross-link highlights (called on mouseleave). */
function clearBoundaryHighlight() {
  document.querySelectorAll('.plot-segment.active').forEach(el => {
    el.classList.remove('active');
    el.setAttribute('stroke-width', '2.5');
  });
  document.querySelectorAll('.call-row.active').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.call-text-link.call-text-active').forEach(el => el.classList.remove('call-text-active'));
  document.querySelectorAll('.plot-label, .plot-dist-label').forEach(el => el.setAttribute('opacity', '0.8'));
}

/** Calculate a nice grid step size for the plot. */
function _niceGridStep(range) {
  if (range <= 0) return 0;
  const rough = range / 5;
  const pow10 = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / pow10;
  let nice;
  if (norm <= 1) nice = 1;
  else if (norm <= 2) nice = 2;
  else if (norm <= 5) nice = 5;
  else nice = 10;
  return nice * pow10;
}

// ── EDITABLE CALL TABLE — LIVE REPLOT ──────────────────────────────────────────

/** Parse a bearing label like "N 45°30'00\" E" into components. */
function _parseBearingLabel(str) {
  const m = str.match(/^\s*([NS])\s*(\d{1,3})[°\s]+(\d{0,2})['\u2032\s]*(\d{0,2})["\u2033\s]*([EW])\s*$/i);
  if (!m) return null;
  return { ns: m[1].toUpperCase(), deg: parseFloat(m[2])||0, min: parseFloat(m[3])||0, sec: parseFloat(m[4])||0, ew: m[5].toUpperCase() };
}

/** Convert quadrant bearing to azimuth. */
function _bearingToAzimuth(ns, deg, mn, sec, ew) {
  const dd = deg + mn / 60.0 + sec / 3600.0;
  if (ns === 'N' && ew === 'E') return dd;
  if (ns === 'S' && ew === 'E') return 180.0 - dd;
  if (ns === 'S' && ew === 'W') return 180.0 + dd;
  if (ns === 'N' && ew === 'W') return 360.0 - dd;
  return 0;
}

/** Recompute coordinates from calls (client-side, mirrors backend). */
function _callsToCoords(calls) {
  const pts = [[0, 0]];
  let x = 0, y = 0;
  for (const c of calls) {
    if (c._azimuth !== undefined && c.distance) {
      const az = c._azimuth * Math.PI / 180;
      x += c.distance * Math.sin(az);
      y += c.distance * Math.cos(az);
      pts.push([Math.round(x * 10000) / 10000, Math.round(y * 10000) / 10000]);
    }
  }
  return pts;
}

/** Handle an edit to a bearing or distance field in the calls table. */
function handleCallEdit(idx, field, value) {
  const desc = state._boundaryDesc;
  if (!desc || !desc.calls || !desc.calls[idx]) return;
  const call = desc.calls[idx];
  const origClosure = desc.closure_err || 0;

  if (field === 'bearing') {
    const parsed = _parseBearingLabel(value);
    if (!parsed) {
      const inp = document.querySelector(`.call-bearing-input[data-idx="${idx}"]`);
      if (inp) { inp.classList.add('call-edit-error'); setTimeout(() => inp.classList.remove('call-edit-error'), 1200); }
      return;
    }
    call.bearing = value.trim();
    call._azimuth = _bearingToAzimuth(parsed.ns, parsed.deg, parsed.min, parsed.sec, parsed.ew);
    call.azimuth = call._azimuth;
  } else if (field === 'distance') {
    const num = parseFloat(value.replace(/,/g, ''));
    if (isNaN(num) || num <= 0) {
      const inp = document.querySelector(`.call-dist-input[data-idx="${idx}"]`);
      if (inp) { inp.classList.add('call-edit-error'); setTimeout(() => inp.classList.remove('call-edit-error'), 1200); }
      return;
    }
    call.distance = num;
  }

  for (const c of desc.calls) {
    if (c._azimuth === undefined && c.bearing) {
      const p = _parseBearingLabel(c.bearing);
      if (p) c._azimuth = _bearingToAzimuth(p.ns, p.deg, p.min, p.sec, p.ew);
      else if (c.azimuth !== undefined) c._azimuth = c.azimuth;
    }
  }

  const newCoords = _callsToCoords(desc.calls);
  desc.coords = newCoords;
  if (newCoords.length >= 2) {
    const [fx,fy] = newCoords[0]; const [lx,ly] = newCoords[newCoords.length-1];
    desc.closure_err = Math.round(Math.hypot(lx-fx, ly-fy) * 10000) / 10000;
    const perimeter = desc.calls.reduce((s,c) => s + (c.distance||0), 0);
    desc.closure_ratio = desc.closure_err > 0.001 && perimeter > 0 ? `1:${Math.round(perimeter / desc.closure_err)}` : '';
  }

  renderBoundaryPlot(desc);

  const closureEl = document.querySelector('.boundary-plot-closure');
  if (closureEl && desc.closure_err !== undefined) {
    const err = desc.closure_err;
    const icon = err <= 0.5 ? '✅' : err <= 5 ? '⚠️' : '❌';
    const cls = err <= 1 ? 'closure-ok' : err <= 5 ? 'closure-warn' : 'closure-err';
    closureEl.className = `boundary-plot-closure ${cls}`;
    closureEl.innerHTML = `${icon} Closure: ${err.toFixed(2)} ft${desc.closure_ratio ? ' (' + desc.closure_ratio + ')' : ''}`;
    const delta = err - origClosure;
    if (Math.abs(delta) > 0.01) {
      closureEl.innerHTML += ` <span class="closure-delta ${delta < 0 ? 'delta-better' : 'delta-worse'}">${delta > 0 ? '+' : ''}${delta.toFixed(2)}</span>`;
    }
  }

  const inp = document.querySelector(`.call-edit-input[data-idx="${idx}"][data-field="${field}"]`);
  if (inp) inp.classList.add('call-edited');
  highlightBoundarySegment(idx);
}

// ── CLOSURE REPORT GENERATOR ──────────────────────────────────────────────────

/** Generate a printable Closure Report and open in a new window. */
function generateClosureReport() {
  const desc = state._boundaryDesc;
  if (!desc || !desc.calls?.length) { showToast('No boundary data available', 'warn'); return; }

  const rs = state.researchSession;
  const client = rs?.subjects?.find(s => s.type === 'client');
  const jobNum = rs?.job_number || 'N/A';
  const clientName = client?.name || 'Unknown';
  const dateStr = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
  const segColors = ['#4fc3f7','#81c784','#ffb74d','#e57373','#ba68c8','#4dd0e1','#aed581','#ff8a65','#f06292','#7986cb','#a1887f','#90a4ae'];

  const coords = desc.coords || [];
  let plotSvg = '';
  if (coords.length > 1) {
    let minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity;
    for (const [x,y] of coords) { if(x<minX)minX=x;if(x>maxX)maxX=x;if(y<minY)minY=y;if(y>maxY)maxY=y; }
    const rX=maxX-minX||1, rY=maxY-minY||1, pad=50, W=600, H=450;
    const sc=Math.min((W-pad*2)/rX,(H-pad*2)/rY);
    const tx=(x)=>pad+(x-minX)*sc+((W-pad*2)-rX*sc)/2;
    const ty=(y)=>pad+(maxY-y)*sc+((H-pad*2)-rY*sc)/2;
    plotSvg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="border:1px solid #ccc;background:#fafafa">`;
    for (let i=0;i<coords.length-1;i++) {
      const [x1,y1]=coords[i],[x2,y2]=coords[i+1];
      plotSvg+=`<line x1="${tx(x1)}" y1="${ty(y1)}" x2="${tx(x2)}" y2="${ty(y2)}" stroke="${segColors[i%segColors.length]}" stroke-width="2"/>`;
      plotSvg+=`<text x="${(tx(x1)+tx(x2))/2}" y="${(ty(y1)+ty(y2))/2-5}" fill="#333" font-size="7" text-anchor="middle" font-family="monospace">${i+1}</text>`;
    }
    if (desc.closure_err>0.1) { const [lx,ly]=coords[coords.length-1],[fx,fy]=coords[0]; plotSvg+=`<line x1="${tx(lx)}" y1="${ty(ly)}" x2="${tx(fx)}" y2="${ty(fy)}" stroke="red" stroke-width="1" stroke-dasharray="4 2"/>`; }
    for (let i=0;i<coords.length;i++) { const [x,y]=coords[i]; plotSvg+=`<circle cx="${tx(x)}" cy="${ty(y)}" r="${i===0?4:2.5}" fill="${i===0?'green':segColors[(i-1)%segColors.length]}" stroke="#333" stroke-width="0.5"/>`; }
    plotSvg+=`<text x="${tx(coords[0][0])+6}" y="${ty(coords[0][1])-6}" fill="green" font-size="9" font-weight="bold">POB</text></svg>`;
  }

  const callRows = desc.calls.map((c,i)=>`<tr><td style="text-align:center;color:${segColors[i%segColors.length]};font-weight:bold">${i+1}</td><td style="font-family:monospace">${c.bearing||'-'}</td><td style="font-family:monospace;text-align:right">${c.distance?.toLocaleString()||'-'}</td></tr>`).join('');
  const err = desc.closure_err||0;
  const quality = err<=0.5?'Excellent':err<=2?'Good':err<=5?'Fair':'Poor';
  const qColor = err<=0.5?'#228B22':err<=2?'#2E8B57':err<=5?'#B8860B':'#CC0000';
  const perimeter = desc.calls.reduce((s,c)=>s+(c.distance||0),0);

  const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Closure Report — ${escHtml(clientName)}</title>
<style>body{font-family:'Segoe UI',Arial,sans-serif;max-width:800px;margin:0 auto;padding:30px;color:#222}h1{font-size:20px;border-bottom:2px solid #333;padding-bottom:8px}
.sub{color:#666;font-size:12px;margin-bottom:20px}.meta{display:grid;grid-template-columns:1fr 1fr;gap:8px 30px;margin-bottom:20px;font-size:13px}
.ml{font-weight:700;color:#555;text-transform:uppercase;font-size:10px;letter-spacing:.5px}table{width:100%;border-collapse:collapse;margin:16px 0;font-size:12px}
th,td{padding:6px 10px;border:1px solid #ddd}th{background:#f5f5f5;font-size:10px;text-transform:uppercase}
.cb{padding:14px 18px;border-radius:8px;margin:16px 0}.pc{text-align:center;margin:16px 0}
.ft{margin-top:30px;padding-top:12px;border-top:1px solid #ddd;font-size:10px;color:#999;text-align:center}@media print{.np{display:none}}</style></head><body>
<h1>📊 Boundary Closure Report</h1><div class="sub">Generated ${dateStr} by Deed & Plat Helper</div>
<div class="meta"><div><span class="ml">Job #</span><br>${escHtml(String(jobNum))}</div><div><span class="ml">Client</span><br>${escHtml(clientName)}</div>
<div><span class="ml">Description Type</span><br>Metes & Bounds (${desc.calls.length} calls)</div><div><span class="ml">TRS</span><br>${desc.trs_refs?.join(', ')||'N/A'}</div></div>
<div class="cb" style="background:${qColor}15;border:1px solid ${qColor}40"><strong style="color:${qColor};font-size:15px">${quality} Closure</strong><br>
<span style="font-family:monospace;font-size:14px">Error: ${err.toFixed(4)} ft${desc.closure_ratio?' ('+desc.closure_ratio+')':''}</span><br>
<span style="font-size:12px;color:#666">Perimeter: ${perimeter.toLocaleString()} ft | Area: ${desc.area_acres||0} acres</span></div>
<div class="pc">${plotSvg}</div><h3 style="font-size:14px;margin-top:24px">Call Table</h3>
<table><thead><tr><th>#</th><th>Bearing</th><th>Distance (ft)</th></tr></thead><tbody>${callRows}</tbody>
<tfoot><tr><th colspan="2" style="text-align:right">Total Perimeter</th><th style="text-align:right;font-family:monospace">${perimeter.toLocaleString()} ft</th></tr></tfoot></table>
${desc.monuments?.length?`<p style="font-size:12px"><strong>Monuments:</strong> ${desc.monuments.join(', ')}</p>`:''}
<div class="ft">Deed & Plat Helper — Jore Ve USA Land Surveying<br>This report is for reference only and does not constitute a legal survey.</div>
<div class="np" style="text-align:center;margin-top:20px"><button onclick="window.print()" style="padding:10px 30px;font-size:14px;cursor:pointer;border-radius:6px;border:1px solid #ccc;background:#f8f8f8">🖨️ Print / Save as PDF</button></div>
</body></html>`;

  const w = window.open('', '_blank', 'width=850,height=1000');
  if (w) { w.document.write(html); w.document.close(); }
  else showToast('Popup blocked — allow popups for this site', 'warn');
}

/**
 * Handle manual paste of deed text when auto-extraction fails (scanned PDFs).
 * Sends the pasted text to the backend for parsing, then re-renders the
 * property description card with full structured data.
 */
async function submitManualDeedDescription() {
  const textarea = document.getElementById('manualDeedDescInput');
  if (!textarea) return;
  const text = textarea.value.trim();
  if (!text) { showToast('Paste the deed text first', 'warn'); return; }
  if (text.length < 30) { showToast('Text seems too short — paste the full deed description', 'warn'); return; }

  const descArea = document.getElementById('deedPropertyDescArea');
  if (descArea) {
    descArea.innerHTML = `
      <div class="prop-desc-card prop-desc-loading">
        <div class="spinner" style="width:16px;height:16px"></div>
        <span>Parsing pasted deed text…</span>
      </div>`;
  }

  try {
    // Send the pasted text as both the 'detail' fields and via a custom text field
    const detail = { ...(state.selectedDetail || {}), 'Legal Description': text };

    const res = await apiFetch('/extract-deed-description', 'POST', {
      pdf_path: '',
      detail: detail,
      doc_no: '',
    });

    if (!res.success) {
      showToast('Parse error: ' + res.error, 'error');
      if (descArea) descArea.innerHTML = `<div class="prop-desc-card prop-desc-error">⚠ ${escHtml(res.error)}</div>`;
      return;
    }

    const desc = res.description;

    // Override source to indicate manual paste
    desc.source = 'paste';

    // If the backend didn't isolate a legal description, use the full pasted text
    if (!desc.legal_description && !desc.full_text) {
      desc.legal_description = text;
      desc.full_text = text;
    }
    if (!desc.legal_description && desc.full_text) {
      desc.legal_description = desc.full_text;
    }

    state._propertyDescription = desc;

    // Save to session
    const rs = state.researchSession;
    if (rs) {
      const clientSubj = rs.subjects.find(s => s.type === 'client');
      if (clientSubj) {
        clientSubj.property_description = desc.legal_description || desc.full_text || text;
        clientSubj.desc_type = desc.desc_type;
        clientSubj.trs_refs = desc.trs_refs;
        clientSubj.area_acres = desc.area_acres;
        clientSubj.calls_count = desc.calls_count;
        persistSession();
      }
    }

    // Re-render the description card with real data
    renderPropertyDescriptionCard(desc);

    const descTypeLabels = {
      metes_and_bounds: 'Metes & Bounds',
      lot_block: 'Lot/Block',
      tract: 'Tract',
      trs_only: 'TRS Only',
      unknown: 'General',
    };
    showToast(`📜 Property description parsed — ${descTypeLabels[desc.desc_type] || desc.desc_type}${desc.calls_count ? `, ${desc.calls_count} bearing calls` : ''}`, 'success');

  } catch (e) {
    showToast('Error: ' + e.message, 'error');
    if (descArea) {
      descArea.innerHTML = `<div class="prop-desc-card prop-desc-error">⚠ ${escHtml(e.message)}</div>`;
    }
  }
}


/** Skip deed download and jump directly to the plat step */
function skipToStep3() {
  goToStep(3);
}

// ── DEED ANALYSIS ─────────────────────────────────────────────────────────

/**
 * Call the backend /api/analyze-deed endpoint and render the results
 * into the Analysis tab. Lazy-loaded on first tab click.
 */
async function runDeedAnalysis() {
  const container = document.getElementById('deedTabAnalysis');
  if (!container) return;

  container.innerHTML = `<div class="loading-state flex-col gap-2">
    <div class="spinner"></div>Analyzing deed health…
  </div>`;

  try {
    const detail = state.selectedDetail || {};
    const pdfPath = state.researchSession?.subjects?.find(s => s.type === 'client')?.deed_path || '';

    const res = await apiFetch('/analyze-deed', 'POST', {
      detail,
      pdf_path: pdfPath,
    });

    if (!res.success) {
      container.innerHTML = `<div class="empty-state text-danger">Analysis failed: ${escHtml(res.error)}</div>`;
      return;
    }

    state._analysisResult = res.analysis;
    container.innerHTML = buildDeedAnalysisHtml(res.analysis, pdfPath);

  } catch (e) {
    container.innerHTML = `<div class="empty-state text-danger">Error: ${escHtml(e.message)}</div>`;
  }
}

/**
 * Build the full HTML for the Analysis tab from the analysis result object.
 */
function buildDeedAnalysisHtml(a, pdfPath) {
  const score = a.score;
  const grade = a.grade; // 'good' | 'fair' | 'poor'
  const gradeLabel = grade === 'good' ? 'Healthy Deed' : grade === 'fair' ? 'Needs Attention' : 'Issues Found';

  // SVG ring gauge — start at 0 offset, animate after mount
  const radius = 38;
  const circ = 2 * Math.PI * radius;
  const targetOffset = circ * (1 - score / 100);

  // Count issues by severity
  const counts = { ok: 0, info: 0, warn: 0, critical: 0 };
  (a.issues || []).forEach(i => { counts[i.severity] = (counts[i.severity] || 0) + 1; });

  // Description type label
  const descLabels = {
    metes_and_bounds: 'Metes & Bounds',
    lot_block: 'Lot / Block',
    tract: 'Tract Reference',
    trs_only: 'TRS Only',
    unknown: 'Unknown',
  };
  const descLabel = descLabels[a.desc_type] || a.desc_type;

  let html = `<div class="analysis-tab">`;

  // ── Health Score Ring ────────────────────────────────────────────
  html += `<div class="health-score-area">
    <div class="health-ring-wrap">
      <svg class="health-ring-svg" viewBox="0 0 90 90">
        <circle class="health-ring-bg" cx="45" cy="45" r="${radius}"/>
        <circle class="health-ring-fill grade-${grade}" cx="45" cy="45" r="${radius}"
          id="healthRingArc"
          stroke-dasharray="${circ.toFixed(1)}"
          stroke-dashoffset="${circ.toFixed(1)}"/>
      </svg>
      <div class="health-ring-label">
        <div class="health-ring-num" id="healthScoreNum">0</div>
        <div class="health-ring-txt">/ 100</div>
      </div>
    </div>
    <div class="health-summary">
      <div class="health-grade-label grade-${grade}">${gradeLabel}</div>
      <div class="health-meta">
        ${counts.critical ? `<span class="health-meta-pill"><span class="hm-icon">❌</span> ${counts.critical} Critical</span>` : ''}
        ${counts.warn ? `<span class="health-meta-pill"><span class="hm-icon">⚠️</span> ${counts.warn} Warning${counts.warn > 1 ? 's' : ''}</span>` : ''}
        ${counts.info ? `<span class="health-meta-pill"><span class="hm-icon">ℹ️</span> ${counts.info} Info</span>` : ''}
        ${counts.ok ? `<span class="health-meta-pill"><span class="hm-icon">✅</span> ${counts.ok} Passed</span>` : ''}
        <span class="health-meta-pill"><span class="hm-icon">📜</span> ${descLabel}</span>
      </div>
      ${a.pdf_used ? `<div class="health-pdf-note">✓ Full PDF text analyzed (${a.pdf_source || 'text layer'})</div>` : pdfPath ? `<div class="health-pdf-note">📄 PDF saved — click Re-analyze for deeper scan</div>` : `<div class="health-pdf-note">💡 Save the deed PDF for deeper text analysis</div>`}
    </div>
  </div>`;

  // ── Quick Stats (only shown when we have useful data) ────────────────
  const closure = a.categories?.closure || {};
  const completeness = a.categories?.completeness || {};

  const statsItems = [];
  if (closure.desc_type === 'metes_and_bounds') {
    if (closure.area_acres) statsItems.push({ label: 'Area', value: `${closure.area_acres} ac`, icon: '📐' });
    if (closure.perimeter) statsItems.push({ label: 'Perimeter', value: `${closure.perimeter.toLocaleString()} ft`, icon: '📏' });
    if (closure.closure_err !== undefined) statsItems.push({ label: 'Closure', value: closure.closure_ratio || `${closure.closure_err} ft`, icon: closure.closure_err <= 1 ? '✅' : '⚠️' });
    if (closure.calls_count) statsItems.push({ label: 'Calls', value: `${closure.calls_count}`, icon: '🧭' });
  }
  if (closure.monuments?.length) statsItems.push({ label: 'Monuments', value: closure.monuments.join(', '), icon: '📍' });
  if (completeness.percent !== undefined) statsItems.push({ label: 'Completeness', value: `${completeness.percent}%`, icon: '📋' });

  if (statsItems.length) {
    html += `<div class="analysis-quick-stats">`;
    for (const s of statsItems) {
      html += `<div class="aq-stat">
        <div class="aq-stat-icon">${s.icon}</div>
        <div class="aq-stat-body">
          <div class="aq-stat-val">${escHtml(s.value)}</div>
          <div class="aq-stat-label">${escHtml(s.label)}</div>
        </div>
      </div>`;
    }
    html += `</div>`;
  }

  // ── Category Cards ───────────────────────────────────────────────
  html += `<div class="analysis-categories">`;

  const catOrder = ['closure', 'parties', 'legal', 'completeness', 'nm_specific'];
  for (const catKey of catOrder) {
    const cat = a.categories[catKey];
    if (!cat) continue;

    const catIssues = (a.issues || []).filter(i => i.category === catKey);
    const hasCritical = catIssues.some(i => i.severity === 'critical');
    const hasWarn = catIssues.some(i => i.severity === 'warn');
    const badgeClass = hasCritical ? 'cat-badge-err' : hasWarn ? 'cat-badge-warn' : 'cat-badge-ok';
    const badgeText = hasCritical ? '❌ Issues' : hasWarn ? '⚠ Warnings' : '✅ OK';

    // Extra detail for specific categories
    let extraBadge = '';
    if (catKey === 'closure' && cat.calls_count > 0) {
      extraBadge = `<span class="analysis-cat-badge cat-badge-ok" style="margin-right:4px">${cat.calls_count} calls</span>`;
    }
    if (catKey === 'completeness') {
      const pctClass = cat.percent >= 80 ? 'cat-badge-ok' : cat.percent >= 50 ? 'cat-badge-warn' : 'cat-badge-err';
      extraBadge = `<span class="analysis-cat-badge ${pctClass}" style="margin-right:4px">${cat.percent}%</span>`;
    }

    html += `<div class="analysis-cat-card">
      <div class="analysis-cat-header" onclick="toggleAnalysisCat(this)">
        <div class="analysis-cat-title">
          <span class="cat-icon">${cat.icon || '📋'}</span>
          ${escHtml(cat.title)}
        </div>
        <div style="display:flex;align-items:center;gap:6px">
          ${extraBadge}
          <span class="analysis-cat-badge ${badgeClass}">${badgeText}</span>
          <span class="cat-chevron">▾</span>
        </div>
      </div>
      <div class="analysis-cat-body">`;

    // Completeness progress bar
    if (catKey === 'completeness' && cat.passed !== undefined) {
      const pctColor = cat.percent >= 80 ? '#40c29f' : cat.percent >= 50 ? '#e3c55a' : '#da3633';
      html += `<div style="padding:10px 16px 6px;border-bottom:1px solid rgba(255,255,255,0.03)">
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text3);margin-bottom:5px">
          <span>${cat.passed} of ${cat.total} fields present</span>
          <span style="font-weight:700;color:${pctColor}">${cat.percent}%</span>
        </div>
        <div style="height:4px;background:rgba(255,255,255,0.06);border-radius:2px;overflow:hidden">
          <div style="width:${cat.percent}%;height:100%;background:${pctColor};border-radius:2px;transition:width 0.8s ease"></div>
        </div>
      </div>`;
    }

    // Render check items
    for (const iss of catIssues) {
      const sevIcon = iss.severity === 'ok' ? '✓' : iss.severity === 'info' ? 'ℹ' : iss.severity === 'warn' ? '!' : '✕';
      html += `<div class="analysis-check">
        <div class="check-icon sev-${iss.severity}">${sevIcon}</div>
        <div class="check-content">
          <div class="check-title">${escHtml(iss.title)}</div>
          ${iss.detail ? `<div class="check-detail">${escHtml(iss.detail)}</div>` : ''}
        </div>
      </div>`;
    }

    if (!catIssues.length) {
      html += `<div class="analysis-check"><div class="check-content"><div class="check-detail" style="font-style:italic">No checks for this category.</div></div></div>`;
    }

    html += `</div></div>`; // close body + card
  }

  html += `</div>`; // close categories grid

  // ── Re-analyze / Refresh ─────────────────────────────────────────
  html += `<div class="analysis-reanalyze">
    <span>Analysis based on ${a.pdf_used ? 'full PDF text' : 'online metadata'}</span>
    <button class="btn btn-outline btn-sm" onclick="state._analysisLoaded=false;runDeedAnalysis()">⟳ Re-analyze</button>
  </div>`;

  html += `</div>`; // close .analysis-tab

  // Animate the score ring after DOM update
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      const arc = document.getElementById('healthRingArc');
      const num = document.getElementById('healthScoreNum');
      if (arc) arc.style.strokeDashoffset = targetOffset.toFixed(1);
      // Animate number counting up
      if (num) {
        let cur = 0;
        const step = Math.max(1, Math.ceil(score / 40));
        const tick = () => {
          cur = Math.min(cur + step, score);
          num.textContent = cur;
          if (cur < score) requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      }
    });
  });

  return html;
}

/** Toggle expand/collapse on analysis category cards */
function toggleAnalysisCat(headerEl) {
  const body = headerEl.parentElement.querySelector('.analysis-cat-body');
  const chevron = headerEl.querySelector('.cat-chevron');
  if (!body) return;
  body.classList.toggle('hidden');
  if (chevron) chevron.textContent = body.classList.contains('hidden') ? '▸' : '▾';
}

// STEP 3: CLIENT PLAT
// ============================================================

/** Returns the user-selected cabinet letter from the Step 3 dropdown, or '' for Auto. */
function getS3CabinetOverride() {
  const sel = document.getElementById('s3CabinetSelect');
  return sel ? sel.value.trim().toUpperCase() : '';
}

/**
 * Called whenever the cabinet dropdown changes.
 * If a deed detail is already loaded, immediately re-run the local cabinet scan
 * so the user sees the results without having to click Refresh.
 */
async function onCabinetSelectChange(val) {
  if (!state.selectedDetail) return;  // nothing to search yet
  const locCards = document.getElementById('s3LocalPlats');
  const cabLabel = val ? `Cabinet ${val}` : 'auto-detected cabinet';
  locCards.innerHTML = `<div class="loading-state">Scanning ${cabLabel}...</div>`;

  const kmlHits = state._kmlHits || [];
  const override = val.toUpperCase();
  const forcedCabs = override ? [override] : null;

  try {
    // Rebuild cabinet refs from deed (zero I/O)
    let cabRefs = [];
    try {
      const fastRes = await apiFetch('/find-plat', 'POST', { detail: state.selectedDetail });
      cabRefs = (fastRes && fastRes.cabinet_refs) || [];
    } catch (e) { }

    const payload = {
      detail: state.selectedDetail,
      cabinet_refs: forcedCabs ? [] : cabRefs,
      kml_matches: forcedCabs ? [] : kmlHits,
      client_name: state.researchSession?.client_name || '',
    };
    if (forcedCabs) payload.forced_cabinets = forcedCabs;

    const res = await apiFetch('/find-plat-local', 'POST', payload);
    const localHits = (res && res.local) || [];
    const targetCabs = (res && res.target_cabinets) || [];
    const targetingReason = (res && res.targeting_reason) || '';
    const cabLabelRes = targetCabs.length && targetCabs.length < 6
      ? `Cabinet${targetCabs.length > 1 ? 's' : ''} ${targetCabs.join(', ')}`
      : 'all cabinets';

    if (!localHits.length) {
      locCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">' +
        '<div class="text-3xl mb-2">\uD83D\uDDC4\uFE0F</div>' +
        `No cabinet plats matched in ${cabLabelRes}.<br>` +
        (targetingReason ? `<span class="text-xs opacity-50">${escHtml(targetingReason)}</span><br><br>` : '<br>') +
        '<button class="btn btn-outline btn-sm" onclick="openGlobalCabinetBrowser()">Browse Cabinets Manually</button></div>';
    } else {
      state._cabinetHits = localHits;
      locCards.innerHTML = localHits.map((f, fi) => {
        const stratLabel = f.strategy === 'doc_number' ? '\u2605 Doc# Match'
          : f.strategy === 'kml_plat_name' ? '\u2605 Plat Name'
            : f.strategy === 'kml_cab_ref' ? '\u2605 KML Match'
              : f.strategy === 'deed_cab_ref' ? '\u2B50 Deed Ref'
                : f.strategy === 'client_name' ? '\u2B50 Client'
                  : f.strategy === 'name_match' ? 'Name Match'
                    : f.strategy === 'page_ref' ? 'Page Ref'
                      : (f.strategy || 'match');
        const isTop = f.strategy === 'doc_number' || f.strategy === 'kml_plat_name' || f.strategy === 'kml_cab_ref' || f.strategy === 'deed_cab_ref' || f.strategy === 'client_name';
        const docNumBadge = f.doc_number
          ? ' <span style="font-family:monospace;font-size:10px;opacity:.7">Doc# ' + escHtml(f.doc_number) + '</span>'
          : '';
        return '<div class="plat-item' + (isTop ? ' plat-item-client' : '') + '">' +
          '<div class="plat-info">' +
          '<span class="plat-name text-xs" title="' + escHtml(f.file) + '" style="font-size:13px;font-weight:600">' + escHtml(f.display_name || f.file) + '</span>' +
          '<span class="plat-meta">Cabinet ' + (f.cabinet || '') + ' \u00A0\u00B7\u00A0 ' + stratLabel + docNumBadge + '</span>' +
          '</div>' +
          '<div style="display:flex;gap:4px">' +
          (f.path ? '<button class="btn btn-outline btn-sm" style="font-size:10px;padding:3px 7px" ' +
            'onclick="event.stopPropagation(); showPlatPreview(\x27' + escHtml(f.path).replace(/'/g, "\\\\'") + '\x27,\x27' + escHtml(f.display_name || f.file).replace(/'/g, "\\\\'") + '\x27,\x27savePlatByIndex(' + fi + ')\x27)">\ud83d\udc41 Preview</button>' : '') +
          '<button class="btn btn-success btn-sm" onclick="savePlatByIndex(' + fi + ')">\u2B07 Save</button>' +
          '<button class="btn btn-sm" style="background:var(--accent2);color:#fff;font-size:10px" ' +
          'onclick="downloadLocalFileToBrowser(\'' + escHtml(f.path).replace(/'/g, "\\\\'") + '\')" ' +
          'title="Download to your computer">⬇ My PC</button>' +
          '</div>' +
          '</div>';
      }).join('');
      if (targetingReason) {
        locCards.insertAdjacentHTML('afterbegin',
          `<div class="text-xs text-text3 p-2 pb-0 opacity-60">\uD83C\uDFAF ${escHtml(targetingReason)}</div>`);
      }
    }
  } catch (e) {
    locCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">Cabinet scan failed: ' + escHtml(e.message) + '</div>';
  }
}

async function doStep3Search() {
  const locCards = document.getElementById('s3LocalPlats');
  const kmlCards = document.getElementById('s3KmlPlats');
  const onlCards = document.getElementById('s3OnlinePlats');

  const clientName = state.researchSession && state.researchSession.client_name
    ? state.researchSession.client_name : '';
    // If no deed was selected, still run a name-only search using client_name.
  // Show a soft warning banner, but don't block the search.
  const noDeed = !state.selectedDetail;
  const noDeedBanner = noDeed
    ? '<div class="text-xs p-2" style="background:rgba(227,197,90,.08);border-bottom:1px solid rgba(227,197,90,.2);color:var(--accent2)">' +
    '⚠ No deed selected — searching by client name only. Select a deed in Step 2 for more targeted results.</div>'
    : '';

  // Use an empty detail object when no deed is available so we can still call
  // the backend endpoints (they all handle an empty detail gracefully).
  const detail = state.selectedDetail || {};

  // Set all columns to loading state
  locCards.innerHTML = noDeedBanner + '<div class="loading-state">Identifying target cabinet...</div>';
  if (kmlCards) kmlCards.innerHTML = '<div class="loading-state">Querying KML parcel index...</div>';
  onlCards.innerHTML = '<div class="loading-state">Searching 1stnmtitle.com...</div>';

  // ── A: Instant deed parse (returns cabinet refs, zero I/O) ────────────────
  let cabRefs = [];
  if (!noDeed) {
    try {
      const fastRes = await apiFetch('/find-plat', 'POST', { detail });
      cabRefs = (fastRes && fastRes.cabinet_refs) || [];
    } catch (e) { /* ignore */ }
  }

  // Pull grantor/grantee from the deed detail (prefer detail, fall back to selectedDoc search row)
  const grantor = detail?.Grantor || detail?.grantor
    || state.selectedDoc?.grantor || '';
  const grantee = detail?.Grantee || detail?.grantee
    || state.selectedDoc?.grantee || '';

  // Prior owners from the deed hint (extracted when deed was loaded).
  // These are grantor names — the plat may be filed under any of these.
  const hint = state._platHint || {};
  const priorOwners = hint.priorOwners || (grantor ? [grantor] : []);
  // If the user clicked a specific prior owner, prioritise that name
  const activeOwner = hint.activeOwnerSearch || '';
  // Clear the one-shot override so it only applies to this search run
  if (hint.activeOwnerSearch) { hint.activeOwnerSearch = null; }

  // ── Online search — runs in parallel, uses client_name as primary ──────────
  apiFetch('/find-plat-online', 'POST', {
    detail,
    client_name: activeOwner || clientName,
    grantee,
    grantor: activeOwner || grantor,
    prior_owners: priorOwners,
  })
    .then(res => {
      const surveyHits = (res && res.online) || [];
      if (!surveyHits.length) {
        onlCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">' +
          '<div class="text-3xl mb-2">🌐</div>' +
          'No online survey records found for <strong>' + escHtml(activeOwner || clientName || 'this name') + '</strong>.<br><br>' +
          '<div style="display:flex;flex-direction:column;gap:6px;align-items:center">' +
          '<button class="btn btn-outline btn-sm" onclick="goToStep(2)" title="Go back and try a different search name">← Try Different Name</button>' +
          '<button class="btn btn-outline btn-sm" onclick="showPropertyPicker()" title="Open the map to visually identify the client parcel">🗺️ Use Map Picker</button>' +
          '</div></div>';
      } else {
        state._onlineSurveyHits = surveyHits;
        _renderOnlineSurveyHits(onlCards, surveyHits, '');
      }
    })
    .catch(() => {
      onlCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">Online search unavailable.</div>';
    });

  // ── KML → then chain Local (so kml_matches with cab_refs are passed) ──────
  // Always query KML index: with deed → full cross-reference; without → name-only search
  // Pass client_upc from map picker so the selected parcel always ranks first.
  const clientUpc = (state.researchSession && state.researchSession.client_upc) || '';
  const kmlPromise = apiFetch('/find-plat-kml', 'POST', { detail, client_name: clientName, client_upc: clientUpc })
    .then(res => {
      const kmlHits = (res && res.kml_matches) || [];
      if (!kmlCards) return kmlHits;
      if (!kmlHits.length) {
        const noIdxHint = '<span class="text-xs opacity-60">Use the \u{1F5FA}\uFE0F KML Index button to build the index from county data.</span>';
        const noMatchMsg = noDeed
          ? 'No KML parcels found for <strong>' + escHtml(clientName) + '</strong>.<br><br>' + noIdxHint
          : 'No parcel records found in KML index.<br><br>' + noIdxHint;
        kmlCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">' +
          '<div class="text-3xl mb-2">\u{1F5FA}\uFE0F</div>' + noMatchMsg + '</div>';
      } else {
        state._kmlHits = kmlHits;
        _renderKmlHits(kmlCards, kmlHits);
      }
      return kmlHits;
    })
    .catch(() => {
      if (kmlCards) kmlCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">KML index unavailable.</div>';
      return [];
    });

  kmlPromise.then(kmlHits => {
    // ── Local cabinet scan — fires AFTER KML resolves
    const cabinetOverride = getS3CabinetOverride();
    const reason = cabinetOverride
      ? `Scanning Cabinet ${cabinetOverride} (manual selection)...`
      : (kmlHits.length
        ? 'Targeting cabinet from KML...'
        : (cabRefs.length ? 'Targeting cabinet from deed text...'
          : (clientName ? `Scanning all cabinets for "${clientName}"...` : 'Scanning all cabinets...')));
    locCards.innerHTML = noDeedBanner + `<div class="loading-state">${reason}</div>`;

    const payload = {
      detail,
      cabinet_refs: cabinetOverride ? [] : cabRefs,
      kml_matches: cabinetOverride ? [] : kmlHits,
      client_name: activeOwner || clientName,
      grantor: activeOwner || grantor,
      grantee,
      prior_owners: priorOwners,   // also search under prior owner names
    };
    if (cabinetOverride) payload.forced_cabinets = [cabinetOverride];

    return apiFetch('/find-plat-local', 'POST', payload);
  })
    .then(res => {
      const localHits = (res && res.local) || [];
      const targetCabs = (res && res.target_cabinets) || [];
      const targetingReason = (res && res.targeting_reason) || '';
      const cabLabel = targetCabs.length && targetCabs.length < 6
        ? `Cabinet${targetCabs.length > 1 ? 's' : ''} ${targetCabs.join(', ')}`
        : 'all cabinets';

      if (!localHits.length) {
        locCards.innerHTML = noDeedBanner +
          '<div class="empty-state text-text3 text-sm p-4">' +
          '<div class="text-3xl mb-2">\u{1F5C4}\uFE0F</div>' +
          `No cabinet plats matched in ${cabLabel}.<br>` +
          (targetingReason ? `<span class="text-xs opacity-50">${escHtml(targetingReason)}</span><br><br>` : '<br>') +
          '<button class="btn btn-outline btn-sm" onclick="openGlobalCabinetBrowser()">Browse Cabinets Manually</button></div>';
      } else {
        state._cabinetHits = localHits;
        const hitRows = localHits.map((f, fi) => {
          const confidencePct = _calcPlatConfidence(f);
          const stratLabel = f.strategy === 'doc_number' ? '\u2605 Doc# Match'
            : f.strategy === 'kml_plat_name' ? '\u2605 Plat Name Match'
              : f.strategy === 'kml_cab_ref' ? '\u2605 KML Ref Match'
                : f.strategy === 'deed_cab_ref' ? '\u2B50 Deed Ref Match'
                  : f.strategy === 'client_name' ? '\u2B50 Client Match'
                    : f.strategy === 'prior_owner' ? '\uD83D\uDC64 Prior Owner'
                      : f.strategy === 'name_match' ? 'Name Match'
                        : f.strategy === 'page_ref' ? 'Page Ref'
                          : (f.strategy || 'match');
          const isTop = f.strategy === 'doc_number' || f.strategy === 'kml_plat_name' || f.strategy === 'kml_cab_ref' || f.strategy === 'deed_cab_ref' || f.strategy === 'client_name' || f.strategy === 'prior_owner';
          const docNumBadge = f.doc_number
            ? ' <span style="font-family:monospace;font-size:10px;opacity:.7">Doc# ' + escHtml(f.doc_number) + '</span>'
            : '';
          const previewBtn = f.path
            ? '<button class="btn btn-outline btn-sm" style="font-size:10px;padding:3px 7px" ' +
            'onclick="event.stopPropagation(); showPlatPreview(\'' + escHtml(f.path).replace(/'/g, "\\'") + '\',\'' + escHtml(f.display_name || f.file).replace(/'/g, "\\'") + '\',\'savePlatByIndex(' + fi + ')\')">👁 Preview</button>'
            : '';
          return '<div class="plat-item' + (isTop ? ' plat-item-client' : '') + '" style="gap:10px">' +
            _confidenceRingHtml(confidencePct) +
            '<div class="plat-info" style="flex:1">' +
            '<span class="plat-name text-xs" title="' + escHtml(f.file) + '" style="font-size:13px;font-weight:600">' + escHtml(f.display_name || f.file) + '</span>' +
            '<span class="plat-meta">Cabinet ' + (f.cabinet || '') + ' \u00A0\u00B7\u00A0 ' + stratLabel + docNumBadge + '</span>' +
            '</div>' +
            '<div style="display:flex;gap:6px;align-items:center">' +
            previewBtn +
            '<button class="btn btn-success btn-sm" onclick="savePlatByIndex(' + fi + ')">\u2B07 Save</button>' +
            '<button class="btn btn-sm" style="background:var(--accent2);color:#fff;font-size:10px" ' +
            'onclick="downloadLocalFileToBrowser(\'' + escHtml(f.path).replace(/'/g, "\\\\'") + '\')" ' +
            'title="Download to your computer">⬇ My PC</button>' +
            '</div>' +
            '</div>';
        }).join('');

        locCards.innerHTML = noDeedBanner + hitRows;

        if (targetingReason) {
          locCards.insertAdjacentHTML('afterbegin',
            `<div class="text-xs text-text3 p-2 pb-0 opacity-60">\u{1F3AF} ${escHtml(targetingReason)}</div>`);
        }
      }
    })
    .catch(() => {
      locCards.innerHTML = noDeedBanner +
        '<div class="empty-state text-text3 text-sm p-4">Cabinet scan unavailable.</div>';
    });
}


function _renderKmlHits(container, kmlHits, selectedIdx) {
  container.innerHTML = kmlHits.map((p, pi) => {
    const ct = p.centroid ? 'Lat: ' + p.centroid[1].toFixed(5) + ', Lng: ' + p.centroid[0].toFixed(5) : '';
    const isSelected = pi === selectedIdx;
    const saveBtns = (p.local_files && p.local_files.length)
      ? p.local_files.map((lf, lfi) =>
        '<button class="btn btn-success btn-sm" style="font-size:10px;padding:3px 7px;white-space:nowrap" ' +
        'onclick="saveKmlLocalFile(' + pi + ',' + lfi + ')" title="' + escHtml(lf.file) + '">' +
        '\u2B07 ' + escHtml(lf.cab_ref || (lf.cabinet + '-' + lf.doc)) + '</button>'
      ).join('')
      : '';
    const searchBtn =
      '<button class="btn btn-sm kml-search-cab-btn' + (isSelected ? ' kml-search-cab-active' : '') + '" ' +
      'onclick="searchCabinetFromKml(' + pi + ')" title="Search local cabinet for this parcel">' +
      '\uD83D\uDD0D Cabinet</button>';
    return '<div class="plat-item kml-parcel-item' + (isSelected ? ' kml-parcel-selected' : '') + '" id="kml-parcel-' + pi + '">' +
      '<div class="plat-info" style="flex:1">' +
      '<span class="plat-name" title="' + escHtml(ct) + '">' + escHtml(p.owner) + '</span>' +
      '<div class="kml-meta-row">' +
      (p.upc ? '<span class="kml-chip chip-upc">UPC: ' + escHtml(p.upc) + '</span>' : '') +
      (p.book ? '<span class="kml-chip chip-book">Bk/Pg: ' + escHtml(p.book) + '/' + escHtml(p.page) + '</span>' : '') +
      (p.cab_refs_str ? '<span class="kml-chip chip-cab">' + escHtml(p.cab_refs_str) + '</span>' : '') +
      '<span class="kml-chip chip-addr-placeholder" id="kml-addr-' + pi + '" style="cursor:pointer;background:rgba(78,205,196,.08);color:#4ecdc4;border-color:rgba(78,205,196,.2)" onclick="event.stopPropagation();_lookupKmlCardAddress(' + pi + ')" title="Click to look up property address">📍 Address</span>' +
      '</div>' +
      (p.match_reason ? '<span class="plat-meta text-xs" style="color:var(--accent2)">' + escHtml(p.match_reason) + '</span>' : '') +
      (p.plat ? '<span class="plat-meta text-xs" title="' + escHtml(p.plat) + '">' +
        escHtml(p.plat.substring(0, 60)) + (p.plat.length > 60 ? '\u2026' : '') + '</span>' : '') +
      '</div>' +
      '<div class="flex-col gap-1" style="min-width:100px;align-items:flex-end">' +
      searchBtn +
      (saveBtns ? '<div style="margin-top:3px;display:flex;flex-direction:column;gap:2px">' + saveBtns + '</div>' : '') +
      '</div>' +
      '</div>';
  }).join('');
}

// Strip cabinet ref prefix from a KML PLAT string to get the filename-searchable name.
// Mirrors backend _extract_plat_name_tokens().
// "C-191-A ADELA RAEL" → "ADELA RAEL"  |  "CAB C-84-B TORRES" → "TORRES"
function _extractPlatName(platStr) {
  if (!platStr) return '';
  return platStr
    .replace(/(?:CAB(?:INET)?\.?\s*)?[A-Fa-f]\s*-\s*\d{1,4}(?:-[A-Za-z])?\s*/i, '')
    .trim();
}

// ── Phase 1: show filter panel ────────────────────────────────────────────────
function searchCabinetFromKml(pi) {
  const kmlHits = state._kmlHits;
  if (!kmlHits || !kmlHits[pi]) { showToast('KML parcel not found', 'error'); return; }
  const p = kmlHits[pi];
  const kmlCards = document.getElementById('s3KmlPlats');
  const locCards = document.getElementById('s3LocalPlats');

  // Highlight the selected parcel in the KML column
  if (kmlCards) _renderKmlHits(kmlCards, kmlHits, pi);
  const selEl = document.getElementById('kml-parcel-' + pi);
  if (selEl) selEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  // Pre-fill values from this KML parcel
  const cabRef = p.cab_refs_str || '';   // e.g. "C-191-A" — used for folder targeting only
  const platHint = p.plat || '';   // full PLAT field (shown as tooltip)
  // Cabinet files are named after the ORIGINAL plat filer (e.g. "Adela Rael.pdf"),
  // NOT necessarily the current owner. The KML PLAT field contains the original name.
  // Priority: extracted plat name → current owner fallback.
  const platName = _extractPlatName(p.plat || '');
  const nameDefault = platName || p.owner || '';

  // Build filter panel in the local cabinet column
  locCards.innerHTML =
    '<div class="cab-filter-panel">' +

    // Header: show cabinet ref as context chip, name as the primary label
    '<div class="cab-filter-header">' +
    '<span class="cab-filter-title">\uD83D\uDD0D Cabinet Search' +
    (cabRef ? ' &nbsp;<span style="font-family:monospace;font-size:10px;' +
      'background:rgba(176,128,224,0.15);color:#b080e0;padding:1px 6px;' +
      'border-radius:6px;border:1px solid rgba(176,128,224,0.3)">' +
      escHtml(cabRef) + '</span>' : '') +
    '</span>' +
    '<span class="cab-filter-subtitle" title="' + escHtml(platHint) + '">' +
    escHtml(nameDefault || 'Unknown parcel') +
    '</span>' +
    '</div>' +

    // Fields
    '<div class="cab-filter-body">' +

    '<div class="cab-filter-row">' +
    '<label class="cab-filter-label" for="cabFilterRef">Cabinet Ref</label>' +
    '<input id="cabFilterRef" class="inp cab-filter-inp" ' +
    'value="' + escHtml(cabRef) + '" ' +
    'placeholder="e.g. C-191-A" />' +
    '<span class="cab-filter-hint">Targets the cabinet folder (C, B, etc.) — not used for filename matching</span>' +
    '</div>' +

    '<div class="cab-filter-row">' +
    '<label class="cab-filter-label" for="cabFilterName">Plat Name <span style="opacity:.5">(matched against filenames)</span></label>' +
    '<input id="cabFilterName" class="inp cab-filter-inp" ' +
    'value="' + escHtml(nameDefault) + '" ' +
    'placeholder="e.g. ADELA RAEL" />' +
    '<span class="cab-filter-hint">Original plat filer name — from KML PLAT field' +
    (p.owner && p.owner !== nameDefault ? ' &nbsp;&middot;&nbsp; Current owner: <em>' + escHtml(p.owner) + '</em>' : '') +
    '</span>' +
    '</div>' +

    '<div class="cab-filter-row" style="flex-direction:row;align-items:center;gap:8px;padding-top:2px">' +
    '<input type="checkbox" id="cabFilterAllCabs" style="accent-color:var(--accent2);width:14px;height:14px">' +
    '<label for="cabFilterAllCabs" style="font-size:12px;color:var(--text2);cursor:pointer">' +
    'Search all cabinets (ignore cabinet ref for targeting)' +
    '</label>' +
    '</div>' +

    '</div>' +

    // Actions
    '<div class="cab-filter-actions">' +
    '<button class="btn btn-primary flex-1" ' +
    'onclick="_executeKmlCabinetSearch(' + pi + ')">' +
    'Search Cabinet \u2192' +
    '</button>' +
    '<button class="btn btn-outline btn-sm" onclick="openGlobalCabinetBrowser()">' +
    'Browse Manually' +
    '</button>' +
    '</div>' +

    '</div>';
}

// ── Phase 2: run the search with filter panel values ─────────────────────────
async function _executeKmlCabinetSearch(pi) {
  const kmlHits = state._kmlHits;
  if (!kmlHits || !kmlHits[pi]) return;
  const p = kmlHits[pi];

  // Read filter panel values
  const cabRefInput = (document.getElementById('cabFilterRef') || {}).value || '';
  const nameInput = (document.getElementById('cabFilterName') || {}).value || '';
  const searchAllCabs = document.getElementById('cabFilterAllCabs')?.checked || false;
  const clientName = state.researchSession?.client_name || '';

  // Label for display: just the name being searched (cabinet ref shown separately in header)
  const searchName = nameInput.trim() || p.owner || '';
  const parcelLabel = searchName;
  const locCards = document.getElementById('s3LocalPlats');

  // Build a modified parcel object for the backend.
  // Cabinet ref is ONLY used to target the correct folder (letter = "C", "B", etc.).
  // The number/suffix (e.g. "191A") doesn't appear in cabinet filenames and is ignored.
  let cabRefs = [];
  if (cabRefInput.trim()) {
    const letter = cabRefInput.trim().match(/^([A-Fa-f])/i);
    if (letter) cabRefs = [letter[1].toUpperCase()];   // just "C", not "C-191A"
  } else {
    // Fall back to the original parcel's cab_refs, but strip to letters only
    cabRefs = (p.cab_refs || []).map(r => r.split('-')[0].toUpperCase()).filter(Boolean);
  }

  const overrideParcel = {
    ...p,
    cab_refs: cabRefs,
    cab_refs_str: cabRefs.join(', '),
    // If user left name blank, clear owner so backend doesn't name-match on it
    owner: nameInput.trim() || '',
  };

  locCards.innerHTML = '<div class="loading-state">Searching cabinet for <strong>' +
    escHtml(parcelLabel) + '</strong>\u2026</div>';

  try {
    const payload = {
      detail: state.selectedDetail,
      cabinet_refs: [],
      kml_matches: searchAllCabs ? [] : [overrideParcel],
      client_name: clientName,
    };
    // If the user typed a name override, pass it as grantor for name matching
    // and set name_override=true so the backend skips client_name token search
    // (prevents "Garza, Veronica" from flooding results when searching "Adela Rael")
    if (nameInput.trim()) {
      payload.grantor = nameInput.trim();
      payload.name_override = true;
    }

    const res = await apiFetch('/find-plat-local', 'POST', payload);
    const localHits = (res && res.local) || [];
    const targetCabs = (res && res.target_cabinets) || [];
    const targetingReason = (res && res.targeting_reason) || '';
    const cabLabel = targetCabs.length && targetCabs.length < 6
      ? 'Cabinet' + (targetCabs.length > 1 ? 's' : '') + ' ' + targetCabs.join(', ')
      : 'all cabinets';

    if (!localHits.length) {
      locCards.innerHTML =
        '<div class="empty-state text-text3 text-sm p-4">' +
        '<div class="text-3xl mb-2">\uD83D\uDDC4\uFE0F</div>' +
        'No cabinet plats matched in ' + cabLabel + ' for <strong>' + escHtml(parcelLabel) + '</strong>.<br>' +
        (targetingReason ? '<span class="text-xs opacity-50">' + escHtml(targetingReason) + '</span><br><br>' : '<br>') +
        '<button class="btn btn-outline btn-sm" style="margin-top:8px" ' +
        'onclick="searchCabinetFromKml(' + pi + ')">\u21A9 Adjust Filters</button>' +
        ' &nbsp; ' +
        '<button class="btn btn-outline btn-sm" onclick="openGlobalCabinetBrowser()">Browse Manually</button>' +
        '</div>';
    } else {
      state._cabinetHits = localHits;
      const adjLink =
        '<button class="btn btn-sm cab-filter-adj-btn" onclick="searchCabinetFromKml(' + pi + ')">' +
        '\u21A9 Adjust</button>';
      const header =
        '<div class="text-xs p-2 pb-1" style="display:flex;justify-content:space-between;align-items:center;' +
        'color:var(--accent2);border-bottom:1px solid var(--border)">' +
        '<span>\uD83D\uDD0D Results for: <strong>' + escHtml(parcelLabel) + '</strong></span>' +
        adjLink +
        '</div>';
      locCards.innerHTML = header + localHits.map((f, fi) => {
        const stratLabel = f.strategy === 'doc_number' ? '\u2605 Doc# Match'
          : f.strategy === 'kml_plat_name' ? '\u2605 Plat Name Match'
            : f.strategy === 'kml_cab_ref' ? '\u2605 KML Ref Match'
              : f.strategy === 'deed_cab_ref' ? '\u2B50 Deed Ref Match'
                : f.strategy === 'client_name' ? '\u2B50 Client Match'
                  : f.strategy === 'name_match' ? 'Name Match'
                    : f.strategy === 'page_ref' ? 'Page Ref'
                      : (f.strategy || 'match');
        const isTop = f.strategy === 'doc_number' || f.strategy === 'kml_plat_name' || f.strategy === 'kml_cab_ref' || f.strategy === 'deed_cab_ref' || f.strategy === 'client_name' || f.strategy === 'prior_owner';
        const docNumBadge = f.doc_number
          ? ' <span style="font-family:monospace;font-size:10px;opacity:.7">Doc# ' + escHtml(f.doc_number) + '</span>'
          : '';
        return '<div class="plat-item' + (isTop ? ' plat-item-client' : '') + '">' +
          '<div class="plat-info">' +
          '<span class="plat-name text-xs" title="' + escHtml(f.file) + '" style="font-size:13px;font-weight:600">' + escHtml(f.display_name || f.file) + '</span>' +
          '<span class="plat-meta">Cabinet ' + (f.cabinet || '') + ' \u00A0\u00B7\u00A0 ' + stratLabel + docNumBadge + '</span>' +
          '</div>' +
          '<div style="display:flex;gap:4px">' +
          '<button class="btn btn-success btn-sm" onclick="savePlatByIndex(' + fi + ')">\u2B07 Save</button>' +
          '<button class="btn btn-sm" style="background:var(--accent2);color:#fff;font-size:10px" ' +
          'onclick="downloadLocalFileToBrowser(\'' + escHtml(f.path).replace(/'/g, "\\\\'") + '\')" ' +
          'title="Download to your computer">⬇ My PC</button>' +
          '</div>' +
          '</div>';
      }).join('');

      if (targetingReason) {
        locCards.insertAdjacentHTML('afterbegin',
          '<div class="text-xs text-text3 p-2 pb-0 opacity-60">\uD83C\uDFAF ' + escHtml(targetingReason) + '</div>');
      }
    }
  } catch (e) {
    locCards.innerHTML = '<div class="empty-state text-text3 text-sm p-4">Cabinet scan failed: ' +
      escHtml(e.message) + '<br><br>' +
      '<button class="btn btn-outline btn-sm" onclick="searchCabinetFromKml(' + pi + ')">\u21A9 Adjust Filters</button>' +
      '</div>';
  }
}



async function saveKmlLocalFile(kmlIdx, fileIdx) {
  const rs = state.researchSession;
  if (!rs) { showToast('No active session', 'error'); return; }
  const p = state._kmlHits && state._kmlHits[kmlIdx];
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
        if (res.saved_to) clientSubj.plat_path = res.saved_to;
        await persistSession();
      }
      _prefetchAdjoinerDiscovery();  // Fire-and-forget: pre-scan adjoiners
      setTimeout(() => goToStep(4), 800);
    } else { showToast('Save failed: ' + res.error, 'error'); }
  } catch (e) { showToast('Error: ' + e.message, 'error'); }
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
        ? '<strong style="color:var(--accent2)">\u2705 Index Ready</strong> &nbsp;\u2014&nbsp; ' + (res.total || 0).toLocaleString() + ' parcels<br>' +
        '<span class="text-xs text-text3">Built: ' + (res.built_at || 'unknown') + ' &nbsp;\u00B7&nbsp; ' + (res.size_mb || '?') + ' MB</span>'
        : '<strong style="color:#ff7b72">\u26A0 No Index Yet</strong> \u2014 Build it below to enable KML parcel search.') +
      '</div>' +
      (res.exists && srcRows
        ? '<div class="mt-3"><div class="text-xs font-bold uppercase text-text3 mb-1">Indexed Sources</div>' +
        '<table class="data-table"><tbody>' + srcRows + '</tbody></table></div>' : '') +
      (fileRows
        ? '<div class="mt-3"><div class="text-xs font-bold uppercase text-text3 mb-1">KML / KMZ Files Available</div>' +
        '<table class="data-table"><tbody>' + fileRows + '</tbody></table></div>'
        : '<div class="empty-state text-sm mt-3">No KML/KMZ files found in Survey Data\\XML folder.</div>');
  } catch (e) {
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
    showToast('KML index built: ' + (res.total || 0).toLocaleString() + ' parcels in ' + res.elapsed_sec + 's', 'success');
    await showKmlIndexModal();
  } catch (e) {
    showToast('Build failed: ' + e.message, 'error');
    document.getElementById('kmlIndexBody').innerHTML = '<div class="text-danger p-3">Error: ' + e.message + '</div>';
  } finally {
    btn.disabled = false; btn.innerHTML = '\u26A1 Build / Rebuild Index';
  }
}


function savePlatByIndex(idx) {
  const f = state._cabinetHits && state._cabinetHits[idx];
  if (!f) { showToast('Plat not found', 'error'); return; }
  saveClientPlatLocal(f.path, f.file);
}

async function saveClientPlatLocal(filePath, filename) {
  const rs = state.researchSession;
  if (!rs) { showToast("No active session", "error"); return; }
  const clientSubj = rs.subjects.find(s => s.type === "client");

  try {
    const res = await apiFetch("/save-plat", "POST", {
      source: "local", file_path: filePath, filename: filename,
      job_number: rs.job_number, client_name: rs.client_name, job_type: rs.job_type,
      subject_id: clientSubj?.id
    });
    if (res.success) {
      showToast(`Plat saved to project: ${res.filename}`, "success");
      if (clientSubj) { clientSubj.plat_saved = true; if (res.saved_to) clientSubj.plat_path = res.saved_to; }
      await persistSession();
      _prefetchAdjoinerDiscovery();  // Fire-and-forget: pre-scan adjoiners
      goToStep(4);
    } else {
      showToast("Save failed: " + res.error, "error");
    }
  } catch (e) {
    showToast("Error saving plat: " + e.message, "error");
  }
}

async function saveClientPlatOnline(docNo, loc) {
  const rs = state.researchSession;
  if (!rs) { showToast("No active session", "error"); return; }

  const clientSubj = rs.subjects.find(s => s.type === "client");

  try {
    const res = await apiFetch("/save-plat", "POST", {
      source: "online", doc_no: docNo, location: loc,
      job_number: rs.job_number, client_name: rs.client_name, job_type: rs.job_type,
      subject_id: clientSubj.id
    });

    if (res.success) {
      showToast(res.skipped ? "Plat file already exists" : `Plat saved to project: ${res.filename}`, "success");
      clientSubj.plat_saved = true;
      if (res.saved_to) clientSubj.plat_path = res.saved_to;
      await persistSession();
      _prefetchAdjoinerDiscovery();  // Fire-and-forget: pre-scan adjoiners
      goToStep(4);
    } else {
      showToast("Download failed: " + res.error, "error");
    }
  } catch (e) {
    showToast("Error downloading plat: " + e.message, "error");
  }
}

// 
// STEP 4: ADJOINER DISCOVERY
// 
/**
 * Fire-and-forget: pre-scan adjoiners in the background after plat save.
 * Results are cached in state._prefetchedAdjoiners so Step 4 renders instantly.
 */
function _prefetchAdjoinerDiscovery() {
  const rs = state.researchSession;
  if (!rs) return;
  const clientSubj = rs.subjects.find(s => s.type === 'client');
  if (!clientSubj || !clientSubj.deed_saved) return;

  state._prefetchedAdjoiners = null;  // clear any stale cache
  state._adjDiscoveryRan = true;      // prevent redundant auto-run in updateStepUI

  apiFetch('/find-adjoiners', 'POST', {
    detail: state.selectedDetail || {},
    deed_path: clientSubj.deed_path || '',
    job_number: rs.job_number,
    client_name: rs.client_name,
    job_type: rs.job_type,
  })
    .then(res => {
      if (res.success) {
        state._prefetchedAdjoiners = res;
        console.log(`[prefetch] Adjoiner discovery cached: ${(res.adjoiners || []).length} found`);
      }
    })
    .catch(e => console.warn('[prefetch] Adjoiner discovery failed:', e.message));
}

async function runAdjoinerDiscovery(autoMode = false) {
  const rs = state.researchSession;
  if (!rs) { if (!autoMode) showToast("No active session", "error"); return; }

  const clientSubj = rs.subjects.find(s => s.type === "client");
  if (!clientSubj || !clientSubj.deed_saved) {
    if (!autoMode) showToast("Save the client deed first", "warn");
    return;
  }

  const btn = document.getElementById("btnDiscoverAdjoiners");
  const grid = document.getElementById("s4AdjoinerGrid");
  const resArea = document.getElementById("s4DiscoveryResults");
  const countEl = document.getElementById("s4CountText");

  if (btn) { btn.disabled = true; btn.innerHTML = `<div class="spinner"></div> Scanning...`; }
  if (resArea) resArea.classList.remove("hidden");
  if (grid) grid.innerHTML = `<div class="loading-state col-span-full">Running OCR on plat and scanning online records\u2026</div>`;
  if (countEl) countEl.textContent = "...";

  if (autoMode) showToast("\ud83d\udd0d Auto-running adjoiner discovery\u2026", "info");

  try {
    // Use prefetched results if available (fired during plat save)
    let res = state._prefetchedAdjoiners;
    if (res && res.success) {
      state._prefetchedAdjoiners = null;  // consume once
      if (autoMode) showToast('✓ Adjoiner scan ready (pre-fetched)', 'success');
    } else {
      const signal = _getAbortSignal('adjoiners');
      res = await apiFetch("/find-adjoiners", "POST", {
        detail: state.selectedDetail || {},
        deed_path: clientSubj.deed_path || "",
        job_number: rs.job_number,
        client_name: rs.client_name,
        client_upc: rs.client_upc || '',
        job_type: rs.job_type,
      }, { signal });
    }

    if (!res.success) throw new Error(res.error);

    state.discoveredAdjoiners = res.adjoiners || [];
    state.ocrRawText = res.ocr_text || "";
    state._dirty = true;

    const count = state.discoveredAdjoiners.length;
    if (countEl) countEl.textContent = count;

    if (!count) {
      const noDetailHint = !state.selectedDetail
        ? `<br><br><span class="text-xs" style="color:var(--text3)">&#128161; Tip: Go to <button class="link-btn" onclick="goToStep(2)">Step 2</button> and select the client deed to enable full-text scanning.</span>`
        : "";
      if (grid) grid.innerHTML = `<div class="empty-state col-span-full">No adjoiners found automatically. Add manually below.${noDetailHint}</div>`;
      if (autoMode) showToast("Discovery scan found no adjoiners — add manually or use map picker", "info");
    } else {
      let html = state.discoveredAdjoiners.map(j => `
        <div class="adjoiner-chip">
          <div class="flex-col">
            <span class="adjoiner-chip-name">${j.name}</span>
            <span class="source-tag text-text3">${j.source}</span>
          </div>
          <button class="btn btn-outline btn-sm" onclick="addFoundAdjoiner(\'${j.name.replace(/\'/g, "\\\'")}\')">+ Add</button>
        </div>
      `).join("");
      grid.innerHTML = html;
    }

  } catch (e) {
    if (e.name === 'AbortError') return;  // cancelled by newer scan
    if (grid) grid.innerHTML = `<div class="text-danger col-span-full p-4">Discovery failed: ${e.message}</div>`;
    if (!autoMode) showToast("Discovery failed: " + e.message, "error");
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = `⚡ Re-run Scan`; }
  }
}

// ── ArcGIS Spatial Adjoiner Discovery ─────────────────────────────────────────
/**
 * Uses ArcGIS spatial queries to find all parcels physically touching the
 * client's property. Far more reliable than text-based discovery because it
 * uses actual geometry (polygon intersection via esriSpatialRelTouches).
 */
async function runArcgisSpatialDiscovery() {
  const rs = state.researchSession;
  if (!rs) { showToast("Start a session first", "warn"); return; }

  const upc = rs.client_upc || (rs.client_parcel && rs.client_parcel.upc) || '';
  if (!upc) {
    showToast("No client UPC found — select a property on the map first (Step 1)", "warn");
    return;
  }

  const btn = document.getElementById("btnArcgisSpatial");
  const grid = document.getElementById("s4AdjoinerGrid");
  const resultsPanel = document.getElementById("s4DiscoveryResults");
  const countEl = document.getElementById("s4CountText");

  if (btn) { btn.disabled = true; btn.innerHTML = '🛰️ Searching…'; }
  if (resultsPanel) resultsPanel.classList.remove("hidden");
  if (grid) grid.innerHTML = '<div class="col-span-full text-center p-4"><div class="spinner" style="margin:0 auto"></div><p class="text-text3 mt-2">Querying ArcGIS for adjacent parcels…</p></div>';

  try {
    const res = await apiFetch('/arcgis-adjoiners', 'POST', {
      upc: upc,
      client_name: rs.client_name || '',
    });

    if (!res || !res.success) {
      const err = (res && res.error) || 'Unknown error';
      if (grid) grid.innerHTML = `<div class="text-danger col-span-full p-4">ArcGIS query failed: ${err}</div>`;
      showToast("ArcGIS spatial search failed: " + err, "error");
      return;
    }

    const adjoiners = res.adjoiners || [];
    const count = adjoiners.length;

    if (countEl) countEl.textContent = count;

    if (!count) {
      if (grid) grid.innerHTML = '<div class="empty-state col-span-full">No adjacent parcels found via ArcGIS. Try the map picker or manual entry.</div>';
      showToast("No adjacent parcels found", "info");
      return;
    }

    // Merge with existing discoveredAdjoiners (avoid duplicates)
    if (!state.discoveredAdjoiners) state.discoveredAdjoiners = [];
    for (const adj of adjoiners) {
      const exists = state.discoveredAdjoiners.some(
        d => d.name.toLowerCase() === adj.owner.toLowerCase()
      );
      if (!exists) {
        state.discoveredAdjoiners.push({
          name: adj.owner,
          source: '🛰️ ArcGIS Spatial',
          upc: adj.upc,
          land_area: adj.land_area,
          subdivision: adj.subdivision,
          address: adj.address,
          trs: adj.trs,
          legal: adj.legal,
        });
      }
    }

    // Render enhanced cards with metadata + cross-ref status
    const boardNames = new Map(
      (state.researchSession?.subjects || []).map(s => [s.name.toLowerCase(), s])
    );
    let html = adjoiners.map(j => {
      const safeName = (j.owner || '').replace(/'/g, "\\'");
      const chips = [];
      if (j.land_area) chips.push(`<span class="kml-chip chip-upc">${j.land_area} ac</span>`);
      if (j.subdivision) chips.push(`<span class="kml-chip chip-cab">${escHtml(j.subdivision)}</span>`);
      if (j.address) chips.push(`<span class="kml-chip chip-book">📍 ${escHtml(j.address)}</span>`);
      if (j.trs) chips.push(`<span class="kml-chip chip-upc">📐 ${escHtml(j.trs)}</span>`);

      // Cross-reference: is this person on the board? deed/plat status?
      const boardSubj = boardNames.get((j.owner || '').toLowerCase());
      let xrefBadges = '';
      if (boardSubj) {
        const isClient = boardSubj.type === 'client';
        xrefBadges += isClient
          ? '<span style="font-size:8px;font-weight:700;padding:1px 5px;border-radius:3px;background:rgba(227,197,90,.15);color:#e3c55a;margin-right:4px">★ CLIENT</span>'
          : '<span style="font-size:8px;font-weight:700;padding:1px 5px;border-radius:3px;background:rgba(176,128,224,.12);color:#b080e0;margin-right:4px">ON BOARD</span>';
        xrefBadges += boardSubj.deed_saved
          ? '<span class="pcp-doc-badge pcp-saved" style="font-size:8px">📄✓</span>'
          : '<span class="pcp-doc-badge pcp-missing" style="font-size:8px">📄✗</span>';
        xrefBadges += boardSubj.plat_saved
          ? '<span class="pcp-doc-badge pcp-saved" style="font-size:8px;margin-left:2px">📐✓</span>'
          : '<span class="pcp-doc-badge pcp-missing" style="font-size:8px;margin-left:2px">📐✗</span>';
      }

      const addBtn = boardSubj
        ? `<span style="font-size:10px;color:var(--text3);font-style:italic">Added</span>`
        : `<button class="btn btn-outline btn-sm" onclick="addFoundAdjoiner('${safeName}')">+ Add</button>`;

      return `
        <div class="adjoiner-chip arcgis-spatial-chip" style="${boardSubj ? 'border-left:2px solid ' + (boardSubj.type === 'client' ? '#e3c55a' : '#b080e0') : ''}">
          <div class="flex-col gap-1">
            <span class="adjoiner-chip-name">${escHtml(j.owner)} ${xrefBadges}</span>
            <span class="source-tag text-text3">🛰️ ArcGIS Spatial · UPC ${escHtml(j.upc)}</span>
            ${chips.length ? `<div class="kml-meta-row">${chips.join('')}</div>` : ''}
          </div>
          ${addBtn}
        </div>
      `;
    }).join('');
    if (grid) grid.innerHTML = html;

    showToast(`🛰️ Found ${count} adjacent parcels via ArcGIS`, "success");

    // Fetch geometry for mini-map context panel (background)
    _pcpFetchAllGeometry().catch(() => {});

  } catch (e) {
    if (grid) grid.innerHTML = `<div class="text-danger col-span-full p-4">ArcGIS error: ${e.message}</div>`;
    showToast("ArcGIS spatial search error: " + e.message, "error");
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '🛰️ ArcGIS Spatial'; }
  }
}

async function addFoundAdjoiner(name) {
  const rs = state.researchSession;
  if (!rs) {
    showToast("No active session — adjoiner not saved", "error");
    return false;
  }

  // Clean the name (strip trailing &, Or, etc.)
  let cleanName = cleanOwnerName(name);

  // Carry the UPC if we know it (from discoveredAdjoiners or ArcGIS spatial)
  const adjData = (state.discoveredAdjoiners || []).find(d => d.name.toLowerCase() === name.toLowerCase());
  const upc = (adjData && adjData.upc) || '';

  // If the name is just a UPC code, resolve it to the owner's name
  if (isUpcCode(cleanName) && upc) {
    const resolved = await resolveUpcToOwner(upc);
    if (resolved) cleanName = resolved;
    else cleanName = `UPC ${cleanName.trim()}`; // Fallback: clearly label as UPC
  } else if (isUpcCode(cleanName)) {
    // It's a bare number with no UPC mapping — try treating it as a UPC
    const resolved = await resolveUpcToOwner(cleanName.trim());
    if (resolved) {
      cleanName = resolved;
    } else {
      cleanName = `UPC ${cleanName.trim()}`;
    }
  }

  if (!cleanName || cleanName.length < 2) {
    showToast('Name too short to add', 'warn');
    return false;
  }

  const exists = rs.subjects.some(s => s.type === "adjoiner" && s.name.toLowerCase() === cleanName.toLowerCase());
  if (exists) { showToast("Already on board", "info"); return true; }

  rs.subjects.push({
    id: "adj_" + Date.now() + Math.random().toString(36).substr(2, 5),
    type: "adjoiner",
    name: cleanName,
    upc: upc,
    plat: (adjData && adjData.plat) || '',
    deed_saved: false, plat_saved: false, status: "pending", notes: ""
  });

  const ok = await persistSession();
  if (ok) {
    showToast(`Added ${cleanName} to research board`, "success");
    // Fetch geometry for mini-map (background)
    if (upc) _pcpFetchGeometry(upc).then(() => renderPropertyContextPanel()).catch(() => {});
  } else {
    // Roll back the in-memory push so state stays consistent
    rs.subjects.pop();
    showToast(`Failed to save ${cleanName} — not added`, "error");
  }
  return ok;
}

async function addAllDiscoveredToBoard() {
  await addAllAndContinue();
}

/**
 * One-click: add ALL discovered adjoiners to the board, then navigate to Step 5.
 * Safe to call even if no adjoiners were discovered (won't navigate if none on board).
 */
async function addAllAndContinue() {
  const rs = state.researchSession;
  if (!rs) { showToast("No active session", "warn"); return; }

  let added = 0;
  const discovered = state.discoveredAdjoiners || [];
  for (const j of discovered) {
    const cleanName = cleanOwnerName(j.name);
    if (!cleanName || cleanName.length < 2 || isUpcCode(cleanName)) continue;
    const exists = rs.subjects.some(s => s.type === "adjoiner" && s.name.toLowerCase() === cleanName.toLowerCase());
    if (!exists) {
      rs.subjects.push({
        id: "adj_" + Date.now() + "_" + Math.random().toString(36).substr(2, 5),
        type: "adjoiner",
        name: cleanName,
        upc: j.upc || '',
        plat: j.plat || '',
        deed_saved: false, plat_saved: false, status: "pending", notes: ""
      });
      added++;
    }
  }

  if (added > 0) {
    await persistSession();
    // Fetch geometry for mini-map (background)
    _pcpFetchAllGeometry().catch(() => {});
  }

  const totalAdj = rs.subjects.filter(s => s.type === "adjoiner").length;
  if (totalAdj === 0) {
    showToast("No adjoiners on board yet — add some above or pick from map", "warn");
    return;
  }

  showToast(
    added > 0 ? `✓ Added ${added} adjoiners — opening Research Board` : `Going to Research Board (adjoiners already on board)`,
    "success"
  );
  setTimeout(() => goToStep(5), 300);
}

/**
 * Skip deed research and go directly to Client Plat step.
 */
function skipToStep3() {
  const rs = state.researchSession;
  if (!rs) { showToast("Start a session first", "warn"); return; }
  // Ensure client subject exists even without a deed
  const clientSubj = rs.subjects.find(s => s.type === "client");
  if (clientSubj && !clientSubj.deed_saved) {
    // Mark deed as skipped so Step 4 still functions
    showToast("Skipping deed — going to Client Plat search", "info");
  }
  goToStep(3);
}

async function manualAddAdjoiner() {
  const inp = document.getElementById("s4ManualName");
  const name = inp.value.trim();
  if (!name || name.length < 2) return;

  await addFoundAdjoiner(name);
  inp.value = "";
}

// Expose Cabinet browser globally
function openGlobalCabinetBrowser(defaultCab) {
  document.getElementById('cabinetOverlay').classList.remove('hidden');
  browseCabinet(defaultCab || 'A');
}
function closeCabinetBrowser() {
  document.getElementById('cabinetOverlay').classList.add('hidden');
}

let _cabState = { cab: 'C', filter: '', page: 1 };
async function browseCabinet(cab, page = 1) {
  _cabState.cab = cab; _cabState.page = page;

  document.querySelectorAll('.cab-tab').forEach(b => b.classList.toggle('active', b.dataset.cab === cab));
  const body = document.getElementById('cabinetBody');
  body.innerHTML = `<div class="loading-state">Loading...</div>`;

  try {
    const res = await apiFetch(`/cabinet-browse?cabinet=${cab}&filter=${encodeURIComponent(_cabState.filter)}&page=${page}&per_page=50`);
    if (!res.success) throw new Error(res.error);

    document.getElementById('cabinetCount').textContent = `Total: ${res.total} files`;

    if (!res.files.length) {
      body.innerHTML = `<div class="empty-state text-sm">No files match.</div>`;
      return;
    }

    let html = `<table class="data-table"><tbody>`;
    res.files.forEach(f => {
      const escapedPath = f.path.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
      const escapedFile = (f.file || '').replace(/'/g, "\\'");
      html += `
        <tr>
          <td class="text-xs" title="${escHtml(f.file)}">
            ${escHtml(f.display_name || f.file)}
            ${f.doc_number ? `<span style="font-family:monospace;font-size:10px;opacity:.6;margin-left:5px">Doc# ${escHtml(f.doc_number)}</span>` : ''}
          </td>
          <td class="text-text3 text-xs w-16">${f.size_kb} KB</td>
          <td style="white-space:nowrap">
            <button class="btn btn-outline btn-sm" style="margin-right:4px" onclick="apiFetch('/open-file','POST',{path:'${escapedPath}'})">&#128065; Open</button>
            <button class="btn btn-success btn-sm" onclick="saveFromCabinetBrowser('${escapedPath}','${escapedFile}')">&#11015; Save as Plat</button>
          </td>
        </tr>`;
    });
    html += `</tbody></table>`;
    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = `<div class="text-danger p-2">${e.message}</div>`;
  }
}
function filterCabinet() {
  _cabState.filter = document.getElementById('cabinetFilter').value.trim();
  browseCabinet(_cabState.cab, 1);
}

/**
 * Save a file chosen directly from the Cabinet Browser as the client plat.
 * Closes the browser modal on success.
 */
async function saveFromCabinetBrowser(filePath, filename) {
  const rs = state.researchSession;
  if (!rs) { showToast('No active research session', 'error'); return; }
  const clientSubj = rs.subjects.find(s => s.type === 'client');

  try {
    const res = await apiFetch('/save-plat', 'POST', {
      source: 'local',
      file_path: filePath,
      filename: filename,
      job_number: rs.job_number,
      client_name: rs.client_name,
      job_type: rs.job_type,
      subject_id: clientSubj ? clientSubj.id : 'client'
    });
    if (res.success) {
      showToast(res.skipped ? 'Plat already exists in project (skipped)' : '\u2B07 Plat saved: ' + res.filename, 'success');
      if (clientSubj && !res.skipped) {
        clientSubj.plat_saved = true;
        if (res.saved_to) clientSubj.plat_path = res.saved_to;
        await persistSession();
      }
      closeCabinetBrowser();
      setTimeout(() => goToStep(4), 600);
    } else {
      showToast('Save failed: ' + res.error, 'error');
    }
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}
// 
// STEP 5: ADJOINER RESEARCH BOARD
// 
function renderResearchBoard() {
  const grid = document.getElementById("s5ResearchGrid");
  const rs = state.researchSession;
  if (!rs || !rs.subjects.length) {
    grid.innerHTML = `<div class="empty-state">No subjects on board. Add adjoiners in Step 4.</div>`;
    return;
  }

  // Auto-resolve any numeric-only names (UPCs) that slipped through
  _autoResolveNumericNames(rs);

  // Clean trailing conjunctions from existing session names (one-time migration)
  let _namesCleaned = false;
  for (const subj of rs.subjects) {
    const cleaned = cleanOwnerName(subj.name);
    if (cleaned !== subj.name && cleaned.length > 1) {
      subj.name = cleaned;
      _namesCleaned = true;
    }
  }
  if (_namesCleaned) {
    persistSession(); // Save cleaned names (fire-and-forget)
  }

  const adjoiners = rs.subjects.filter(s => s.type === "adjoiner");
  const client = rs.subjects.find(s => s.type === "client");
  let html = "";

  // Client card first
  if (client) html += buildSubjectCard(client, rs);

  adjoiners.forEach(s => { html += buildSubjectCard(s, rs); });
  grid.innerHTML = html;

  // Add export button at the bottom of the grid
  grid.insertAdjacentHTML('beforeend', `
    <div style="grid-column:1/-1;text-align:center;padding:16px 0;border-top:1px solid var(--border);margin-top:8px">
      <button class="btn btn-primary btn-sm" onclick="exportResearchReport()" style="padding:8px 24px">
        📊 Export Research Report
      </button>
      <div style="font-size:10px;color:var(--text3);margin-top:4px">Opens a printable summary of all findings in a new tab</div>
    </div>
  `);

  // Update the bottom progress bar
  _updateBoardProgress(rs, client, adjoiners);
}

/**
 * Auto-resolve numeric-only adjoiner names via ArcGIS.
 * Runs once per session load — marks resolved names so it doesn't re-run.
 */
let _resolveInFlight = false;
async function _autoResolveNumericNames(rs) {
  if (_resolveInFlight) return;
  const numericSubjects = rs.subjects.filter(
    s => s.type === 'adjoiner' && isUpcCode(s.name) && !s._resolveAttempted
  );
  if (!numericSubjects.length) return;

  _resolveInFlight = true;
  let changed = false;
  for (const subj of numericSubjects) {
    subj._resolveAttempted = true;
    const upc = subj.upc || subj.name.trim();
    try {
      const resolved = await resolveUpcToOwner(upc);
      if (resolved && resolved.length > 2 && !isUpcCode(resolved)) {
        console.log(`[resolve] UPC ${upc} → ${resolved}`);
        subj.name = resolved;
        if (!subj.upc) subj.upc = upc;
        changed = true;
      } else {
        // Label it clearly as a UPC
        subj.name = `UPC ${subj.name.trim()}`;
        if (!subj.upc) subj.upc = upc;
        changed = true;
      }
    } catch (e) {
      console.warn(`[resolve] Failed for ${upc}:`, e.message);
    }
  }
  _resolveInFlight = false;
  if (changed) {
    await persistSession();
    renderResearchBoard();
  }
}

/** Update the Deeds: X/Y, Plats: X/Y footer counters. */
function _updateBoardProgress(rs, client, adjoiners) {
  const allSubjects = [client, ...adjoiners].filter(Boolean);
  const total = allSubjects.length;
  const deedsDone = allSubjects.filter(s => s.deed_saved).length;
  const platsDone = allSubjects.filter(s => s.plat_saved).length;
  const deedEl = document.getElementById('statDeeds');
  const platEl = document.getElementById('statPlats');
  if (deedEl) deedEl.textContent = `${deedsDone}/${total}`;
  if (platEl) platEl.textContent = `${platsDone}/${total}`;
}

function buildSubjectCard(s, rs) {
  const isClient = s.type === "client";
  const st = s.status || "pending";
  const accentColor = isClient ? "var(--accent)" : "#7a4f9a";

  const deedChip = s.deed_saved
    ? `<span class="chip chip-done">✓ Deed</span>${s.deed_path ? `<button class="btn-icon-sm ml-1" title="View deed" onclick="viewSubjectFile('${s.id}','deed')">👁️</button><button class="btn-icon-sm ml-1" title="Download" onclick="downloadLocalFileToBrowser('${s.deed_path.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}')">⬇</button>` : ""}<button class="btn-icon-sm ml-1" title="Discard saved deed" style="color:#ff7b72;font-size:11px" onclick="clearSubjectDeed('${s.id}')">✕</button>`
    : `<span class="chip chip-todo">Deed</span>`;
  const platChip = s.plat_saved
    ? `<span class="chip chip-done">✓ Plat</span>${s.plat_path ? `<button class="btn-icon-sm ml-1" title="View plat" onclick="viewSubjectFile('${s.id}','plat')">👁️</button><button class="btn-icon-sm ml-1" title="Download" onclick="downloadLocalFileToBrowser('${s.plat_path.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}')">⬇</button>` : ""}<button class="btn-icon-sm ml-1" title="Discard saved plat" style="color:#ff7b72;font-size:11px" onclick="clearSubjectPlat('${s.id}')">✕</button>`
    : `<span class="chip chip-todo">Plat</span>`;

  const statusColors = { done: "#1a3028;color:#56d3a0", na: "#281a1a;color:#888", pending: "var(--bg3);color:var(--text3)" };
  const statusLabel = { done: "✓ Done", na: "— N/A", pending: "⧗ Pending" }[st];

  // UPC badge (only if we have a UPC and it's not already in the display name)
  const upcBadge = s.upc && !s.name.includes('UPC')
    ? `<span style="font-size:9px;font-family:monospace;color:var(--text3);opacity:.7;margin-left:4px">UPC ${escHtml(s.upc)}</span>`
    : '';

  // Search result count badge (persisted from bulk search)
  const countBadge = s.search_count != null
    ? (s.search_count > 0
      ? `<span style="font-size:10px;color:var(--accent2);margin-left:auto">🔍 ${s.search_count} record${s.search_count !== 1 ? 's' : ''} found</span>`
      : `<span style="font-size:10px;color:var(--text3);margin-left:auto">No records found</span>`)
    : '';

  // Loading indicator (set during bulk/individual search)
  const loadingId = `loader_${s.id}`;

  return `
    <div class="adjoiner-card status-${st}" id="card_${s.id}" style="border-top-color:${accentColor}">
      <div class="adjoiner-card-header">
        <div class="flex-col gap-1" style="flex:1;min-width:0">
          <strong style="font-size:15px">${escHtml(s.name)}</strong>
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
            <span style="font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:${isClient ? 'var(--accent2)' : '#b080e0'}">
              ${isClient ? "★ Client" : "⬡ Adjoiner"}
            </span>
            ${upcBadge}
          </div>
        </div>
        <div class="flex-col gap-1" style="align-items:flex-end">
          <button class="chip" style="background:${statusColors[st]};border-color:transparent;cursor:pointer;padding:4px 12px;border-radius:12px;font-size:11px;font-weight:700"
            onclick="cycleSubjectStatus('${s.id}')">${statusLabel}</button>
          ${countBadge}
        </div>
        <span id="${loadingId}" style="display:none"><div class="spinner" style="width:16px;height:16px;margin-left:6px"></div></span>
      </div>

      <div class="adjoiner-card-body">
        <!-- Status chips -->
        <div class="row-layout gap-2 flex-wrap">
          ${deedChip}
          ${platChip}
          ${buildExceptionFlags(s)}
        </div>

        <!-- Notes -->
        <input class="inp" style="padding:6px 10px;font-size:12px"
          placeholder="Notes..." value="${escHtml(s.notes || "")}"
          onchange="saveNote('${s.id}', this.value)">

        <!-- Chain tracker -->
        ${buildChainTracker(s)}

        <!-- Actions -->
        <div class="row-layout gap-2 flex-wrap border-t pt-2" style="border-color:var(--border)">
          ${!isClient ? `
          <button class="btn btn-outline btn-sm flex-1" onclick="saveAdjDeed('${s.id}')" title="Search records and save a deed for this adjoiner">
            🔍 Find Deed
          </button>
          <button class="btn btn-outline btn-sm flex-1" onclick="saveAdjPlat('${s.id}')" title="Search cabinet/KML for plat">
            📐 Find Plat
          </button>
          <button class="btn btn-outline btn-sm" style="color:#ff7b72" onclick="removeSubject('${s.id}')" title="Remove this adjoiner">✗</button>
          ` : `
          <button class="btn btn-outline btn-sm flex-1" onclick="goToStep(2)" title="Go to Client Deed search">
            🔍 Client Deed
          </button>
          <button class="btn btn-outline btn-sm flex-1" onclick="goToStep(3)" title="Go to Client Plat search">
            📐 Client Plat
          </button>
          `}
        </div>
      </div>
    </div>`;
}

function buildExceptionFlags(s) {
  const FLAGS = [
    { key: "mineral", label: " Mineral" },
    { key: "easement", label: " Easement" },
    { key: "roe", label: " ROW" },
    { key: "access", label: " Access" },
  ];
  const exc = s.exceptions || {};
  return `<div class="row-layout gap-1 flex-wrap">` +
    FLAGS.map(f => `<span class="exc-chip ${exc[f.key] ? "exc-active" : "exc-off"}"
      onclick="toggleException('${s.id}','${f.key}')" title="Toggle ${f.label}">${f.label}</span>`).join("") +
    `</div>`;
}

function buildChainTracker(s) {
  const years = (s.chain_years || []).sort((a, b) => b - a);
  const goal = s.chain_goal || null;
  const reached = goal && years.length && Math.min(...years) <= goal;

  return `<div class="chain-box">
    <div class="row-layout justify-between mb-1">
      <span class="text-xs text-text3 font-bold uppercase">Chain of Title</span>
      <div class="row-layout gap-2">
        ${goal
      ? `<span class="text-xs" style="color:${reached ? "#56d3a0" : "#e3c55a"}">${reached ? "" : ""} Goal: ${goal}</span>`
      : `<button class="link-btn" onclick="setChainGoal('${s.id}')">+ Set goal year</button>`}
        <button class="link-btn text-accent2" onclick="addChainYear('${s.id}')">+ Add year</button>
      </div>
    </div>
    ${years.length
      ? `<div class="chain-years">${years.map(y => `<span class="year-chip" onclick="removeChainYear('${s.id}',${y})" title="Remove">${y}</span>`).join("")}</div>`
      : `<div class="text-xs text-text3 italic">No years logged</div>`}
  </div>`;
}

//  Search from board — now opens inline deed pick modal instead of navigating away
function searchForSubject(name) {
  // Find the subject by name and use the inline deed search modal
  const rs = state.researchSession;
  if (!rs) return;
  const subj = rs.subjects.find(s => s.name.split(',')[0].trim().toLowerCase() === name.toLowerCase()
    || s.name.toLowerCase() === name.toLowerCase());
  if (subj && subj.type === 'adjoiner') {
    saveAdjDeed(subj.id);
  } else {
    // Fallback for client or unmatched — go to Step 2
    if (document.getElementById("s2SearchName")) {
      document.getElementById("s2SearchName").value = name;
    }
    goToStep(2);
    setTimeout(() => doStep2Search(), 300);
  }
}

// ── Adjoiner: auto-search deeds and show a pick modal ──────────────────────────
async function saveAdjDeed(subjId) {
  const rs = state.researchSession;
  const subj = rs.subjects.find(s => s.id === subjId);
  if (!subj) return;

  // If a deed is already loaded in Step 2 AND matches the adjoiner's last name, use it.
  const adjLast = subj.name.split(',')[0].trim().toLowerCase();
  const loadedGrantor = (state.selectedDetail?.['Grantor'] || '').toLowerCase();
  const loadedGrantee = (state.selectedDetail?.['Grantee'] || '').toLowerCase();
  if (state.selectedDoc && state.selectedDetail &&
    (loadedGrantor.includes(adjLast) || loadedGrantee.includes(adjLast))) {
    await _doSaveAdjDeedFromLoaded(subjId, rs, subj);
    return;
  }

  // Handle UPC-only names — try to resolve the owner first
  let searchableName = subj.name;
  if (searchableName.startsWith('UPC ') && subj.upc) {
    // Try resolving UPC to owner name one more time
    const resolved = await resolveUpcToOwner(subj.upc);
    if (resolved && !isUpcCode(resolved)) {
      searchableName = resolved;
      subj.name = resolved;  // Update the name permanently
      await persistSession();
    } else {
      showToast(`Cannot search for "${subj.name}" — no owner name found for this UPC. Try using the map to identify the owner.`, 'warn');
      return;
    }
  }

  // Extract search name — try full name first, fall back to last name only
  const fullName = searchableName.trim();
  const lastName = fullName.split(',')[0].trim();
  if (!lastName || lastName.length < 2) {
    showToast('Adjoiner name too short to search', 'warn');
    return;
  }

  if (!state.loggedIn) {
    showToast('Not connected to records — searching anyway...', 'warn');
  }

  showToast(`Searching records for "${fullName}"...`, 'info');
  try {
    // Filter to deed / conveyance instrument types
    const _DEED_TYPE_RE = /deed|warranty|quitclaim|grant|convey|patent|transfer|bargain|assign/i;

    // Try full name first (more precise)
    let res = await apiFetch('/search', 'POST', { name: fullName, operator: 'begins with' });
    if (!res.success) { showToast('Search error: ' + res.error, 'error'); return; }
    let deedResults = (res.results || []).filter(r =>
      _DEED_TYPE_RE.test(r.instrument_type || '') || !r.instrument_type
    );

    // If full name returns 0 results, fall back to last name only
    if (!deedResults.length && fullName.includes(',')) {
      showToast(`No results for full name, trying last name "${lastName}"...`, 'info');
      res = await apiFetch('/search', 'POST', { name: lastName, operator: 'begins with' });
      if (res.success) {
        deedResults = (res.results || []).filter(r =>
          _DEED_TYPE_RE.test(r.instrument_type || '') || !r.instrument_type
        );
      }
    }

    if (!deedResults.length) {
      showToast(`No deed-type records found for "${lastName}" (${(res.results||[]).length} non-deed records skipped).`, 'warn');
      return;
    }
    _showAdjDeedPickModal(subjId, subj.name, deedResults);
  } catch (e) {
    showToast('Search failed: ' + e.message, 'error');
  }
}

function _showAdjDeedPickModal(subjId, adjName, results) {
  // Build or reuse a simple pick modal
  let overlay = document.getElementById('adjDeedPickOverlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'adjDeedPickOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:9999;display:flex;align-items:center;justify-content:center';
    document.body.appendChild(overlay);
  }

  const rows = results.slice(0, 15).map((r, i) => `
    <tr style="cursor:pointer" onclick="_pickAdjDeed('${subjId}', ${i})" id="adjrow_${i}">
      <td class="mono text-xs" style="color:var(--accent2);padding:6px 8px">${escHtml(r.doc_no || '')}</td>
      <td style="padding:6px 8px;font-size:12px">${escHtml((r.grantor || '').split(',')[0] || r.grantor || '')}</td>
      <td style="padding:6px 8px;font-size:12px">${escHtml((r.grantee || '').split(',')[0] || r.grantee || '')}</td>
      <td style="padding:6px 8px"><span class="badge ${getTypeClass(r.instrument_type)}">${escHtml(r.instrument_type || 'Deed')}</span></td>
      <td class="text-xs" style="padding:6px 8px;color:var(--text3)">${escHtml(r.location || '')}</td>
      <td class="text-xs" style="padding:6px 8px;color:var(--text3)">${(r.recorded_date || r.date || '').split('-')[0] || ''}</td>
    </tr>`).join('');

  // Store results in state temporarily
  state._adjPickResults = results;
  state._adjPickSubjId = subjId;

  overlay.innerHTML = `
    <div class="glass-card" style="width:min(960px,95vw);max-height:80vh;display:flex;flex-direction:column;overflow:hidden">
      <div style="padding:16px 20px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border)">
        <div>
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.6px;color:var(--text3)">Select deed for adjoiner</div>
          <div style="font-size:18px;font-weight:700;color:var(--accent2)">${escHtml(adjName)}</div>
        </div>
        <button class="btn btn-outline btn-sm" onclick="document.getElementById('adjDeedPickOverlay').remove()">✕ Cancel</button>
      </div>
      <div style="overflow-y:auto;flex:1">
        <table class="data-table" style="width:100%">
          <thead><tr>
            <th style="padding:6px 8px;font-size:10px">Doc #</th>
            <th style="padding:6px 8px;font-size:10px">Grantor</th>
            <th style="padding:6px 8px;font-size:10px">Grantee</th>
            <th style="padding:6px 8px;font-size:10px">Type</th>
            <th style="padding:6px 8px;font-size:10px">Location</th>
            <th style="padding:6px 8px;font-size:10px">Year</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div style="padding:10px 20px;border-top:1px solid var(--border);font-size:11px;color:var(--text3);text-align:center">
        Click a row to save that deed for this adjoiner &nbsp;|&nbsp;
        <button class="link-btn" onclick="document.getElementById('adjDeedPickOverlay').remove();searchForSubject('${escHtml(adjName.split(',')[0]).replace(/'/g, "\\'")}')">Search manually in Step 2 →</button>
      </div>
    </div>`;
}

async function _pickAdjDeed(subjId, idx) {
  const rs = state.researchSession;
  const subj = rs.subjects.find(s => s.id === subjId);
  const r = state._adjPickResults?.[idx];
  if (!subj || !r) return;

  // Highlight selected row
  document.querySelectorAll('#adjDeedPickOverlay tr[id^=adjrow_]').forEach(tr => tr.style.background = '');
  const row = document.getElementById('adjrow_' + idx);
  if (row) row.style.background = 'rgba(46,160,67,0.15)';

  showToast(`Downloading deed ${r.doc_no}...`, 'info');
  try {
    const res = await apiFetch('/download', 'POST', {
      doc_no: r.doc_no,
      grantor: r.grantor || '',
      grantee: r.grantee || '',
      location: r.location || '',
      job_number: rs.job_number,
      client_name: rs.client_name,
      job_type: rs.job_type,
      create_project: true,
      is_adjoiner: true,
      adjoiner_name: subj.name,
      subject_id: subjId,
    });
    if (res.success) {
      subj.deed_saved = true;
      if (res.saved_to) subj.deed_path = res.saved_to;
      // Store deed detail for reference table
      if (r) { subj.detail = r; subj.doc_no = r.doc_no || ''; }
      await persistSession();
      showToast(res.skipped ? `Deed already exists for ${subj.name}` : `Deed saved for ${subj.name}!`, 'success');
      document.getElementById('adjDeedPickOverlay')?.remove();
      renderResearchBoard();
      _autoQaCheck();
    } else {
      showToast('Save failed: ' + res.error, 'error');
    }
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

async function _doSaveAdjDeedFromLoaded(subjId, rs, subj) {
  try {
    const res = await apiFetch('/download', 'POST', {
      doc_no: state.selectedDoc.doc_no,
      grantor: state.selectedDetail['Grantor'] || '',
      grantee: state.selectedDetail['Grantee'] || '',
      location: state.selectedDetail['Location'] || '',
      job_number: rs.job_number,
      client_name: rs.client_name,
      job_type: rs.job_type,
      create_project: true,
      is_adjoiner: true,
      adjoiner_name: subj.name,
      subject_id: subjId,
    });
    if (res.success) {
      subj.deed_saved = true;
      if (res.saved_to) subj.deed_path = res.saved_to;
      // Store deed detail for reference table
      if (state.selectedDetail) subj.detail = state.selectedDetail;
      if (state.selectedDoc?.doc_no) subj.doc_no = state.selectedDoc.doc_no;
      await persistSession();
      showToast(res.skipped ? `Deed already exists for ${subj.name}` : `Deed saved for ${subj.name}!`, 'success');
      renderResearchBoard();
      _autoQaCheck();
    } else {
      showToast('Save failed: ' + res.error, 'error');
    }
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}

// ── Adjoiner: find plat using full 3-way parallel search ─────────────────────
async function saveAdjPlat(subjId) {
  const rs = state.researchSession;
  const subj = rs.subjects.find(s => s.id === subjId);
  if (!subj) return;

  const clientName = rs.client_name;
  const adjName = subj.name;
  const lastName = adjName.split(',')[0].trim();

  // If the adjoiner already has a saved deed, try to extract its detail
  // for book/page/Location — this gives us precise cabinet targeting.
  let searchDetail = { 'Grantor': adjName, 'Grantee': '' };
  if (subj.deed_saved && subj.deed_path) {
    try {
      showToast(`📄 Reading saved deed for "${lastName}" to target plat search…`, 'info');
      const deedInfo = await apiFetch('/extract-deed-info', 'POST', { pdf_path: subj.deed_path });
      if (deedInfo.success && deedInfo.detail) {
        // Merge: keep adjoiner as Grantor but add Location/book/page from the pdf
        searchDetail = { ...deedInfo.detail, 'Grantor': adjName };
      }
    } catch (e) { /* fall through to name-only search */ }
  }

  showToast(`🔍 Searching all sources for "${lastName}" plat…`, 'info');

  // Run the same 3-way parallel search as Step 3
  try {
    // 1. Fast deed parse (cabinet refs from synthesized deed)
    let cabRefs = [];
    try {
      const fastRes = await apiFetch('/find-plat', 'POST', { detail: searchDetail });
      cabRefs = (fastRes && fastRes.cabinet_refs) || [];
    } catch (e) { }

    // 2. KML lookup (by name + UPC if available — UPC ensures the right parcel ranks first)
    const adjUpc = subj.upc || '';
    const kmlRes = await apiFetch('/find-plat-kml', 'POST', { detail: searchDetail, client_name: adjName, client_upc: adjUpc });
    const kmlHits = (kmlRes && kmlRes.kml_matches) || [];

    // 3. Local cabinet scan (fires after KML so we pass kml_matches)
    const localRes = await apiFetch('/find-plat-local', 'POST', {
      detail: searchDetail,
      cabinet_refs: cabRefs,
      kml_matches: kmlHits,
      client_name: adjName,
      grantor: adjName,
      grantee: '',
    });
    const localHits = (localRes && localRes.local) || [];

    // Collect all candidates in priority order
    const allCandidates = [
      ...localHits,
      ...(kmlHits.flatMap(k => (k.local_files || []).map(lf => ({ ...lf, strategy: 'kml_local' })))),
    ];

    if (allCandidates.length) {
      // Show a quick pick modal with all candidates
      _showAdjPlatPickModal(subjId, subj, allCandidates);
    } else {
      // 4. Fall back: online search
      const onlineRes = await apiFetch('/find-plat-online', 'POST', {
        detail: searchDetail, client_name: adjName, grantee: '', grantor: adjName
      });
      const onlineHits = (onlineRes && onlineRes.online) || [];
      if (onlineHits.length) {
        _showAdjPlatPickModal(subjId, subj, [], onlineHits);
      } else {
        showToast(`No plat found for "${lastName}" in cabinet, KML, or online records.`, 'warn');
      }
    }
  } catch (e) {
    showToast('Plat search error: ' + e.message, 'error');
  }
}

/** Show a compact modal to pick the best adjoiner plat candidate */
function _showAdjPlatPickModal(subjId, subj, localHits, onlineHits = []) {
  const rs = state.researchSession;
  let overlay = document.getElementById('adjPlatPickOverlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'adjPlatPickOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:9999;display:flex;align-items:center;justify-content:center';
    document.body.appendChild(overlay);
  }

  // Cap displayed results to prevent UI flooding
  const cappedLocalHits = localHits.slice(0, 20);
  const localRows = cappedLocalHits.map((f, i) => `
    <tr style="cursor:pointer" onclick="_pickAdjPlat('${subjId}', 'local', ${i})">
      <td style="padding:7px 10px;font-size:12px;font-weight:600">${escHtml(f.display_name || f.file)}</td>
      <td style="padding:7px 10px;font-size:11px;color:var(--text3)">Cabinet ${escHtml(f.cabinet || '')}</td>
      <td style="padding:7px 10px"><span class="badge badge-local">${escHtml(f.strategy || 'Cabinet')}</span></td>
    </tr>`);

  const cappedOnlineHits = onlineHits.slice(0, 15);
  const onlineRows = cappedOnlineHits.map((r, i) => `
    <tr style="cursor:pointer" onclick="_pickAdjPlatOnline('${subjId}', ${i})">
      <td style="padding:7px 10px;font-size:12px;font-weight:600">${escHtml((r.grantor || '').split(',')[0] || r.grantor || r.doc_no)}</td>
      <td style="padding:7px 10px;font-size:11px;color:var(--text3)">${escHtml(r.recorded_date || '')}</td>
      <td style="padding:7px 10px"><span class="badge badge-online">Online Doc ${escHtml(r.doc_no || '')}</span></td>
    </tr>`);

  state._adjPlatSubjId = subjId;
  state._adjPlatLocalHits = localHits;
  state._adjPlatOnlineHits = onlineHits;

  overlay.innerHTML = `
    <div class="glass-card" style="width:min(760px,95vw);max-height:75vh;display:flex;flex-direction:column;overflow:hidden">
      <div style="padding:14px 20px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border)">
        <div>
          <div style="font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:var(--text3)">Select plat for adjoiner</div>
          <div style="font-size:17px;font-weight:700;color:var(--accent2)">${escHtml(subj.name)}</div>
        </div>
        <button class="btn btn-outline btn-sm" onclick="document.getElementById('adjPlatPickOverlay').remove()">✕ Cancel</button>
      </div>
      <div style="overflow-y:auto;flex:1">
        <table class="data-table" style="width:100%">
          <thead><tr>
            <th style="padding:6px 10px;font-size:10px">File / Name</th>
            <th style="padding:6px 10px;font-size:10px">Location</th>
            <th style="padding:6px 10px;font-size:10px">Source</th>
          </tr></thead>
          <tbody>${localRows.join('') || onlineRows.join('') || '<tr><td colspan="3" class="empty-cell">No candidates found</td></tr>'}
          ${localRows.length && onlineRows.length ? '<tr><td colspan="3" style="padding:4px 10px;background:var(--bg3);font-size:10px;font-weight:700;color:var(--text3);text-transform:uppercase">Online Records</td></tr>' + onlineRows.join('') : ''}
          </tbody>
        </table>
      </div>
      <div style="padding:10px 20px;border-top:1px solid var(--border);font-size:11px;color:var(--text3);text-align:center">
        Click a row to save that plat for this adjoiner
      </div>
    </div>`;
}

async function _pickAdjPlat(subjId, type, idx) {
  const rs = state.researchSession;
  const subj = rs.subjects.find(s => s.id === subjId);
  const f = state._adjPlatLocalHits && state._adjPlatLocalHits[idx];
  if (!subj || !f) return;
  try {
    const res = await apiFetch('/save-plat', 'POST', {
      source: 'local', file_path: f.path, filename: f.file,
      job_number: rs.job_number, client_name: rs.client_name,
      job_type: rs.job_type, subject_id: subjId, is_adjoiner: true, adjoiner_name: subj.name
    });
    if (res.success) {
      subj.plat_saved = true;
      if (res.saved_to) subj.plat_path = res.saved_to;
      await persistSession();
      showToast(`Plat saved for ${subj.name}`, 'success');
      document.getElementById('adjPlatPickOverlay')?.remove();
      renderResearchBoard();
    } else { showToast('Save failed: ' + res.error, 'error'); }
  } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

async function _pickAdjPlatOnline(subjId, idx) {
  const rs = state.researchSession;
  const subj = rs.subjects.find(s => s.id === subjId);
  const r = state._adjPlatOnlineHits && state._adjPlatOnlineHits[idx];
  if (!subj || !r) return;
  try {
    const res = await apiFetch('/save-plat', 'POST', {
      source: 'online', doc_no: r.doc_no, location: r.location || '',
      job_number: rs.job_number, client_name: rs.client_name,
      job_type: rs.job_type, subject_id: subjId, is_adjoiner: true, adjoiner_name: subj.name
    });
    if (res.success) {
      subj.plat_saved = true;
      if (res.saved_to) subj.plat_path = res.saved_to;
      await persistSession();
      showToast(`Plat downloaded for ${subj.name}`, 'success');
      document.getElementById('adjPlatPickOverlay')?.remove();
      renderResearchBoard();
    } else { showToast('Download failed: ' + res.error, 'error'); }
  } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

//  Board persistence helpers 
async function removeSubject(id) {
  state.researchSession.subjects = state.researchSession.subjects.filter(s => s.id !== id);
  await persistSession();
  renderResearchBoard();
}

/** Clear a saved deed from an adjoiner card (marks it as not saved) */
async function clearSubjectDeed(id) {
  const subj = state.researchSession?.subjects?.find(s => s.id === id);
  if (!subj) return;
  if (!confirm(`Discard saved deed for ${subj.name}? This removes the saved status but does not delete the file.`)) return;
  subj.deed_saved = false;
  subj.deed_path = '';
  subj.doc_no = '';
  subj.detail = null;
  await persistSession();
  renderResearchBoard();
  updateGlobalProgress();
  showToast(`Deed discarded for ${subj.name}`, 'info');
}

/** Clear a saved plat from an adjoiner card (marks it as not saved) */
async function clearSubjectPlat(id) {
  const subj = state.researchSession?.subjects?.find(s => s.id === id);
  if (!subj) return;
  if (!confirm(`Discard saved plat for ${subj.name}? This removes the saved status but does not delete the file.`)) return;
  subj.plat_saved = false;
  subj.plat_path = '';
  subj.plat_refs = [];
  await persistSession();
  renderResearchBoard();
  updateGlobalProgress();
  showToast(`Plat discarded for ${subj.name}`, 'info');
}

async function removePendingSubjects() {
  const rs = state.researchSession;
  if (!rs) return;
  const pending = rs.subjects.filter(s => s.type === 'adjoiner' && !s.deed_saved && !s.plat_saved);
  if (!pending.length) { showToast('No pending adjoiners to remove', 'info'); return; }
  if (!confirm(`Remove ${pending.length} pending adjoiner(s) with no saved deeds or plats?`)) return;
  const removeIds = new Set(pending.map(s => s.id));
  rs.subjects = rs.subjects.filter(s => !removeIds.has(s.id));
  await persistSession();
  renderResearchBoard();
  updateGlobalProgress();
  showToast(`Removed ${pending.length} pending adjoiner(s)`, 'success');
}

async function cycleSubjectStatus(id) {
  const subj = state.researchSession?.subjects.find(s => s.id === id);
  if (!subj) return;
  const order = ["pending", "done", "na"];
  const cur = subj.status || "pending";
  subj.status = order[(order.indexOf(cur) + 1) % order.length];
  await persistSession();
  renderResearchBoard();
}

async function saveNote(id, text) {
  const subj = state.researchSession?.subjects.find(s => s.id === id);
  if (!subj) return;
  subj.notes = text;
  await persistSession();
}

async function toggleException(subjId, key) {
  const subj = state.researchSession?.subjects.find(s => s.id === subjId);
  if (!subj) return;
  if (!subj.exceptions) subj.exceptions = {};
  subj.exceptions[key] = !subj.exceptions[key];
  await persistSession();
  renderResearchBoard();
}

function addChainYear(subjId) {
  const y = parseInt(prompt("Enter deed year to add to chain:"));
  if (!y || y < 1600 || y > 2100) return;
  const subj = state.researchSession?.subjects.find(s => s.id === subjId);
  if (!subj) return;
  if (!subj.chain_years) subj.chain_years = [];
  if (!subj.chain_years.includes(y)) subj.chain_years.push(y);
  persistSession().then(() => renderResearchBoard());
}

function setChainGoal(subjId) {
  const y = parseInt(prompt("Need chain back to year:"));
  if (!y || y < 1600 || y > 2100) return;
  const subj = state.researchSession?.subjects.find(s => s.id === subjId);
  if (!subj) return;
  subj.chain_goal = y;
  persistSession().then(() => renderResearchBoard());
}

function removeChainYear(subjId, yr) {
  const subj = state.researchSession?.subjects.find(s => s.id === subjId);
  if (!subj || !subj.chain_years) return;
  subj.chain_years = subj.chain_years.filter(y => y !== yr);
  persistSession().then(() => renderResearchBoard());
}

async function bulkSearchAdjoiners() {
  const rs = state.researchSession;
  if (!rs) return;
  const pending = rs.subjects.filter(s => s.type === "adjoiner" && !s.deed_saved);
  if (!pending.length) { showToast("No pending adjoiners", "info"); return; }
  if (!state.loggedIn) { showToast("Not connected to records", "warn"); return; }

  // Ask user whether to auto-save or just count
  const autoSave = confirm(
    `Found ${pending.length} adjoiners without deeds.\n\n` +
    `• Click OK to AUTO-SAVE the best deed for each adjoiner\n` +
    `• Click Cancel to just count records (no downloads)\n\n` +
    `Auto-save picks the most recent warranty/deed document for each name.`
  );

  const _DEED_TYPE_RE = /deed|warranty|quitclaim|grant|convey|patent|transfer|bargain|assign/i;
  showToast(`🔍 ${autoSave ? 'Auto-saving deeds' : 'Counting records'} for ${pending.length} adjoiners...`, "info");
  let searched = 0, saved = 0, failed = 0;

  for (const subj of pending) {
    // Show per-card loading indicator
    const loader = document.getElementById(`loader_${subj.id}`);
    if (loader) loader.style.display = 'inline-block';

    // Skip UPC-only names that can't be searched
    if (subj.name.startsWith('UPC ')) {
      subj.search_count = 0;
      subj._bulkNote = 'Skipped — no owner name';
      if (loader) loader.style.display = 'none';
      renderResearchBoard();
      continue;
    }

    // Use FULL name first, fall back to last name
    const fullName = subj.name.trim();
    const lastName = fullName.split(',')[0].trim();
    let searchName = fullName.includes(',') ? fullName : lastName;

    try {
      let res = await apiFetch("/search", "POST", { name: searchName, operator: "begins with" });
      let allResults = res.results || [];

      // Fall back to last name if full name returns 0
      if (!allResults.length && fullName.includes(',')) {
        res = await apiFetch("/search", "POST", { name: lastName, operator: "begins with" });
        allResults = res.results || [];
      }

      // Filter to deed types
      const deedResults = allResults.filter(r =>
        _DEED_TYPE_RE.test(r.instrument_type || '') || !r.instrument_type
      );

      subj.search_count = deedResults.length;
      searched++;

      // Auto-save: pick the best deed and download it
      if (autoSave && deedResults.length > 0 && !subj.deed_saved) {
        const best = _pickBestDeed(deedResults, subj.name);
        if (best) {
          try {
            const dlRes = await apiFetch('/download', 'POST', {
              doc_no: best.doc_no,
              grantor: best.grantor || '',
              grantee: best.grantee || '',
              location: best.location || '',
              job_number: rs.job_number,
              client_name: rs.client_name,
              job_type: rs.job_type,
              create_project: true,
              is_adjoiner: true,
              adjoiner_name: subj.name,
              subject_id: subj.id,
            });
            if (dlRes.success) {
              subj.deed_saved = true;
              if (dlRes.saved_to) subj.deed_path = dlRes.saved_to;
              saved++;
            } else {
              failed++;
            }
          } catch (e) {
            console.warn(`[bulk] Download failed for ${subj.name}:`, e.message);
            failed++;
          }
        }
      }
    } catch (e) {
      console.warn(`[bulk] Search failed for ${searchName}:`, e.message);
    }

    if (loader) loader.style.display = 'none';
    renderResearchBoard();
    await new Promise(r => setTimeout(r, 500));
  }

  await persistSession();
  if (autoSave) {
    showToast(`✓ Bulk complete — ${saved} deeds saved, ${failed} failed, ${searched - saved - failed} no results`, "success");
  } else {
    showToast(`✓ Bulk search complete — ${searched} adjoiners checked`, "success");
  }
}

/**
 * Pick the best deed from search results for auto-save.
 * Prioritizes: warranty deeds > other deeds, most recent first,
 * and prefers records where the adjoiner is the GRANTEE (they received the property).
 */
function _pickBestDeed(results, adjName) {
  const adjLast = adjName.split(',')[0].trim().toUpperCase();
  const scored = results.map(r => {
    let score = 0;
    const type = (r.instrument_type || '').toLowerCase();
    const grantor = (r.grantor || '').toUpperCase();
    const grantee = (r.grantee || '').toUpperCase();

    // Instrument type priority
    if (/warranty/i.test(type)) score += 50;
    else if (/deed/i.test(type) && !/trust/i.test(type)) score += 40;
    else if (/quitclaim/i.test(type)) score += 30;
    else if (/grant|convey|patent/i.test(type)) score += 25;
    else score += 5;  // Unknown type, still a deed candidate

    // Prefer records where adjoiner is grantee (they received the property)
    if (grantee.includes(adjLast)) score += 20;
    // Also boost if they're grantor (they owned it)
    if (grantor.includes(adjLast)) score += 10;

    // Recency bonus
    const dateStr = r.recorded_date || r.instrument_date || '';
    try {
      const yr = parseInt(dateStr.split('-')[0]) || parseInt(dateStr.slice(-4));
      if (yr >= 2020) score += 15;
      else if (yr >= 2010) score += 10;
      else if (yr >= 2000) score += 5;
    } catch (e) {}

    return { ...r, _score: score };
  });

  scored.sort((a, b) => b._score - a._score);
  return scored[0] || null;
}

async function bulkFindPlats() {
  const rs = state.researchSession;
  if (!rs) return;
  const pending = rs.subjects.filter(s => s.type === "adjoiner" && !s.plat_saved);
  if (!pending.length) { showToast("No adjoiners need plats", "info"); return; }

  const autoSave = confirm(
    `Found ${pending.length} adjoiners without plats.\n\n` +
    `• Click OK to AUTO-SAVE the best plat match for each\n` +
    `• Click Cancel to cancel\n\n` +
    `This searches cabinet files, KML index, and online records.`
  );
  if (!autoSave) return;

  showToast(`📐 Finding plats for ${pending.length} adjoiners...`, "info");
  let found = 0, failed = 0;

  for (const subj of pending) {
    // Skip UPC-only
    if (subj.name.startsWith('UPC ')) continue;

    const loader = document.getElementById(`loader_${subj.id}`);
    if (loader) loader.style.display = 'inline-block';

    const adjName = subj.name;
    const adjUpc = subj.upc || '';

    // Build search detail from deed if available
    let searchDetail = { 'Grantor': adjName, 'Grantee': '' };
    if (subj.deed_saved && subj.deed_path) {
      try {
        const deedInfo = await apiFetch('/extract-deed-info', 'POST', { pdf_path: subj.deed_path });
        if (deedInfo.success && deedInfo.detail) {
          searchDetail = { ...deedInfo.detail, 'Grantor': adjName };
        }
      } catch (e) {}
    }

    try {
      // 1. Cabinet refs from deed
      let cabRefs = [];
      try {
        const fastRes = await apiFetch('/find-plat', 'POST', { detail: searchDetail });
        cabRefs = (fastRes && fastRes.cabinet_refs) || [];
      } catch (e) {}

      // 2. KML lookup
      const kmlRes = await apiFetch('/find-plat-kml', 'POST', {
        detail: searchDetail, client_name: adjName, client_upc: adjUpc
      });
      const kmlHits = (kmlRes && kmlRes.kml_matches) || [];

      // 3. Local cabinet scan
      const localRes = await apiFetch('/find-plat-local', 'POST', {
        detail: searchDetail, cabinet_refs: cabRefs, kml_matches: kmlHits,
        client_name: adjName, grantor: adjName, grantee: '',
      });
      const localHits = (localRes && localRes.local) || [];

      // Collect all candidates
      const allCandidates = [
        ...localHits,
        ...(kmlHits.flatMap(k => (k.local_files || []).map(lf => ({ ...lf, strategy: 'kml_local' })))),
      ];

      if (allCandidates.length > 0) {
        // Auto-pick the first (highest-ranked) candidate
        const best = allCandidates[0];
        try {
          const saveRes = await apiFetch('/save-plat', 'POST', {
            source: 'local', file_path: best.path, filename: best.file,
            job_number: rs.job_number, client_name: rs.client_name,
            job_type: rs.job_type, subject_id: subj.id,
            is_adjoiner: true, adjoiner_name: subj.name
          });
          if (saveRes.success) {
            subj.plat_saved = true;
            if (saveRes.saved_to) subj.plat_path = saveRes.saved_to;
            found++;
          } else { failed++; }
        } catch (e) { failed++; }
      } else {
        // Try online fallback
        try {
          const onlineRes = await apiFetch('/find-plat-online', 'POST', {
            detail: searchDetail, client_name: adjName, grantee: '', grantor: adjName
          });
          const onlineHits = (onlineRes && onlineRes.online) || [];
          if (onlineHits.length > 0) {
            const best = onlineHits[0];
            const saveRes = await apiFetch('/save-plat', 'POST', {
              source: 'online', doc_no: best.doc_no, location: best.location || '',
              job_number: rs.job_number, client_name: rs.client_name,
              job_type: rs.job_type, subject_id: subj.id,
              is_adjoiner: true, adjoiner_name: subj.name
            });
            if (saveRes.success) {
              subj.plat_saved = true;
              if (saveRes.saved_to) subj.plat_path = saveRes.saved_to;
              found++;
            } else { failed++; }
          } else { failed++; }
        } catch (e) { failed++; }
      }
    } catch (e) {
      console.warn(`[bulk-plat] Error for ${adjName}:`, e.message);
      failed++;
    }

    if (loader) loader.style.display = 'none';
    renderResearchBoard();
    await new Promise(r => setTimeout(r, 500));
  }

  await persistSession();
  showToast(`✓ Plat search complete — ${found} plats saved, ${failed} not found`, "success");
}

async function openFolderForContext() {
  const rs = state.researchSession;
  if (!rs) { showToast("No active session", "warn"); return; }
  try {
    const drv = await apiFetch("/drive-status");
    const drive = (drv.drive_ok && drv.drive) ? drv.drive : "F";
    const rstart = Math.floor(parseInt(rs.job_number) / 100) * 100;
    const last = rs.client_name.split(",")[0].trim();
    const path = `${drive}:\\AI DATA CENTER\\Survey Data\\${rstart}-${rstart + 99}\\${rs.job_number} ${rs.client_name}\\${rs.job_number}-01-${rs.job_type} ${last}\\E Research`;
    apiFetch("/open-folder", "POST", { path }).catch(() => { });
    showToast("Opening E Research folder...", "info");
  } catch (e) {
    showToast("Could not resolve drive path", "warn");
  }
}

function openFile(filePath) {
  apiFetch("/open-file", "POST", { path: filePath })
    .then(r => { if (!r.success) showToast("File not found", "error"); })
    .catch(() => showToast("Could not open file", "error"));
}
// 
// STEP 6: BOUNDARY LINES (DXF)
// 
function switchS6Tab(tab) {
  ["calls", "parcels", "references", "options"].forEach(t => {
    document.getElementById(`s6Tab${t.charAt(0).toUpperCase() + t.slice(1)}`)?.classList.toggle("hidden", t !== tab);
    const btn = document.querySelector(`[onclick="switchS6Tab('${t}')"]`);
    if (btn) btn.classList.toggle("active", t === tab);
  });
  if (tab === "parcels") renderS6ParcelList();
  if (tab === "references") refreshRefTable();
}

async function reparseClientCallsFromSession(silent = false) {
  // Strategy 1: Use in-memory deed detail (fastest — no I/O)
  if (state.selectedDetail) {
    try {
      const res = await apiFetch("/parse-calls", "POST", { detail: state.selectedDetail });
      if (!res.success) { if (!silent) showToast("Parse error: " + res.error, "error"); return; }
      state.parsedCalls = res.calls || [];
      renderS6CallsTable(res);
      if (!silent) showToast(`${res.count} call${res.count !== 1 ? "s" : ""} parsed from deed`, res.count ? "success" : "warn");
      else if (res.count) showToast(`✓ ${res.count} boundary calls imported from deed`, "success");
      return;
    } catch (e) {
      if (!silent) showToast("Error: " + e.message, "error");
      return;
    }
  }

  // Strategy 2: Fall back to reading the saved deed PDF from disk
  const rs = state.researchSession;
  const clientSubj = rs && rs.subjects ? rs.subjects.find(s => s.type === 'client') : null;
  const deedPath = clientSubj && clientSubj.deed_path;

  if (!deedPath) {
    if (!silent) showToast("No deed detail in memory and no saved deed PDF found — search in Step 2 first", "warn");
    return;
  }

  if (!silent) showToast("📄 Extracting calls from saved deed PDF…", "info");
  try {
    const res = await apiFetch("/extract-calls-from-pdf", "POST", { pdf_path: deedPath });
    if (!res.success) { if (!silent) showToast("PDF extraction error: " + res.error, "error"); return; }
    state.parsedCalls = res.calls || [];
    renderS6CallsTable(res);
    const src = res.source === 'ocr' ? ' (via OCR)' : '';
    if (!silent) showToast(`${res.count} call${res.count !== 1 ? "s" : ""} extracted from ${res.filename || 'deed PDF'}${src}`, res.count ? "success" : "warn");
    else if (res.count) showToast(`✓ ${res.count} boundary calls imported from saved deed${src}`, "success");
  } catch (e) {
    if (!silent) showToast("Error reading deed PDF: " + e.message, "error");
  }
}

async function parseS6Text() {
  const txt = document.getElementById("s6PasteText").value.trim();
  if (!txt) { showToast("Paste deed text first", "warn"); return; }
  try {
    const res = await apiFetch("/parse-calls", "POST", { text: txt });
    if (!res.success) { showToast("Parse error: " + res.error, "error"); return; }
    state.parsedCalls = res.calls || [];
    renderS6CallsTable(res);
    showToast(`${res.count} calls parsed`, res.count ? "success" : "warn");
  } catch (e) {
    showToast("Error: " + e.message, "error");
  }
}

function renderS6CallsTable(res) {
  const tbody = document.getElementById("s6CallsTbody");
  const closureBar = document.getElementById("s6ClosureBar");
  const closureText = document.getElementById("s6ClosureText");
  const calls = state.parsedCalls;

  if (!calls.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty-cell">No calls parsed yet.</td></tr>`;
    closureBar.classList.add("hidden");
    updateS6Sketch();
    return;
  }

  const err = res.closure_err ?? 0;
  const cls = err < 0.5 ? "text-accent2" : err < 2 ? "text-gold" : "text-danger";
  closureBar.classList.remove("hidden");
  closureBar.className = `closure-bar ${err < 0.5 ? "bg-green" : err < 2 ? "bg-gold" : "bg-red"}`;
  closureText.innerHTML = `<span class="${cls}">${err < 0.01 ? " Perfect closure" : ` ${err.toFixed(4)} ft error`}</span> &nbsp;&nbsp; ${calls.length} calls`;

  tbody.innerHTML = calls.map((c, i) => `
    <tr class="call-row">
      <td class="text-text3 text-center">${i + 1}</td>
      <td><input class="inp" style="font-family:monospace;font-size:11px;padding:4px 6px" value="${escHtml(c.bearing_label)}"
        onchange="updateCallField(${i},'bearing_label',this.value)"></td>
      <td><input class="inp" type="number" step="0.001" style="font-size:11px;padding:4px 6px" value="${c.distance}"
        onchange="updateCallField(${i},'distance',parseFloat(this.value)||0)"></td>
      <td><button class="btn btn-outline btn-sm" style="color:#ff7b72;padding:2px 8px" onclick="deleteCall(${i})">✗</button></td>
    </tr>
  `).join("");

  updateS6Sketch();
}

function updateCallField(idx, field, value) {
  if (!state.parsedCalls[idx]) return;
  state.parsedCalls[idx][field] = value;
  if (field === "bearing_label") {
    const m = value.trim().toUpperCase().match(/^([NS])(\d+)°(\d+)'(\d+)"([EW])$/);
    if (m) {
      const [, ns, deg, mn, sec, ew] = m;
      let az = +deg + +mn / 60 + +sec / 3600;
      if (ns === "S" && ew === "E") az = 180 - az;
      else if (ns === "S" && ew === "W") az = 180 + az;
      else if (ns === "N" && ew === "W") az = 360 - az;
      state.parsedCalls[idx].azimuth = +az.toFixed(6);
    }
  }
  recalcS6Closure();
  updateS6Sketch();
}

function deleteCall(idx) {
  state.parsedCalls.splice(idx, 1);
  renderS6CallsTable({ closure_err: 0 });
}

function clearAllCalls() {
  state.parsedCalls = [];
  renderS6CallsTable({});
}

function addManualCall() {
  state.parsedCalls.push({ bearing_label: "N00°00'00\"E", azimuth: 0, distance: 0, bearing_raw: "" });
  renderS6CallsTable({ closure_err: 0 });
}

function recalcS6Closure() {
  const calls = state.parsedCalls;
  if (!calls.length) return;
  let x = 0, y = 0;
  calls.forEach(c => { const az = c.azimuth * Math.PI / 180; x += c.distance * Math.sin(az); y += c.distance * Math.cos(az); });
  const err = Math.hypot(x, y);
  const txt = document.getElementById("s6ClosureText");
  if (txt) {
    const cls = err < 0.5 ? "text-accent2" : err < 2 ? "text-gold" : "text-danger";
    txt.innerHTML = `<span class="${cls}">${err < 0.01 ? " Perfect closure" : ` ${err.toFixed(4)} ft`}</span> &nbsp;&nbsp; ${calls.length} calls`;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// PLAT REFERENCE TABLE  (auto-generated list of all documents referenced)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Build and render the plat reference table from the research session.
 * Collects all deeds, plats, and cabinet files referenced in this survey
 * and formats them as a numbered table ready for the finished plat.
 */
function refreshRefTable() {
  const tbody = document.getElementById('s6RefTbody');
  if (!tbody) return;
  const rs = state.researchSession;
  if (!rs || !rs.subjects?.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-cell">No research session — start one in Step 1.</td></tr>';
    return;
  }

  const refs = []; // {type, owner, doc_no, book_page, cabinet, date, relationship}

  for (const subj of rs.subjects) {
    const isClient = subj.type === 'client';
    const relationship = isClient ? '★ Client Property' : 'Adjoiner';

    // ── Deed reference ──
    if (subj.deed_saved) {
      const detail = subj.detail || {};
      const docNo = subj.doc_no || detail['DocumentNumber'] || detail['doc_no'] || '';
      const location = detail['Location'] || detail['Reference'] || '';
      const bookPage = _extractBookPage(location);
      const date = detail['RecordingDate'] || detail['Date'] || '';
      const cabRefs = _extractCabRefsFromDetail(detail);
      refs.push({
        type: 'Deed',
        owner: subj.name,
        doc_no: docNo,
        book_page: bookPage,
        cabinet: cabRefs,
        date: date,
        relationship: relationship,
      });
    }

    // ── Plat reference ──
    if (subj.plat_saved) {
      const platPath = subj.plat_path || '';
      const platRef = _extractPlatRef(platPath, subj);
      refs.push({
        type: 'Plat',
        owner: subj.name,
        doc_no: platRef.doc_no,
        book_page: platRef.book_page,
        cabinet: platRef.cabinet,
        date: '',
        relationship: relationship,
      });
    }

    // ── Plat refs from deed analysis (cross-referenced plats) ──
    if (subj.plat_refs?.length) {
      for (const pr of subj.plat_refs) {
        const prName = typeof pr === 'string' ? pr : (pr.ref || pr.name || '');
        if (!prName) continue;
        // Avoid duplicates
        if (refs.some(r => r.type === 'Plat' && r.cabinet === prName && r.owner === subj.name)) continue;
        refs.push({
          type: 'Plat (Ref)',
          owner: subj.name,
          doc_no: '',
          book_page: '',
          cabinet: prName,
          date: '',
          relationship: relationship + ' (from deed)',
        });
      }
    }
  }

  // ── Also add client deed detail from session (may have more info) ──
  if (rs.client_detail && !refs.some(r => r.type === 'Deed' && r.relationship.includes('Client'))) {
    const cd = rs.client_detail;
    refs.unshift({
      type: 'Deed',
      owner: rs.client_name,
      doc_no: cd['DocumentNumber'] || state.selectedDoc?.doc_no || '',
      book_page: _extractBookPage(cd['Location'] || cd['Reference'] || ''),
      cabinet: _extractCabRefsFromDetail(cd),
      date: cd['RecordingDate'] || cd['Date'] || '',
      relationship: '★ Client Property',
    });
  }

  // ── In-memory detail as last resort for client ──
  if (state.selectedDetail && !refs.some(r => r.type === 'Deed' && r.relationship.includes('Client'))) {
    const d = state.selectedDetail;
    refs.unshift({
      type: 'Deed',
      owner: rs.client_name,
      doc_no: state.selectedDoc?.doc_no || d['DocumentNumber'] || '',
      book_page: _extractBookPage(d['Location'] || d['Reference'] || ''),
      cabinet: _extractCabRefsFromDetail(d),
      date: d['RecordingDate'] || d['Date'] || '',
      relationship: '★ Client Property',
    });
  }

  // ── Render table ──
  if (!refs.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-cell">No documents saved yet. Save deeds & plats in Steps 2–5, then return here.</td></tr>';
    return;
  }

  tbody.innerHTML = refs.map((r, i) => {
    const typeClass = r.type === 'Deed'
      ? 'background:rgba(227,197,90,.08);color:#e3c55a'
      : 'background:rgba(176,128,224,.08);color:#b080e0';
    const isClient = r.relationship.includes('Client');
    return `<tr style="${isClient ? 'background:rgba(227,197,90,.04)' : ''}">
      <td style="font-weight:700;color:var(--text3)">${i + 1}</td>
      <td><span style="font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;${typeClass}">${escHtml(r.type)}</span></td>
      <td style="font-weight:${isClient ? '700' : '600'};color:${isClient ? '#e3c55a' : 'var(--text)'}">${escHtml(r.owner)}</td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--accent2)">${escHtml(r.doc_no)}</td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:11px">${escHtml(r.book_page)}</td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#b080e0">${escHtml(r.cabinet)}</td>
      <td style="font-size:11px;color:var(--text2)">${escHtml(r.date)}</td>
      <td style="font-size:10px;color:var(--text3)">${escHtml(r.relationship)}</td>
    </tr>`;
  }).join('');

  // Store for copy/print
  state._refTableData = refs;
}

/** Extract Book/Page from a Location string like "Book 123 Page 456" */
function _extractBookPage(loc) {
  if (!loc) return '';
  const m = loc.match(/\bB(?:oo)?k\.?\s*(\d+)\s*[\/,]\s*P(?:age|g)?\.?\s*(\d+)/i)
    || loc.match(/\b(\d+)\s*\/\s*(\d+)/);
  return m ? `Bk ${m[1]} / Pg ${m[2]}` : loc.substring(0, 30);
}

/** Extract cabinet references from a deed detail object */
function _extractCabRefsFromDetail(detail) {
  if (!detail) return '';
  // Check _cab_refs field (parsed from deed text)
  if (detail._cab_refs?.length) return detail._cab_refs.join(', ');
  // Check Location for cabinet pattern
  const loc = detail['Location'] || detail['Reference'] || '';
  const cabs = [];
  const re = /\bCab(?:inet)?\s*([A-F])\s*[-–]\s*(\d+\w?)/gi;
  let m;
  while ((m = re.exec(loc)) !== null) cabs.push(`${m[1]}-${m[2]}`);
  return cabs.join(', ');
}

/** Extract plat reference info from plat path and subject data */
function _extractPlatRef(platPath, subj) {
  let cabinet = '';
  let doc_no = '';
  let book_page = '';

  // Try to extract cabinet ref from path: ".../Cabinet C/123A.pdf"
  const pathMatch = platPath.match(/Cabinet\s*([A-F])[\/\\]+(.+?)\.pdf/i);
  if (pathMatch) {
    cabinet = `${pathMatch[1]}-${pathMatch[2]}`;
  }

  // Try from plat_refs on subject
  if (subj.plat_refs?.length) {
    cabinet = cabinet || (typeof subj.plat_refs[0] === 'string'
      ? subj.plat_refs[0]
      : subj.plat_refs[0].ref || '');
  }

  // From KML data (book/page)
  if (subj.plat) {
    book_page = subj.plat;
  }

  return { doc_no, book_page, cabinet };
}

/** Copy the reference table to clipboard as tab-separated text for CAD/Excel */
function copyRefTableToClipboard() {
  const refs = state._refTableData;
  if (!refs?.length) { showToast('No references to copy — refresh first', 'warn'); return; }

  const header = '#\tType\tOwner\tDocument #\tBook/Page\tCabinet Ref\tDate\tRelationship';
  const rows = refs.map((r, i) =>
    `${i + 1}\t${r.type}\t${r.owner}\t${r.doc_no}\t${r.book_page}\t${r.cabinet}\t${r.date}\t${r.relationship}`
  );
  const text = [header, ...rows].join('\n');

  navigator.clipboard.writeText(text).then(() => {
    showToast(`📋 ${refs.length} references copied to clipboard`, 'success');
  }).catch(() => {
    // Fallback: select text in a temp textarea
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    showToast(`📋 ${refs.length} references copied to clipboard`, 'success');
  });
}

/** Print the reference table */
function printRefTable() {
  const refs = state._refTableData;
  if (!refs?.length) { showToast('No references to print — refresh first', 'warn'); return; }
  const rs = state.researchSession;

  let html = `<!DOCTYPE html><html><head><title>Reference Table — Job #${rs?.job_number || ''}</title>
  <style>
    body { font-family: Arial, sans-serif; font-size: 11px; margin: 20px; color: #000; }
    h2 { font-size: 14px; border-bottom: 2px solid #000; padding-bottom: 4px; margin-bottom: 8px; }
    .meta { font-size: 10px; color: #666; margin-bottom: 12px; }
    table { width: 100%; border-collapse: collapse; }
    th { background: #f0f0f0; font-size: 9px; text-transform: uppercase; letter-spacing: .5px; padding: 4px 6px; border: 1px solid #ccc; text-align: left; }
    td { padding: 3px 6px; border: 1px solid #ddd; font-size: 10px; }
    tr:nth-child(even) { background: #f9f9f9; }
    .client { font-weight: bold; }
    @media print { body { margin: 10px; } }
  </style></head><body>`;
  html += `<h2>DOCUMENTS REFERENCED ON THIS PLAT</h2>`;
  html += `<div class="meta">Job #${rs?.job_number || ''} — ${rs?.client_name || ''} — ${rs?.job_type || ''} — Generated ${new Date().toLocaleDateString()}</div>`;
  html += `<table><thead><tr><th>#</th><th>Type</th><th>Owner</th><th>Doc #</th><th>Book/Page</th><th>Cabinet</th><th>Date</th><th>Relationship</th></tr></thead><tbody>`;
  refs.forEach((r, i) => {
    const cls = r.relationship.includes('Client') ? ' class="client"' : '';
    html += `<tr${cls}><td>${i + 1}</td><td>${r.type}</td><td>${r.owner}</td><td>${r.doc_no}</td><td>${r.book_page}</td><td>${r.cabinet}</td><td>${r.date}</td><td>${r.relationship}</td></tr>`;
  });
  html += `</tbody></table></body></html>`;

  const win = window.open('', '_blank');
  win.document.write(html);
  win.document.close();
  setTimeout(() => win.print(), 300);
}

//  Parcels (Adjoiner boundaries) 
function renderS6ParcelList() {
  const wrap = document.getElementById("s6ParcelList");
  let html = `
    <div class="parcel-client-card">
      <div class="row-layout justify-between">
        <span class="badge badge-local">★ Client</span>
        <span class="text-xs text-text3">${state.parsedCalls.length} calls</span>
      </div>
      <div class="text-xs text-text3 mt-1">Source: Calls tab (above)</div>
    </div>`;

  state.adjoinParcels.forEach((p, pi) => {
    html += `
      <div class="parcel-card">
        <div class="row-layout justify-between mb-2">
          <span class="badge" style="background:rgba(122,79,154,.15);color:#b080e0;border-color:#7a4f9a66"> ${escHtml(p.label)}</span>
          <div class="row-layout gap-2">
            <span class="text-xs text-text3">${p.calls.length} calls ${p.calls.length ? "" : ""}</span>
            <button class="btn btn-outline btn-sm" style="color:#ff7b72;padding:2px 8px" onclick="removeAdjoinerParcel(${pi})">✗</button>
          </div>
        </div>
        ${p.deed_path
        ? `<button class="btn btn-outline btn-sm w-full mb-1" onclick="extractCallsFromPdf(${pi},'${p.deed_path.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}')"> Extract Calls from Deed PDF</button>`
        : `<span class="text-xs text-text3">No deed saved yet</span>`}
        <textarea class="inp mt-1" rows="2" id="adjText${pi}" style="font-size:11px;font-family:monospace;resize:vertical"
          placeholder="Or paste deed text here..."></textarea>
        <button class="btn btn-outline btn-sm mt-1 w-full" onclick="parseAdjoinerText(${pi})">Parse Text</button>
      </div>`;
  });

  if (!state.adjoinParcels.length) {
    html += `<div class="empty-state text-sm mt-2">No adjoiner parcels. Click "Auto-populate from Board" above.</div>`;
  }

  wrap.innerHTML = html;
}

function autoPopulateAdjoiners(silent = false) {
  if (!state.researchSession) { if (!silent) showToast("Load a session first", "warn"); return; }
  const adjs = state.researchSession.subjects.filter(s => s.type === "adjoiner");
  if (!adjs.length) { if (!silent) showToast("No adjoiners on the research board", "info"); return; }
  let added = 0;
  adjs.forEach(subj => {
    if (!state.adjoinParcels.some(p => p.label.toLowerCase() === subj.name.toLowerCase())) {
      state.adjoinParcels.push({ label: subj.name, layer: "ADJOINERS", calls: [], start_x: 0, start_y: 0, deed_path: subj.deed_path || "", plat_path: subj.plat_path || "", extracting: false });
      added++;
    }
  });
  renderS6ParcelList();
  if (!silent) showToast(added ? `${added} parcels added` : "All adjoiners already in list", added ? "success" : "info");
  else if (added) showToast(`✓ ${added} adjoiner parcel${added !== 1 ? 's' : ''} auto-populated`, "success");
}

function addAdjoinerParcel() {
  const suggestions = (state.researchSession?.subjects || []).filter(s => s.type === "adjoiner").map(s => s.name);
  const label = prompt("Adjoiner label:\n" + (suggestions.length ? "Suggestions: " + suggestions.join(", ") : "(none)"), suggestions[0] || "Adjoiner");
  if (!label) return;
  const subj = (state.researchSession?.subjects || []).find(s => s.type === "adjoiner" && s.name.toLowerCase() === label.toLowerCase());
  state.adjoinParcels.push({ label, layer: "ADJOINERS", calls: [], start_x: 0, start_y: 0, deed_path: subj?.deed_path || "", plat_path: subj?.plat_path || "", extracting: false });
  renderS6ParcelList();
}

function clearAllParcels() {
  if (!state.adjoinParcels.length) return;
  if (!confirm(`Remove all ${state.adjoinParcels.length} parcel(s)?`)) return;
  state.adjoinParcels = [];
  renderS6ParcelList();
}

function removeAdjoinerParcel(idx) {
  state.adjoinParcels.splice(idx, 1);
  renderS6ParcelList();
}

async function parseAdjoinerText(idx) {
  const txt = document.getElementById(`adjText${idx}`)?.value.trim();
  if (!txt) { showToast("Paste text first", "warn"); return; }
  const res = await apiFetch("/parse-calls", "POST", { text: txt });
  if (!res.success) { showToast("Parse error: " + res.error, "error"); return; }
  state.adjoinParcels[idx].calls = res.calls;
  renderS6ParcelList();
  showToast(`${res.count} calls parsed for ${state.adjoinParcels[idx].label}`, "success");
}

async function extractCallsFromPdf(idx, pdfPath) {
  if (!pdfPath) { showToast("No saved PDF path", "warn"); return; }
  state.adjoinParcels[idx].extracting = true;
  renderS6ParcelList();
  try {
    const res = await apiFetch("/extract-calls-from-pdf", "POST", { pdf_path: pdfPath });
    if (!res.success) { showToast("Extract failed: " + res.error, "error"); return; }
    state.adjoinParcels[idx].calls = res.calls;
    showToast(res.count ? `${res.count} calls from ${res.filename}` : "No metes & bounds found  paste manually", res.count ? "success" : "warn");
  } catch (e) {
    showToast("Error: " + e.message, "error");
  } finally {
    state.adjoinParcels[idx].extracting = false;
    renderS6ParcelList();
  }
}

//  DXF Generation 
async function doGenerateDxf() {
  const rs = state.researchSession;
  if (!rs) { showToast("Load a session first", "warn"); return; }
  if (!state.parsedCalls.length && !state.adjoinParcels.some(p => p.calls.length)) {
    showToast("No boundary calls to generate", "warn"); return;
  }

  const btn = document.getElementById("btnGenerateDxf");
  const status = document.getElementById("s6GenerateStatus");
  btn.disabled = true;
  btn.innerHTML = "Generating...";
  status.textContent = "";

  const parcels = [];
  if (state.parsedCalls.length) {
    parcels.push({ label: `Client  ${rs.client_name}`, layer: "CLIENT", calls: state.parsedCalls, start_x: 0, start_y: 0 });
  }
  state.adjoinParcels.forEach(p => {
    if (p.calls.length) parcels.push({ label: p.label, layer: p.layer || "ADJOINERS", calls: p.calls, start_x: p.start_x || 0, start_y: p.start_y || 0 });
  });

  const options = {
    draw_boundary: document.getElementById("optDrawBoundary")?.checked ?? true,
    draw_labels: document.getElementById("optDrawLabels")?.checked ?? true,
    draw_endpoints: document.getElementById("optDrawEndpoints")?.checked ?? false,
    label_size: parseFloat(document.getElementById("optLabelSize")?.value) || 2.0,
    close_tolerance: parseFloat(document.getElementById("optCloseTol")?.value) || 0.5,
  };

  try {
    const res = await apiFetch("/generate-dxf", "POST", {
      job_number: rs.job_number, client_name: rs.client_name, job_type: rs.job_type, parcels, options
    });
    if (!res.success) { showToast("DXF failed: " + res.error, "error"); status.textContent = "Error: " + res.error; return; }

    showToast(` DXF saved: ${res.filename}`, "success");
    status.innerHTML = `<span class="text-accent2"> ${escHtml(res.filename)}</span> &nbsp;
      <button class="btn btn-sm" style="background:var(--accent2);color:#fff;font-size:10px;margin-left:8px"
        onclick="downloadDxfToBrowser({saved_to:'${escHtml(res.saved_to).replace(/'/g, "\\'")}',filename:'${escHtml(res.filename).replace(/'/g, "\\'")}'})">
        ⬇ Download to My PC</button>`;
    // Auto-download to user's browser
    downloadDxfToBrowser(res);
    setTimeout(() => {
      const dir = res.saved_to.substring(0, res.saved_to.lastIndexOf("\\"));
      apiFetch("/open-folder", "POST", { path: dir }).catch(() => { });
    }, 500);
  } catch (e) {
    showToast("Error: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "Generate &amp; Save DXF";
  }
}

//  SVG Sketch 
function updateS6Sketch() {
  const calls = state.parsedCalls;
  const sketchWrap = document.getElementById("s6SketchWrap");
  const noSketch = document.getElementById("s6NoSketch");

  if (!calls.length) {
    sketchWrap.classList.add("hidden");
    noSketch.classList.remove("hidden");
    document.getElementById("s6AreaStats").textContent = "";
    return;
  }

  sketchWrap.classList.remove("hidden");
  noSketch.classList.add("hidden");

  let x = 0, y = 0;
  const pts = [[0, 0]];
  calls.forEach(c => {
    const az = c.azimuth * Math.PI / 180;
    x += c.distance * Math.sin(az);
    y += c.distance * Math.cos(az);
    pts.push([+x.toFixed(4), +y.toFixed(4)]);
  });

  // Area (Shoelace) & Perimeter
  let area = 0, perim = 0;
  for (let i = 0; i < pts.length - 1; i++) {
    area += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1];
    perim += Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]);
  }
  const last = pts[pts.length - 1];
  area += last[0] * pts[0][1] - pts[0][0] * last[1];
  area = Math.abs(area) / 2;

  document.getElementById("s6AreaStats").innerHTML =
    `<span class="text-accent2 font-bold">${(area / 43560).toFixed(4)} ac</span> &nbsp;&nbsp; ${area.toFixed(0)} sq ft &nbsp;&nbsp; Perim: ${perim.toFixed(1)} ft`;

  // SVG
  const svg = document.getElementById("s6SketchSvg");
  const W = svg.clientWidth || 420, H = 300;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);

  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const pad = 28, scaleX = (maxX === minX) ? 1 : (W - pad * 2) / (maxX - minX), scaleY = (maxY === minY) ? 1 : (H - pad * 2) / (maxY - minY);
  const scale = Math.min(scaleX, scaleY);
  const tx = p => (p[0] - minX) * scale + pad, ty = p => H - ((p[1] - minY) * scale + pad);

  const polyPts = pts.map(p => `${tx(p).toFixed(1)},${ty(p).toFixed(1)}`).join(" ");
  const isClosed = Math.hypot(last[0] - pts[0][0], last[1] - pts[0][1]) < 0.5;

  let s = `<rect width="${W}" height="${H}" fill="rgba(0,0,0,0.1)" rx="4"/>`;
  s += `<line x1="0" y1="${H / 2}" x2="${W}" y2="${H / 2}" stroke="#ffffff06" stroke-width="1"/>`;
  s += `<line x1="${W / 2}" y1="0" x2="${W / 2}" y2="${H}" stroke="#ffffff06" stroke-width="1"/>`;
  if (!isClosed) s += `<line x1="${tx(last).toFixed(1)}" y1="${ty(last).toFixed(1)}" x2="${tx(pts[0]).toFixed(1)}" y2="${ty(pts[0]).toFixed(1)}" stroke="#ff7b72" stroke-width="1.5" stroke-dasharray="4,3" opacity=".7"/>`;
  s += `<polygon points="${polyPts}" fill="rgba(45,138,110,0.1)" stroke="#2d8a6e" stroke-width="2" stroke-linejoin="round"/>`;
  s += `<circle cx="${tx(pts[0]).toFixed(1)}" cy="${ty(pts[0]).toFixed(1)}" r="5" fill="#2d8a6e" stroke="#56d3a0" stroke-width="1.5"/>`;
  if (!isClosed) s += `<circle cx="${tx(last).toFixed(1)}" cy="${ty(last).toFixed(1)}" r="5" fill="#ff7b72" opacity=".8"/>`;
  // North arrow
  s += `<text x="${W - 16}" y="22" font-size="11" fill="#79a8e0" font-family="monospace" text-anchor="middle">N</text>`;
  s += `<line x1="${W - 16}" y1="26" x2="${W - 16}" y2="42" stroke="#79a8e0" stroke-width="1.5"/>`;
  s += `<polygon points="${W - 16},26 ${W - 20},34 ${W - 12},34" fill="#79a8e0"/>`;
  svg.innerHTML = s;
}
// 
// SETTINGS MODAL
// 
function showSettingsModal() {
  document.getElementById("settingsOverlay").classList.remove("hidden");
  loadDriveStatus(); // refresh drive status every time modal opens
}
function closeSettingsModal() {
  document.getElementById("settingsOverlay").classList.add("hidden");
}

function toggleArcgisSection() {
  const sec = document.getElementById("arcgisSection");
  const chev = document.getElementById("arcgisSectionChevron");
  if (!sec) return;
  const open = sec.style.display !== "none";
  sec.style.display = open ? "none" : "block";
  if (chev) chev.style.transform = open ? "" : "rotate(180deg)";
}

async function loadDriveStatus() {
  const dot = document.getElementById("driveStatusDot");
  const text = document.getElementById("driveStatusText");
  if (!dot || !text) return;
  text.textContent = "Checking...";
  dot.style.background = "var(--text3)";
  try {
    const res = await apiFetch("/drive-status");
    updateDriveStatusUI(res);
  } catch (e) {
    text.textContent = "Cannot reach server";
    dot.style.background = "var(--danger)";
  }
}

function updateDriveStatusUI(res) {
  const dot = document.getElementById("driveStatusDot");
  const text = document.getElementById("driveStatusText");
  if (!dot || !text) return;
  if (res.drive_ok) {
    dot.style.background = "var(--success2)";
    dot.style.boxShadow = "0 0 6px var(--success2)";
    text.innerHTML = `<span style="color:var(--accent2);font-weight:700">${res.drive}:\\</span> &nbsp; <span style="color:var(--text3);font-size:12px">${res.survey_path}</span>`;
    document.getElementById("driveOverrideInput").value = res.drive || "";
  } else {
    dot.style.background = "var(--danger)";
    dot.style.boxShadow = "none";
    text.innerHTML = `<span style="color:#ff7b72">Drive not found</span> <span style="color:var(--text3);font-size:12px"> — plug in the drive then click ⟳ Rescan</span>`;
  }
}

async function rescanDrive() {
  const btn = document.getElementById("btnRescanDrive");
  const text = document.getElementById("driveStatusText");
  btn.disabled = true;
  btn.textContent = "Scanning...";
  text.textContent = "Scanning all drives...";
  try {
    const res = await apiFetch("/drive-status?rescan=1");
    updateDriveStatusUI(res);
    if (res.drive_ok) showToast(`Drive found: ${res.drive}:\\`, "success");
    else showToast("Drive not found. Plug it in and try again.", "warn");
  } catch (e) {
    showToast("Scan error: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "⟳ Rescan";
  }
}

async function pinDrive(clear = false) {
  const letter = clear ? "" : (document.getElementById("driveOverrideInput")?.value.trim() || "");
  try {
    const res = await apiFetch("/drive-override", "POST", { drive: letter });
    if (res.success) {
      await loadDriveStatus();
      showToast(letter ? `Drive pinned to ${letter}:\\` : "Drive set to auto-detect", "success");
    } else {
      showToast("Override failed: " + res.error, "error");
    }
  } catch (e) {
    showToast("Error: " + e.message, "error");
  }
}

async function saveConfig() {
  const url = document.getElementById("cfgUrl").value.trim();
  const user = document.getElementById("cfgUser").value.trim();
  const pass = document.getElementById("cfgPass").value;
  const status = document.getElementById("cfgStatus");

  if (!user || !pass) { showToast("Enter username and password", "warn"); return; }

  const btn = document.getElementById("btnSaveConfig");
  btn.disabled = true;
  btn.innerHTML = "Connecting...";
  status.textContent = "";

  try {
    const res = await apiFetch("/config", "POST", {
      firstnm_url: url, firstnm_user: user, firstnm_pass: pass
    });
    if (!res.success) { showToast("Config save failed: " + res.error, "error"); return; }

    // Now login
    const loginRes = await apiFetch("/login", "POST", { url, username: user, password: pass });
    if (loginRes.success) {
      state.loggedIn = true;
      setStatusDot("online", "Connected");
      showToast("Connected to records!", "success");
      status.textContent = " Connected";
      closeSettingsModal();
    } else {
      showToast("Login failed: " + loginRes.error, "error");
      status.textContent = "Login failed: " + loginRes.error;
    }
  } catch (e) {
    showToast("Connection error: " + e.message, "error");
    status.textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.innerHTML = "Connect";
  }
}

// 
// LOGIN & CONNECTION
// 
async function checkLogin() {
  try {
    const url = document.getElementById("cfgUrl").value.trim();
    const user = document.getElementById("cfgUser").value.trim();
    const pass = document.getElementById("cfgPass").value;
    if (!user || !pass) {
      setStatusDot("offline", "Click Settings to connect");
      // Only auto-open modal on truly first run (no stored credentials)
      showSettingsModal();
      return;
    }
    setStatusDot("loading", "Connecting...");
    const res = await apiFetch("/login", "POST", { url, username: user, password: pass });
    if (res.success) {
      state.loggedIn = true;
      setStatusDot("online", "Connected");
    } else {
      setStatusDot("offline", "Login failed  check Settings");
      // Don't auto-open modal; user can click Settings button
      showToast("Login failed: " + (res.error || "Check username/password in Settings"), "warn");
    }
  } catch (e) {
    setStatusDot("offline", "Offline");
  }
}

function setStatusDot(mode, text) {
  const dot = document.querySelector(".status-dot");
  const span = document.getElementById("statusText");
  if (dot) dot.className = `status-dot ${mode}`;
  if (span) span.textContent = text;
}

// 
// GLOBAL PROGRESS FOOTER
// 
function updateGlobalProgress() {
  const rs = state.researchSession;
  if (!rs) return;

  const all = rs.subjects;
  const deeds = all.filter(s => s.deed_saved).length;
  const plats = all.filter(s => s.plat_saved).length;
  const total = all.length * 2; // each subject needs deed + plat
  const done = deeds + plats;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  // Reveal the footer stats bar (hidden on first load until a session exists)
  const statsBar = document.getElementById("footerStats");
  if (statsBar) statsBar.classList.remove("hidden");

  document.getElementById("statDeeds").textContent = `${deeds}/${all.length}`;
  document.getElementById("statPlats").textContent = `${plats}/${all.length}`;
  document.getElementById("globalProgressFill").style.width = pct + "%";
}

// 
// EXPORT
// 
async function exportSession() {
  const rs = state.researchSession;
  if (!rs) { showToast("No session loaded", "warn"); return; }

  const line = (ch, len = 72) => ch.repeat(len);
  const pad = (label, val) => `  ${(label + ':').padEnd(22)} ${val || '—'}`;
  const now = new Date().toLocaleString();

  const lines = [];
  lines.push(line('═'));
  lines.push(`  DEED & PLAT RESEARCH REPORT`);
  lines.push(`  Red Tail Surveying`);
  lines.push(line('═'));
  lines.push('');
  lines.push(pad('Job Number', rs.job_number));
  lines.push(pad('Client', rs.client_name));
  lines.push(pad('Job Type', rs.job_type));
  lines.push(pad('Generated', now));
  lines.push('');

  // ── Progress summary ──
  const subjects = rs.subjects || [];
  const deeds = subjects.filter(s => s.deed_saved).length;
  const plats = subjects.filter(s => s.plat_saved).length;
  const done = subjects.filter(s => s.deed_saved && s.plat_saved).length;
  lines.push(pad('Progress', `${done}/${subjects.length} complete  |  Deeds: ${deeds}  Plats: ${plats}`));
  lines.push('');

  // ── Deed detail (from current session state) ──
  const detail = state.selectedDetail || {};
  const clientSubj = subjects.find(s => s.type === 'client') || {};

  const grantor = detail['Grantor'] || '';
  const grantee = detail['Grantee'] || '';
  const location = detail['Location'] || detail['Book/Page'] || '';
  const recordedDate = detail['Recorded Date'] || detail['Record Date'] || detail['Instrument Date'] || '';
  const instrumentType = detail['Document Type'] || detail['Type'] || detail['Instrument Type'] || '';
  const consideration = detail['Consideration'] || detail['Amount'] || '';

  if (grantor || grantee) {
    lines.push(line('─'));
    lines.push('  CLIENT DEED INFORMATION');
    lines.push(line('─'));
    if (grantor) lines.push(pad('Grantor', grantor));
    if (grantee) lines.push(pad('Grantee', grantee));
    if (instrumentType) lines.push(pad('Instrument Type', instrumentType));
    if (location) lines.push(pad('Book / Page', location));
    if (recordedDate) lines.push(pad('Recorded Date', recordedDate));
    if (consideration) lines.push(pad('Consideration', consideration));

    // Document numbers
    ['Document Number', 'Instrument Number', 'GF Number', 'GF#', 'File Number'].forEach(k => {
      if (detail[k]) lines.push(pad(k, detail[k]));
    });

    // TRS references
    const trsRefs = detail._trs || clientSubj.trs_refs || [];
    if (trsRefs.length) {
      lines.push(pad('TRS Reference(s)', trsRefs.map(t => typeof t === 'string' ? t : t.trs).join('; ')));
      // GLO Records links
      trsRefs.forEach(t => {
        const gloUrl = buildGloUrl(t);
        if (gloUrl) lines.push(pad('GLO Survey Plat', gloUrl));
      });
    }

    // Acreage
    if (clientSubj.area_acres) {
      lines.push(pad('Area', `${clientSubj.area_acres} acres`));
    }

    lines.push('');
  }

  // ── Property description ──
  const propDesc = clientSubj.property_description || '';
  if (propDesc) {
    lines.push(line('─'));
    lines.push('  PROPERTY LEGAL DESCRIPTION');
    lines.push(line('─'));
    // Word-wrap to ~70 chars
    const words = propDesc.split(/\s+/);
    let currentLine = '  ';
    words.forEach(word => {
      if ((currentLine + ' ' + word).length > 74) {
        lines.push(currentLine);
        currentLine = '  ' + word;
      } else {
        currentLine += (currentLine.length > 2 ? ' ' : '') + word;
      }
    });
    if (currentLine.trim()) lines.push(currentLine);
    lines.push('');
  }

  // ── Plat references ──
  const platHint = state._platHint || {};
  if (platHint.cabRefs?.length || platHint.bookPageMatches?.length || platHint.platNameMatches?.length || platHint.surveyorRefs?.length) {
    lines.push(line('─'));
    lines.push('  PLAT REFERENCES (from deed)');
    lines.push(line('─'));
    if (platHint.cabRefs?.length) lines.push(pad('Cabinet Refs', platHint.cabRefs.join(', ')));
    if (platHint.bookPageMatches?.length) lines.push(pad('Book/Page Refs', platHint.bookPageMatches.join(', ')));
    if (platHint.platNameMatches?.length) lines.push(pad('Plat Name(s)', platHint.platNameMatches.join(', ')));
    if (platHint.surveyorRefs?.length) lines.push(pad('Surveyor(s)', platHint.surveyorRefs.join(', ')));
    lines.push('');
  }

  // ── Bearing / distance calls table ──
  const calls = state.parsedCalls || [];
  if (calls.length) {
    lines.push(line('─'));
    lines.push('  METES & BOUNDS CALLS');
    lines.push(line('─'));
    lines.push(`  ${'#'.padStart(3)}  ${'Bearing'.padEnd(24)}  ${'Distance'.padStart(12)}  Type`);
    lines.push(`  ${'-'.repeat(3)}  ${'-'.repeat(24)}  ${'-'.repeat(12)}  ${'-'.repeat(8)}`);
    calls.forEach((c, i) => {
      const num = String(i + 1).padStart(3);
      const brg = (c.bearing_label || c.bearing_raw || '—').padEnd(24);
      const dist = (c.distance?.toFixed(2) + "'").padStart(12);
      const type = (c.type || 'straight').padEnd(8);
      lines.push(`  ${num}  ${brg}  ${dist}  ${type}`);
    });

    // Closure error
    let closureErr = 0;
    if (calls.length >= 2) {
      let x = 0, y = 0;
      calls.forEach(c => {
        const az = (c.azimuth || 0) * Math.PI / 180;
        const d = c.distance || 0;
        x += d * Math.sin(az);
        y += d * Math.cos(az);
      });
      closureErr = Math.hypot(x, y);
    }
    lines.push('');
    lines.push(pad('Total Calls', calls.length));
    lines.push(pad('Closure Error', closureErr < 0.01 ? 'PERFECT CLOSURE' : `${closureErr.toFixed(4)} ft`));

    // Area from shoelace
    let x = 0, y = 0;
    const pts = [[0, 0]];
    calls.forEach(c => {
      const az = (c.azimuth || 0) * Math.PI / 180;
      x += (c.distance || 0) * Math.sin(az);
      y += (c.distance || 0) * Math.cos(az);
      pts.push([x, y]);
    });
    let area = 0;
    for (let i = 0; i < pts.length - 1; i++) {
      area += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1];
    }
    area += pts[pts.length - 1][0] * pts[0][1] - pts[0][0] * pts[pts.length - 1][1];
    area = Math.abs(area) / 2;
    lines.push(pad('Computed Area', `${(area / 43560).toFixed(4)} acres  (${area.toFixed(0)} sq ft)`));
    lines.push('');
  }

  // ── Subject table ──
  lines.push(line('─'));
  lines.push('  RESEARCH SUBJECTS');
  lines.push(line('─'));
  lines.push(`  ${'Type'.padEnd(10)} ${'Name'.padEnd(30)} ${'Deed'.padEnd(6)} ${'Plat'.padEnd(6)} Status`);
  lines.push(`  ${'-'.repeat(10)} ${'-'.repeat(30)} ${'-'.repeat(6)} ${'-'.repeat(6)} ${'-'.repeat(10)}`);
  subjects.forEach(s => {
    const type = (s.type || 'other').toUpperCase().padEnd(10);
    const name = (s.name || '—').padEnd(30).substring(0, 30);
    const deed = (s.deed_saved ? '  ✓ ' : '  ✗ ').padEnd(6);
    const plat = (s.plat_saved ? '  ✓ ' : '  ✗ ').padEnd(6);
    const status = (s.status || 'pending').toUpperCase();
    lines.push(`  ${type} ${name} ${deed} ${plat} ${status}`);
    if (s.notes) lines.push(`           Notes: ${s.notes}`);
  });
  lines.push('');

  // ── Footer ──
  lines.push(line('═'));
  lines.push(`  End of Report — Job #${rs.job_number} ${rs.client_name}`);
  lines.push(line('═'));

  const text = lines.join('\n');
  const blob = new Blob([text], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `Job${rs.job_number}_${rs.client_name.replace(/[^a-zA-Z0-9]/g, '_')}_Research_Report.txt`;
  a.click();
  URL.revokeObjectURL(url);
  showToast("Research report exported", "success");
}

// ─────────────────────────────────────────────────────────────────────────────
// UNIFIED PARCEL MAP PICKER  (Client Property + Adjoiners — single Leaflet map)
// ─────────────────────────────────────────────────────────────────────────────

const _propPicker = {
  map: null,   // Leaflet map instance
  parcelLayer: null,   // GeoJSON layer
  selectedLayer: null,   // currently clicked polygon layer
  selectedProps: null,   // properties of selected feature
  geojsonData: null,   // cached GeoJSON FeatureCollection
  searchTimer: null,
  // Stores the chosen parcel so startSession can read it
  confirmedParcel: null,
  // Source layer filter — default to '' (all layers)
  sourceFilter: '',
  availableSources: [],
  // ── Adjoiner picking (merged from old _kmlMap) ──
  highlightUpcs: [],     // UPCs to mark as "client" (gold)
  mapAddedNames: [],     // adjoiner parcels added this session via the picker [{name, upc, plat}]
};

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Called when the user types in the manual client name field */
function onClientNameTyped() {
  // Clear any map-confirmed selection so the typed name takes precedence
  const typed = document.getElementById('setupClient').value.trim();
  if (typed) {
    _propPicker.confirmedParcel = null;
    document.getElementById('selectedParcelCard').classList.add('hidden');
  }
  // KG autocomplete
  clearTimeout(onClientNameTyped._debounce);
  onClientNameTyped._debounce = setTimeout(() => _kgClientSuggest(typed), 350);
}

/** Clear the KML-confirmed parcel selection */
function clearPropertySelection() {
  _propPicker.confirmedParcel = null;
  document.getElementById('selectedParcelCard').classList.add('hidden');
  document.getElementById('setupClient').value = '';
  // Also clear client_upc from session so it doesn't flow into downstream searches
  if (state.researchSession) {
    state.researchSession.client_upc = '';
  }
}

// ── Open / close modal ────────────────────────────────────────────────────────

async function showPropertyPicker() {
  document.getElementById('propPickerOverlay').classList.remove('hidden');

  // Gather UPCs already matched to the client (from KML hits in step 3)
  const clientUpcs = (state._kmlHits || []).map(h => h.upc).filter(Boolean);
  if (_propPicker.confirmedParcel && _propPicker.confirmedParcel.upc) {
    if (!clientUpcs.includes(_propPicker.confirmedParcel.upc)) {
      clientUpcs.push(_propPicker.confirmedParcel.upc);
    }
  }
  _propPicker.highlightUpcs = clientUpcs;

  // Sync the "Adjoiners Added" sidebar list from the session's existing
  // subjects so the visual list is accurate when re-opening the picker.
  if (state.researchSession) {
    const boardAdj = state.researchSession.subjects
      .filter(s => s.type === 'adjoiner')
      .map(s => s.name);
    // Merge: keep any queued-but-not-yet-flushed names, add board names
    boardAdj.forEach(n => {
      const alreadyQueued = _propPicker.mapAddedNames.some(e => (typeof e === 'object' ? e.name : e) === n);
      if (!alreadyQueued) _propPicker.mapAddedNames.push({ name: n, upc: '', plat: '' });
    });
    _updatePickerAddedList();
  }

  // Update action hint based on whether session is active
  const hintEl = document.getElementById('pickerActionHint');
  if (hintEl) {
    hintEl.textContent = state.researchSession
      ? 'Select your property or add neighboring parcels as adjoiners.'
      : 'Pick your property first, then click "Start Research Session". Adjoiners picked here will be added automatically.';
  }

  if (!_propPicker.map) {
    _initPropPickerMap();
  } else {
    setTimeout(() => _propPicker.map && _propPicker.map.invalidateSize(), 150);
  }

  await _loadPropPickerMapData();
}

function closePropPicker() {
  document.getElementById('propPickerOverlay').classList.add('hidden');
}

// ── Initialise Leaflet ────────────────────────────────────────────────────────

function _initPropPickerMap() {
  const container = document.getElementById('propPickerLeafletMap');
  const canvasRenderer = L.canvas({ padding: 0.5 });

  // ── ArcGIS Basemap Tile Layers (free, no API key required) ────────────
  const arcgisAttr = '&copy; <a href="https://www.esri.com">Esri</a>, Maxar, Earthstar Geographics';

  const imagery = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    { attribution: arcgisAttr, maxZoom: 19 }
  );

  const topo = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
    { attribution: '&copy; Esri, HERE, Garmin, USGS', maxZoom: 19 }
  );

  const streets = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
    { attribution: '&copy; Esri, HERE, Garmin', maxZoom: 19 }
  );

  // Reference label overlay — road/place names on top of satellite imagery
  const referenceLabels = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
    { maxZoom: 19, pane: 'overlayPane', opacity: 0.85 }
  );

  // Transportation lines overlay (roads visible on imagery)
  const transportOverlay = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}',
    { maxZoom: 19, pane: 'overlayPane', opacity: 0.65 }
  );

  _propPicker.map = L.map(container, {
    center: [36.6, -105.5],
    zoom: 11,
    preferCanvas: true,
    zoomControl: true,
    layers: [imagery, referenceLabels, transportOverlay],  // default: satellite + labels + roads
  });
  _propPicker.renderer = canvasRenderer;

  // Create dedicated panes with z-index ABOVE everything including
  // reference labels (shadowPane=700, markerPane=600, popupPane=700)
  // so parcel lines never get buried under any basemap layer.
  _propPicker.map.createPane('parcelsPane');
  _propPicker.map.getPane('parcelsPane').style.zIndex = 625;
  _propPicker.map.getPane('parcelsPane').style.pointerEvents = 'none';

  // Pane for the ArcGIS-based highlight polygon (above parcel lines)
  _propPicker.map.createPane('highlightPane');
  _propPicker.map.getPane('highlightPane').style.zIndex = 650;
  _propPicker.map.getPane('highlightPane').style.pointerEvents = 'none';

  // ── BLM PLSS Grid Overlay (Township/Range/Section boundaries) ─────────
  // The BLM CadNSDI MapServer is DYNAMIC (singleFusedMapCache: false),
  // so regular L.tileLayer with /tile/ URLs won't work.
  // Instead we use L.TileLayer with a custom getTileUrl that calls /export
  // for each tile's bounding box — this gives us proper dynamic rendering.
  const _PLSS_BASE = 'https://gis.blm.gov/arcgis/rest/services/Cadastral/BLM_Natl_PLSS_CadNSDI/MapServer';

  /**
   * Create a dynamic PLSS layer that renders via ArcGIS MapServer /export.
   * @param {string} layerIds - comma-separated layer IDs to show (e.g. "1" or "1,2")
   * @param {number} opacity  - layer opacity (0–1)
   * @param {number} minZoom  - don't render below this zoom
   */
  function _createPlssDynamicLayer(layerIds, opacity, minZoom) {
    const PlssDynamic = L.GridLayer.extend({
      createTile: function(coords) {
        const tile = document.createElement('img');
        tile.setAttribute('role', 'presentation');
        tile.style.width = this.getTileSize().x + 'px';
        tile.style.height = this.getTileSize().y + 'px';

        if (coords.z < minZoom) { return tile; }

        // Convert tile coords to lat/lng bounds
        const nw = this._map.unproject([coords.x * 256, coords.y * 256], coords.z);
        const se = this._map.unproject([(coords.x + 1) * 256, (coords.y + 1) * 256], coords.z);

        // ArcGIS export expects xmin,ymin,xmax,ymax in Web Mercator (3857)
        // But we can use 4326 with bboxSR=4326
        const bbox = `${se.lng},${se.lat},${nw.lng},${nw.lat}`;
        const url = `${_PLSS_BASE}/export?` +
          `bbox=${bbox}&bboxSR=4326&imageSR=4326` +
          `&size=256,256&dpi=96` +
          `&format=png32&transparent=true` +
          `&layers=show:${layerIds}` +
          `&f=image`;

        tile.src = url;
        tile.onerror = () => { tile.src = ''; };  // graceful fail
        return tile;
      }
    });
    return new PlssDynamic({
      opacity: opacity,
      pane: 'overlayPane',
      maxZoom: 19,
      minZoom: minZoom,
      attribution: '© <a href="https://www.blm.gov">BLM</a> Cadastral Survey',
    });
  }

  // Layer 1: PLSS Townships (visible zoom ≥ 9)
  const plssTownships = _createPlssDynamicLayer('1', 0.55, 9);
  // Layer 2: PLSS Sections (visible zoom ≥ 11)
  const plssSections = _createPlssDynamicLayer('2', 0.50, 11);
  // Layer 3: PLSS Quarter Sections / Intersected (visible zoom ≥ 14)
  const plssQuarters = _createPlssDynamicLayer('3', 0.45, 14);

  // ── ArcGIS Parcel Boundaries via Esri Leaflet (authoritative, pixel-perfect) ──
  // L.esri.dynamicMapLayer handles all the complexity of MapServer /export:
  // proper bounding boxes, scale detection, dynamic symbology, and tile alignment.
  const _PARCELS_URL = 'https://gis.ose.nm.gov/server_s/rest/services/Parcels/County_Parcels_2025/MapServer';

  const arcgisParcelsLayer = L.esri.dynamicMapLayer({
    url: _PARCELS_URL,
    layers: [29],          // Taos County only
    opacity: 1.0,
    pane: 'parcelsPane',
    minZoom: 13,
    maxZoom: 19,
    f: 'image',
    format: 'png32',
    attribution: '© <a href="https://gis.ose.nm.gov">NM OSE</a> County Parcels 2025',
    // Custom symbology: bright red outlines matching Google Earth
    // Must be a JSON *string* for esri-leaflet to pass it correctly to the /export URL
    dynamicLayers: JSON.stringify([{
      id: 29,
      source: { type: 'mapLayer', mapLayerId: 29 },
      drawingInfo: {
        renderer: {
          type: 'simple',
          symbol: {
            type: 'esriSFS',
            style: 'esriSFSSolid',
            color: [0, 0, 0, 0],       // transparent fill
            outline: {
              type: 'esriSLS',
              style: 'esriSLSSolid',
              color: [220, 40, 40, 255], // bright red — matches Google Earth KML lines
              width: 2
            }
          }
        }
      }
    }]),
  });

  // Add ArcGIS parcels to map by default
  arcgisParcelsLayer.addTo(_propPicker.map);

  // ── Water Rights (NM OSE Points of Diversion) dynamic layer ─────────
  const _waterRightsGroup = L.layerGroup();
  let _wrFetchDebounce = null;

  function _fetchWaterRights() {
    if (!_propPicker.map.hasLayer(_waterRightsGroup)) return;
    const bounds = _propPicker.map.getBounds();
    const url = `/api/map-layers/water-rights?minLat=${bounds.getSouth().toFixed(4)}&maxLat=${bounds.getNorth().toFixed(4)}&minLon=${bounds.getWest().toFixed(4)}&maxLon=${bounds.getEast().toFixed(4)}`;
    fetch(url).then(r => r.json()).then(data => {
      _waterRightsGroup.clearLayers();
      if (!data.features) return;
      const statusColors = { ACT: '#0078ff', PEN: '#4ce600', PLG: '#ff3333', CAP: '#ffaa00', INC: '#999', CLW: '#e6e600', UNK: '#555' };
      data.features.forEach(f => {
        const color = statusColors[f.status] || '#0078ff';
        const marker = L.circleMarker([f.lat, f.lon], {
          radius: 5, fillColor: color, color: '#000', weight: 1, fillOpacity: 0.8
        });
        const useLabel = f.use || 'Unknown';
        const ditchLine = f.ditch ? `<br><b>Ditch:</b> ${f.ditch}` : '';
        const trsLine = f.trs ? `<br><b>TRS:</b> ${f.trs}` : '';
        marker.bindPopup(
          `<div style="font-size:12px;min-width:180px">` +
          `<b style="color:${color}">💧 ${f.pod_file || 'POD'}</b>` +
          `<br><b>Name:</b> ${f.name || '—'}` +
          `<br><b>Owner:</b> ${f.owner || '—'}` +
          `<br><b>Status:</b> ${f.status}` +
          `<br><b>Use:</b> ${useLabel}` +
          `${f.depth ? '<br><b>Depth:</b> ' + f.depth + ' ft' : ''}` +
          `${ditchLine}${trsLine}` +
          `</div>`, { maxWidth: 260 }
        );
        _waterRightsGroup.addLayer(marker);
      });
    }).catch(() => {});
  }

  _propPicker.map.on('moveend', () => {
    clearTimeout(_wrFetchDebounce);
    _wrFetchDebounce = setTimeout(_fetchWaterRights, 400);
  });
  _waterRightsGroup.on('add', () => { _fetchWaterRights(); });

  // ── NGS Geodetic Survey Marks dynamic layer ───────────────────────────
  const _surveyMarksGroup = L.layerGroup();
  let _smFetchDebounce = null;

  function _fetchSurveyMarks() {
    if (!_propPicker.map.hasLayer(_surveyMarksGroup)) return;
    const center = _propPicker.map.getCenter();
    const zoom = _propPicker.map.getZoom();
    const radius = zoom >= 14 ? 2 : zoom >= 12 ? 4 : zoom >= 10 ? 7 : 10;
    const url = `/api/map-layers/survey-marks?lat=${center.lat.toFixed(5)}&lon=${center.lng.toFixed(5)}&radius=${radius}`;
    fetch(url).then(r => r.json()).then(data => {
      _surveyMarksGroup.clearLayers();
      if (!data.marks) return;
      const condColors = { GOOD: '#4ce600', MONUMENTED: '#4ce600', 'MARK NOT FOUND': '#ff7b72', 'SEE DESCRIPTION': '#ffaa00' };
      data.marks.forEach(m => {
        const color = condColors[m.condition] || '#79a8e0';
        const icon = L.divIcon({
          className: '',
          html: `<div style="width:12px;height:12px;transform:rotate(45deg);background:${color};border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.5);margin:-6px 0 0 -6px"></div>`,
          iconSize: [12, 12], iconAnchor: [6, 6]
        });
        const marker = L.marker([m.lat, m.lon], { icon });
        const htLine = m.orthoHt && m.orthoHt.trim() ? `<br><b>Elev:</b> ${m.orthoHt} m (${m.vertDatum})` : '';
        marker.bindPopup(
          `<div style="font-size:12px;min-width:200px">` +
          `<b style="color:${color}">🔺 ${m.pid}</b> — ${m.name}` +
          `<br><b>Type:</b> ${m.monumentType}` +
          `<br><b>Stamping:</b> ${m.stamping || '—'}` +
          `<br><b>Setting:</b> ${m.setting || '—'}` +
          `<br><b>Condition:</b> ${m.condition}` +
          `<br><b>Last Recovered:</b> ${m.lastRecovered || '—'}` +
          `${htLine}` +
          `<br><b>Stability:</b> ${m.stability || '—'}` +
          `${m.satUse ? '<br><b>GPS:</b> ✅ Satellite-observed' : ''}` +
          `<br><a href="${m.datasheet_url}" target="_blank" style="color:#79a8e0">📄 View NGS Datasheet</a>` +
          `</div>`, { maxWidth: 300 }
        );
        _surveyMarksGroup.addLayer(marker);
      });
    }).catch(() => {});
  }

  _propPicker.map.on('moveend', () => {
    clearTimeout(_smFetchDebounce);
    _smFetchDebounce = setTimeout(_fetchSurveyMarks, 500);
  });
  _surveyMarksGroup.on('add', () => { _fetchSurveyMarks(); });

  // Layer control — basemaps + overlays
  const baseMaps = {
    '🛰️ Satellite': imagery,
    '🗺️ Topographic': topo,
    '🏙️ Streets': streets,
  };
  const overlays = {
    '🏷️ Place Names': referenceLabels,
    '🛣️ Roads': transportOverlay,
    '🏠 Parcels (ArcGIS)': arcgisParcelsLayer,
    '📐 Townships (PLSS)': plssTownships,
    '📐 Sections (PLSS)': plssSections,
    '📐 Quarter Sec (PLSS)': plssQuarters,
    '💧 Water Rights (POD)': _waterRightsGroup,
    '🔺 Survey Marks (NGS)': _surveyMarksGroup,
  };
  L.control.layers(baseMaps, overlays, {
    position: 'topright',
    collapsed: true,
  }).addTo(_propPicker.map);

  // Move zoom control to bottom-right so it doesn't overlap layer control
  _propPicker.map.zoomControl.setPosition('bottomright');

  // ── Scale bar (imperial — feet/miles, essential for surveying) ────────
  L.control.scale({
    position: 'bottomleft',
    imperial: true,
    metric: false,
    maxWidth: 180,
  }).addTo(_propPicker.map);

  // ── Live coordinate display on mouse move ────────────────────────────
  const coordDiv = L.DomUtil.create('div', 'map-coord-display');
  coordDiv.style.cssText =
    'position:absolute;bottom:8px;left:50%;transform:translateX(-50%);z-index:800;' +
    'background:rgba(13,17,23,0.85);color:#a8d8c4;font-family:"JetBrains Mono",monospace;' +
    'font-size:11px;padding:4px 12px;border-radius:6px;pointer-events:none;' +
    'border:1px solid rgba(64,194,159,0.2);backdrop-filter:blur(6px);white-space:nowrap;' +
    'transition:opacity .15s;opacity:0;';
  container.appendChild(coordDiv);

  _propPicker.map.on('mousemove', e => {
    const lat = e.latlng.lat.toFixed(6);
    const lng = e.latlng.lng.toFixed(6);
    coordDiv.textContent = `${lat}°N  ${lng}°W`;
    coordDiv.style.opacity = '1';
  });
  _propPicker.map.on('mouseout', () => {
    coordDiv.style.opacity = '0';
  });

  // ── Full-screen toggle button ────────────────────────────────────────
  const FullscreenControl = L.Control.extend({
    options: { position: 'topleft' },
    onAdd: function() {
      const btn = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
      btn.innerHTML = '<a href="#" title="Toggle fullscreen" style="font-size:18px;line-height:30px;width:30px;height:30px;display:block;text-align:center;text-decoration:none">⛶</a>';
      btn.style.cursor = 'pointer';
      L.DomEvent.on(btn, 'click', e => {
        L.DomEvent.preventDefault(e);
        const panel = document.getElementById('propPickerPanel');
        if (!panel) return;
        const isFs = panel.style.top === '0px';
        if (isFs) {
          panel.style.cssText = 'position:fixed;top:4vh;left:3vw;right:3vw;bottom:4vh;display:flex;flex-direction:column;z-index:1200;border-radius:16px;overflow:hidden;box-shadow:0 24px 80px rgba(0,0,0,.8)';
        } else {
          panel.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;display:flex;flex-direction:column;z-index:1200;border-radius:0;overflow:hidden;box-shadow:none';
        }
        setTimeout(() => _propPicker.map && _propPicker.map.invalidateSize(), 200);
      });
      return btn;
    }
  });
  new FullscreenControl().addTo(_propPicker.map);
}

// ── Load GeoJSON from backend ─────────────────────────────────────────────────

async function _loadPropPickerMapData() {
  const countEl = document.getElementById('propPickerSearchCount');
  const loaderEl = document.getElementById('propPickerMapLoader');
  countEl.textContent = 'Loading…';
  if (loaderEl) loaderEl.classList.remove('hidden');
  try {
    const res = await apiFetch('/xml/map-geojson', 'POST', {
      highlight_upcs: _propPicker.highlightUpcs || [],
      max_features: 100000,
      source_filter: _propPicker.sourceFilter || '',
    });
    if (!res.success) {
      countEl.textContent = 'Error: ' + (res.error || 'Unknown');
      return;
    }
    if (!res.total) {
      countEl.innerHTML = '<span style="color:#ff7b72">No parcels in index.</span> ' +
        '<button class="btn btn-outline btn-sm" style="font-size:10px;padding:2px 8px;margin-left:6px" ' +
        'onclick="showKmlIndexModal()">🗺️ Build KML Index</button>';
      return;
    }
    _propPicker.geojsonData = res.geojson;
    countEl.textContent = res.total.toLocaleString() + ' parcels';

    // Populate source layer dropdown (only once on first load, or when sources change)
    if (res.sources && res.sources.length) {
      _propPicker.availableSources = res.sources;
      _populateSourceDropdown('propPickerSourceFilter', res.sources, _propPicker.sourceFilter);
    }

    _renderPropPickerLayer();
  } catch (e) {
    countEl.textContent = 'Load failed: ' + e.message;
  } finally {
    if (loaderEl) loaderEl.classList.add('hidden');
  }
}

/** Called when the user changes the Layer dropdown in the Property Picker */
function onPropPickerSourceChange(value) {
  _propPicker.sourceFilter = value;
  _loadPropPickerMapData();
}

/** Populate a source dropdown with available KML/KMZ source file names */
function _populateSourceDropdown(selectId, sources, currentValue) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  // Preserve the "All Layers" option and rebuild the rest
  sel.innerHTML = '<option value="">All Layers</option>';
  sources.forEach(src => {
    // Clean up display name: strip folder path from KMZ sources like "Parcel_Maintenance.kmz/doc.kml"
    const displayName = src.includes('/') ? src.split('/')[0] : src;
    const opt = document.createElement('option');
    opt.value = src;
    opt.textContent = displayName;
    if (currentValue === src) opt.selected = true;
    sel.appendChild(opt);
  });
}

// ── Build GeoJSON layer ───────────────────────────────────────────────────────

function _renderPropPickerLayer() {
  if (_propPicker.parcelLayer) {
    _propPicker.map.removeLayer(_propPicker.parcelLayer);
    _propPicker.parcelLayer = null;
  }

  // Board-aware styling (merged from old KML picker)
  const boardNames = new Set(
    (state.researchSession?.subjects || []).map(s => s.name.toLowerCase())
  );

  _propPicker.parcelLayer = L.geoJSON(_propPicker.geojsonData, {
    renderer: _propPicker.renderer,
    style: feature => _pickerParcelStyle(feature, boardNames),
    pointToLayer: (feature, latlng) => {
      const fill = _pickerParcelFill(feature, boardNames);
      return L.circleMarker(latlng, {
        radius: 6,
        fillColor: fill,
        color: fill,
        weight: 1.5,
        fillOpacity: 0.75,
      });
    },
    onEachFeature: (feature, layer) => {
      layer.on({
        click: e => { L.DomEvent.stopPropagation(e); _onPropParcelClick(feature, layer); },
        // No mouseover/mouseout styling — KML polygons are invisible click targets.
        // The ArcGIS tile layer provides the authoritative red boundary lines.
      });
      const p = feature.properties;
      const ttLines = [`<b>${p.owner || '(no name)'}</b>`];
      if (p.upc) ttLines.push(`<span style="font-size:10px;opacity:.7">UPC: ${p.upc}</span>`);
      if (p.book || p.page) ttLines.push(`<span style="font-size:10px;opacity:.65">Bk ${p.book || ''}/${p.page || ''}</span>`);
      if (p.cab_refs_str) ttLines.push(`<span style="font-size:10px;opacity:.65">Cab: ${p.cab_refs_str}</span>`);
      layer.bindTooltip(ttLines.join('<br>'), { sticky: true, className: 'kml-tooltip', opacity: 0.97 });
    },
  }).addTo(_propPicker.map);

  try {
    const bounds = _propPicker.parcelLayer.getBounds();
    if (bounds.isValid()) _propPicker.map.fitBounds(bounds, { padding: [20, 20] });
  } catch (_) { }

  setTimeout(() => _propPicker.map && _propPicker.map.invalidateSize(), 200);
}

// ── Styling helpers (board-aware coloring) ──────────────────────────────────
function _pickerParcelFill(feature, boardNames) {
  const p = feature.properties;
  if (p.highlight) return '#e3c55a';                                      // client / highlighted → gold
  if (boardNames.has((p.owner || '').toLowerCase())) return '#b080e0';   // on research board → purple
  return 'transparent';                                                    // regular parcel → invisible
}

function _pickerParcelStyle(feature, boardNames) {
  const fill = _pickerParcelFill(feature, boardNames);
  const highlight = feature.properties.highlight;
  const onBoard = boardNames.has((feature.properties.owner || '').toLowerCase());
  // KML polygons are invisible click targets — ArcGIS layer renders real boundaries.
  // stroke:false eliminates gray spider lines from the canvas renderer.
  return {
    fillColor: fill,
    fillOpacity: highlight ? 0.20 : onBoard ? 0.15 : 0,
    stroke: false,
    color: 'transparent',
    weight: 0,
  };
}

// ── ArcGIS-based highlight: fetch precise geometry and draw on highlightPane ──

/**
 * Fetch the actual parcel polygon from ArcGIS by UPC and draw a
 * highlight polygon that perfectly matches the authoritative boundary lines.
 * @param {string} upc - The UPC to query
 * @param {string} color - Fill/stroke color (e.g. '#56d3a0')
 * @param {number} fillOpacity - Fill opacity (0–1)
 */
async function _fetchArcgisHighlight(upc, color, fillOpacity) {
  if (!upc || !_propPicker.map) return;
  try {
    const params = new URLSearchParams({
      where: `UPC='${upc}'`,
      outFields: 'UPC',
      returnGeometry: 'true',
      outSR: '4326',
      f: 'json',
    });
    const url = `https://gis.ose.nm.gov/server_s/rest/services/Parcels/County_Parcels_2025/MapServer/29/query?${params}`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(10000) });
    const data = await resp.json();

    if (!data.features || !data.features.length) return;
    const rings = data.features[0].geometry?.rings;
    if (!rings || !rings.length) return;

    // Convert ArcGIS rings [lng,lat] to Leaflet [lat,lng]
    const latlngs = rings.map(ring =>
      ring.map(pt => [pt[1], pt[0]])
    );

    // Remove any existing highlight
    if (_propPicker._arcgisHighlight) {
      _propPicker.map.removeLayer(_propPicker._arcgisHighlight);
    }

    // Draw the precise polygon on the highlight pane
    _propPicker._arcgisHighlight = L.polygon(latlngs, {
      pane: 'highlightPane',
      fillColor: color,
      fillOpacity: fillOpacity,
      color: color,
      weight: 3,
      opacity: 0.9,
      interactive: false,
    }).addTo(_propPicker.map);

  } catch (e) {
    console.warn('[arcgis-highlight] Failed to fetch geometry for UPC', upc, e.message);
  }
}

// ── Parcel click handler ──────────────────────────────────────────────────────

function _onPropParcelClick(feature, layer) {
  // Deselect previous KML highlight
  if (_propPicker.selectedLayer && _propPicker.selectedLayer !== layer) {
    const boardNames = new Set(
      (state.researchSession?.subjects || []).map(s => s.name.toLowerCase())
    );
    _propPicker.selectedLayer.setStyle && _propPicker.selectedLayer.setStyle(
      _pickerParcelStyle(
        { properties: _propPicker.selectedLayer.feature?.properties || {} },
        boardNames
      )
    );
  }

  // Remove previous ArcGIS highlight polygon
  if (_propPicker._arcgisHighlight) {
    _propPicker.map.removeLayer(_propPicker._arcgisHighlight);
    _propPicker._arcgisHighlight = null;
  }

  // Light KML highlight (just a subtle fill — the ArcGIS highlight will provide the precise outline)
  layer.setStyle && layer.setStyle({
    fillColor: '#56d3a0', fillOpacity: 0.25, color: 'rgba(86,211,160,0.3)', weight: 0.5,
  });

  _propPicker.selectedLayer = layer;
  _propPicker.selectedProps = feature.properties;

  // Fetch precise ArcGIS geometry for the selected parcel and draw accurate highlight
  const upc = feature.properties.upc;
  if (upc) {
    _fetchArcgisHighlight(upc, '#56d3a0', 0.30);
  }

  const p = feature.properties;
  document.getElementById('propPickerOwner').textContent = p.owner || '(No Name)';

  let details = '';
  if (p.upc) details += `<b>UPC:</b> ${escHtml(p.upc)}<br>`;
  if (p.book || p.page) details += `<b>Book/Page:</b> ${escHtml(p.book)}/${escHtml(p.page)}<br>`;
  if (p.cab_refs_str) details += `<b>Cabinet:</b> ${escHtml(p.cab_refs_str)}<br>`;
  if (p.plat) details += `<b>Plat:</b> ${escHtml((p.plat || '').substring(0, 60))}${(p.plat || '').length > 60 ? '…' : ''}<br>`;
  // Address placeholder
  details += `<div id="propPickerAddress" style="margin-top:4px"><span style="color:var(--text3);font-size:11px">📍 Looking up address…</span></div>`;
  // PLSS section placeholder (populated async by BLM spatial query)
  details += `<div id="propPickerPLSS" style="margin-top:4px"><span style="color:var(--text3);font-size:11px">📐 Looking up PLSS section…</span></div>`;
  // Document cross-reference placeholders (populated async)
  details += `<div id="propPickerDocXref" style="margin-top:8px;padding-top:6px;border-top:1px solid rgba(100,110,120,.15)"><span style="color:var(--text3);font-size:10px">🔍 Checking deeds & plats…</span></div>`;
  if (!details) details = '<span style="color:var(--text3);font-style:italic">No extended data.</span>';

  document.getElementById('propPickerDetails').innerHTML = details;
  document.getElementById('btnConfirmProperty').disabled = false;

  // Enable adjoiner button
  const adjBtn = document.getElementById('btnPickerAddAdjoiner');
  if (adjBtn) adjBtn.disabled = false;

  // Also highlight this item in the side list (if visible from search)
  document.querySelectorAll('.prop-picker-result-item').forEach(el => {
    el.classList.toggle('selected', el.dataset.upc === p.upc);
  });

  // ── Async address lookup ───────────────────────────────────────────────
  const centroid = p._centroid || (feature.geometry && feature.geometry.type === 'Point'
    ? feature.geometry.coordinates : null);
  lookupPropertyAddress({ upc: p.upc || '', lat: centroid ? centroid[1] : 0, lon: centroid ? centroid[0] : 0 }).then(res => {
    const el = document.getElementById('propPickerAddress');
    if (!el) return;
    if (res && res.success) {
      const srcIcon = res.source === 'arcgis' ? '🏛️' : '📍';
      let html = `<span style="font-size:11px;color:#4ecdc4">${srcIcon} ${escHtml(res.short_address)}</span>`;

      // Enriched ArcGIS data chips
      const chips = [];
      if (res.land_area) chips.push(`<span class="kml-chip chip-upc">📐 ${res.land_area} ac</span>`);
      if (res.subdivision) chips.push(`<span class="kml-chip chip-cab">${escHtml(res.subdivision)}</span>`);
      if (res.trs) chips.push(`<span class="kml-chip chip-upc">🧭 ${escHtml(res.trs)}</span>`);
      if (res.zoning) chips.push(`<span class="kml-chip chip-book">${escHtml(res.zoning)}</span>`);
      if (res.land_use) chips.push(`<span class="kml-chip chip-book">${escHtml(res.land_use)}</span>`);
      if (res.structure_count > 0) chips.push(`<span class="kml-chip chip-upc">🏠 ${res.structure_count} struct</span>`);
      if (res.owner_official && res.owner_official !== (p.owner || '')) {
        chips.push(`<span class="kml-chip chip-cab" title="Official owner from assessor">👤 ${escHtml(res.owner_official)}</span>`);
      }
      if (chips.length) {
        html += `<div class="kml-meta-row" style="margin-top:6px">${chips.join('')}</div>`;
      }

      if (res.legal_description) {
        html += `<br><span style="font-size:10px;color:var(--text3)" title="${escHtml(res.legal_description)}">📋 ${escHtml(res.legal_description.substring(0, 80))}${res.legal_description.length > 80 ? '…' : ''}</span>`;
      }
      if (res.mail_address) {
        html += `<br><span style="font-size:10px;color:var(--text3)">✉️ ${escHtml(res.mail_address.substring(0, 60))}</span>`;
      }
      el.innerHTML = html;
    } else {
      el.innerHTML = `<span style="font-size:10px;color:var(--text3);opacity:.5">📍 Address unavailable</span>`;
    }
  });

  // ── Async PLSS section lookup (BLM CadNSDI spatial query) ──────────────
  _queryPlssSection(centroid).then(plssRes => {
    const plssEl = document.getElementById('propPickerPLSS');
    if (!plssEl) return;
    if (plssRes) {
      const gloUrl = buildGloUrl(plssRes);
      plssEl.innerHTML = `<span style="font-size:11px;color:#e3c55a;font-family:'JetBrains Mono',monospace;font-weight:600">📐 ${escHtml(plssRes.trs)}</span>` +
        (gloUrl ? `<br><a href="${gloUrl}" target="_blank" rel="noopener" style="font-size:10px;color:#d4a44a;text-decoration:none;display:inline-flex;align-items:center;gap:3px;margin-top:2px" title="View original GLO survey plat on BLM records">📜 GLO Survey Plat →</a>` : '');
    } else {
      plssEl.innerHTML = `<span style="font-size:10px;color:var(--text3);opacity:.5">📐 PLSS section unavailable</span>`;
    }
  });

  // ── Async document cross-reference (deeds & plats for this owner) ──────
  _pickerDocXref(p.owner || '', p.upc || '', p.book || '', p.page || '', p.cab_refs_str || '');
}

/**
 * Cross-reference deeds & plats for a selected parcel owner in the map picker.
 * Checks: (1) local cabinet plat index, (2) existing session subjects.
 * Shows results in #propPickerDocXref. 
 */
async function _pickerDocXref(ownerName, upc, book, page, cabRefs) {
  const el = document.getElementById('propPickerDocXref');
  if (!el || !ownerName) return;

  let html = '';
  const badges = [];

  // ── 1. Check if owner is already on the research board ──
  const rs = state.researchSession;
  let boardSubject = null;
  if (rs) {
    boardSubject = rs.subjects.find(
      s => s.name.toLowerCase() === ownerName.toLowerCase()
    );
  }

  if (boardSubject) {
    const typeLabel = boardSubject.type === 'client' ? '★ Client' : 'Adjoiner';
    badges.push(`<span class="pcp-doc-badge pcp-saved" style="font-size:10px">📋 On Board (${typeLabel})</span>`);
    if (boardSubject.deed_saved)
      badges.push(`<span class="pcp-doc-badge pcp-saved" style="font-size:10px">📄 Deed ✓</span>`);
    else
      badges.push(`<span class="pcp-doc-badge pcp-missing" style="font-size:10px">📄 Deed needed</span>`);
    if (boardSubject.plat_saved)
      badges.push(`<span class="pcp-doc-badge pcp-saved" style="font-size:10px">📐 Plat ✓</span>`);
    else
      badges.push(`<span class="pcp-doc-badge pcp-missing" style="font-size:10px">📐 Plat needed</span>`);
  }

  // ── 2. Check local cabinet index for plat files ──
  try {
    const cabRes = await apiFetch('/find-plat-local', 'POST', {
      client_name: ownerName,
      grantor: ownerName,
      grantee: ownerName,
      detail: {},
    });
    if (cabRes?.success && cabRes.results?.length > 0) {
      const count = cabRes.results.length;
      const cabLetters = [...new Set(
        cabRes.results.map(r => r.cabinet || r.file?.match(/Cabinet\s*([A-F])/i)?.[1] || '').filter(Boolean)
      )].join(', ');
      const label = cabLetters ? `Cabinet ${cabLetters}` : 'Local';
      badges.push(
        `<span class="pcp-doc-badge pcp-saved" style="font-size:10px;cursor:pointer" title="${cabRes.results.map(r => r.file || r.name).join('\\n')}" onclick="event.stopPropagation()">` +
        `🗄️ ${count} plat${count > 1 ? 's' : ''} (${label})</span>`
      );
    } else {
      badges.push(`<span class="pcp-doc-badge pcp-missing" style="font-size:10px">🗄️ No cabinet plats</span>`);
    }
  } catch (_) {
    badges.push(`<span class="pcp-doc-badge pcp-missing" style="font-size:10px">🗄️ Cabinet check failed</span>`);
  }

  // ── 3. Check KML parcel records for recorded plat info ──
  if (book && page) {
    badges.push(`<span class="pcp-doc-badge pcp-saved" style="font-size:10px">📚 Recorded Bk ${escHtml(book)}/${escHtml(page)}</span>`);
  }

  // ── 4. Check if there are cabinet references from KML data ──
  if (cabRefs) {
    const refs = cabRefs.split(',').map(s => s.trim()).filter(Boolean);
    if (refs.length) {
      badges.push(`<span class="pcp-doc-badge pcp-saved" style="font-size:10px" ` +
        `title="KML-parsed cabinet references: ${escHtml(cabRefs)}">📁 ${refs.length} cab ref${refs.length > 1 ? 's' : ''}</span>`);
    }
  }

  // ── Render ──
  if (badges.length) {
    html = `<div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--text3);margin-bottom:4px">📑 Document Cross-Ref</div>` +
      `<div style="display:flex;flex-wrap:wrap;gap:4px">${badges.join('')}</div>`;
  } else {
    html = `<span style="font-size:10px;color:var(--text3);opacity:.5">📑 No documents found</span>`;
  }

  el.innerHTML = html;
}

// ── Owner name search ─────────────────────────────────────────────────────────

function onPropPickerSearch(query) {
  clearTimeout(_propPicker.searchTimer);
  _propPicker.searchTimer = setTimeout(() => _doPropPickerSearch(query.trim()), 300);
}

async function _doPropPickerSearch(q) {
  const countEl = document.getElementById('propPickerSearchCount');
  const listEl = document.getElementById('propPickerList');

  if (!q || q.length < 2) {
    // Reset map styles
    _propPicker.parcelLayer && _propPicker.parcelLayer.eachLayer(layer => {
      if (layer.setStyle) layer.setStyle({ fillColor: '#1a7fd4', fillOpacity: 0.12, color: '#2196f3', weight: 1.5 });
    });
    countEl.textContent = _propPicker.geojsonData ? _propPicker.geojsonData.features.length.toLocaleString() + ' parcels' : '—';
    listEl.innerHTML = '<div style="padding:12px 14px;font-size:11px;color:var(--text3);font-style:italic">Search or click a parcel on the map.</div>';
    return;
  }

  countEl.textContent = 'Searching…';
  listEl.innerHTML = '<div style="padding:16px;font-size:11px;color:var(--text3)">Searching…</div>';

  try {
    const res = await apiFetch('/parcel-search', 'POST', { query: q, operator: 'contains', limit: 40 });

    if (!res.success) {
      countEl.textContent = 'Error';
      listEl.innerHTML = `<div style="padding:12px;color:#ff7b72;font-size:12px">${escHtml(res.error || 'Search failed')}</div>`;
      return;
    }

    countEl.textContent = res.count + ' match' + (res.count !== 1 ? 'es' : '');

    // Highlight matching parcels on map
    const matchUpcs = new Set(res.results.map(r => r.upc).filter(Boolean));
    _propPicker.parcelLayer && _propPicker.parcelLayer.eachLayer(layer => {
      if (!layer.setStyle) return;
      const f = layer.feature;
      const upc = f && f.properties && f.properties.upc;
      if (matchUpcs.has(upc)) {
        layer.setStyle({ fillColor: '#56d3a0', fillOpacity: 0.45, color: '#56d3a0', weight: 2.0 });
      } else {
        layer.setStyle({ fillColor: '#aac4e0', fillOpacity: 0.04, color: '#aac4e0', weight: 0.4 });
      }
    });

    // Pan to first match centroid
    if (res.results.length && res.results[0].centroid) {
      const [lng, lat] = res.results[0].centroid;
      _propPicker.map.setView([lat, lng], 15, { animate: true });
    }

    // Populate side list
    if (!res.results.length) {
      listEl.innerHTML = `<div style="padding:16px;font-size:12px;color:var(--text3)">No parcels found for "<strong>${escHtml(q)}</strong>".<br><span style="font-size:10px;opacity:.6">Check that the KML index is built.</span></div>`;
    } else {
      const boardNames = new Set(
        (state.researchSession?.subjects || []).map(s => s.name.toLowerCase())
      );
      listEl.innerHTML = res.results.map((p, pi) => {
        const onBoard = boardNames.has((p.owner || '').toLowerCase());
        const boardSubj = onBoard ? state.researchSession.subjects.find(
          s => s.name.toLowerCase() === (p.owner || '').toLowerCase()
        ) : null;
        const isClient = boardSubj?.type === 'client';
        let statusBadge = '';
        if (isClient) {
          statusBadge = '<span style="font-size:8px;font-weight:700;padding:1px 5px;border-radius:3px;background:rgba(227,197,90,.15);color:#e3c55a;margin-left:6px">★ CLIENT</span>';
        } else if (onBoard) {
          statusBadge = '<span style="font-size:8px;font-weight:700;padding:1px 5px;border-radius:3px;background:rgba(176,128,224,.12);color:#b080e0;margin-left:6px">ON BOARD</span>';
        }
        let docBadges = '';
        if (boardSubj) {
          docBadges += boardSubj.deed_saved
            ? '<span style="font-size:8px;color:#56d3a0;margin-left:4px">📄✓</span>'
            : '<span style="font-size:8px;color:var(--text3);margin-left:4px">📄✗</span>';
          docBadges += boardSubj.plat_saved
            ? '<span style="font-size:8px;color:#56d3a0;margin-left:2px">📐✓</span>'
            : '<span style="font-size:8px;color:var(--text3);margin-left:2px">📐✗</span>';
        }
        return `
        <div class="prop-picker-result-item" data-upc="${escHtml(p.upc || '')}" data-idx="${pi}" onclick="selectPropPickerResult(${pi})" style="${onBoard ? 'border-left:2px solid ' + (isClient ? '#e3c55a' : '#b080e0') : ''}">
          <div class="prop-picker-result-name">${escHtml(p.owner)}${statusBadge}${docBadges}</div>
          <div class="prop-picker-result-meta">${p.upc ? 'UPC: ' + escHtml(p.upc) : ''}${p.book ? ' · Bk ' + escHtml(p.book) : ''}${p.page ? '/' + escHtml(p.page) : ''}</div>
        </div>`;
      }).join('');

      // Store search results for click handler
      _propPicker._searchResults = res.results;
    }
  } catch (e) {
    countEl.textContent = 'Error';
    listEl.innerHTML = `<div style="padding:12px;color:#ff7b72;font-size:12px">Search error: ${escHtml(e.message)}</div>`;
  }
}

function selectPropPickerResult(idx) {
  const p = _propPicker._searchResults && _propPicker._searchResults[idx];
  if (!p) return;

  // Select corresponding parcel on map
  let found = false;
  _propPicker.parcelLayer && _propPicker.parcelLayer.eachLayer(layer => {
    if (found) return;
    const f = layer.feature;
    if (f && f.properties && f.properties.upc === p.upc) {
      _onPropParcelClick(f, layer);
      found = true;
      // Zoom to parcel
      try {
        const b = layer.getBounds ? layer.getBounds() : null;
        if (b && b.isValid()) _propPicker.map.fitBounds(b, { padding: [60, 60], maxZoom: 18 });
        else if (p.centroid) _propPicker.map.setView([p.centroid[1], p.centroid[0]], 17);
      } catch (_) { }
    }
  });

  // If parcel not on map but still want to select it
  if (!found && p.upc) {
    _propPicker.selectedProps = { owner: p.owner, upc: p.upc, book: p.book, page: p.page, plat: p.plat, cab_refs_str: (p.cab_refs || []).join(', ') };
    document.getElementById('propPickerOwner').textContent = p.owner || '—';
    document.getElementById('propPickerDetails').innerHTML = `<b>UPC:</b> ${escHtml(p.upc || '')}`;
    document.getElementById('btnConfirmProperty').disabled = false;
    if (p.centroid) _propPicker.map.setView([p.centroid[1], p.centroid[0]], 16);
  }

  // Update list highlighting
  document.querySelectorAll('.prop-picker-result-item').forEach((el, i) => {
    el.classList.toggle('selected', i === idx);
  });
}

// ── Reset map view ────────────────────────────────────────────────────────────

function propPickerResetView() {
  if (!_propPicker.parcelLayer || !_propPicker.map) return;
  document.getElementById('propPickerSearch').value = '';
  // Restore all polygon styles to default before calling search reset
  const boardNames = new Set(
    (state.researchSession?.subjects || []).map(s => s.name.toLowerCase())
  );
  _propPicker.parcelLayer.eachLayer(layer => {
    if (layer !== _propPicker.selectedLayer && layer.setStyle) {
      const f = layer.feature;
      if (f) layer.setStyle(_pickerParcelStyle(f, boardNames));
    }
  });
  onPropPickerSearch('');
  try {
    const bounds = _propPicker.parcelLayer.getBounds();
    if (bounds.isValid()) _propPicker.map.fitBounds(bounds, { padding: [20, 20] });
  } catch (_) { }
}

// ── Confirm selection → fills Step 1 form ────────────────────────────────────

function confirmPropertySelection() {
  const p = _propPicker.selectedProps;
  if (!p || !p.owner) { showToast('No parcel selected', 'warn'); return; }

  // Convert "GARZA VERONICA" → "Garza, Veronica" style (Last, First) if possible
  const ownerRaw = p.owner.trim();
  let clientName = ownerRaw;
  if (!ownerRaw.includes(',') && ownerRaw.includes(' ')) {
    const parts = ownerRaw.split(/\s+/);
    if (parts.length >= 2) {
      clientName = parts[0] + ', ' + parts.slice(1).join(' ');
    }
  }
  // Title-case
  clientName = clientName.split(/\b/).map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join('');

  // Store confirmed parcel and update form
  _propPicker.confirmedParcel = p;
  document.getElementById('setupClient').value = clientName;

  // Show the selected parcel card
  document.getElementById('selectedParcelName').textContent = clientName;
  const meta = [
    p.upc ? 'UPC: ' + p.upc : '',
    (p.book && p.page) ? 'Bk ' + p.book + '/' + p.page : '',
  ].filter(Boolean).join('  ·  ');
  document.getElementById('selectedParcelMeta').textContent = meta;
  document.getElementById('selectedParcelCard').classList.remove('hidden');

  // Add UPC to highlight set so it renders gold
  if (p.upc && !_propPicker.highlightUpcs.includes(p.upc)) {
    _propPicker.highlightUpcs.push(p.upc);
  }
  if (p) p.highlight = true;

  // Colour the selected polygon gold (client) — subtle KML fill + precise ArcGIS outline
  _propPicker.selectedLayer && _propPicker.selectedLayer.setStyle && _propPicker.selectedLayer.setStyle({
    fillColor: '#e3c55a', fillOpacity: 0.15, color: 'rgba(240,192,64,0.2)', weight: 0.5,
  });
  // Draw precise ArcGIS gold highlight
  if (p.upc) _fetchArcgisHighlight(p.upc, '#f0c040', 0.35);

  // Disable the "Select as Client" button (already selected)
  document.getElementById('btnConfirmProperty').disabled = true;
  document.getElementById('btnConfirmProperty').innerHTML = '✓ Client Selected';

  // Update action hint — now they can pick adjoiners
  const hintEl = document.getElementById('pickerActionHint');
  if (hintEl) hintEl.textContent = 'Now click neighboring parcels to add as adjoiners.';

  // Stay open so user can keep picking adjoiners
  showToast(`✓ Property selected: ${clientName} — now pick adjoiners!`, 'success');
}

// ── Add selected parcel as adjoiner (unified picker) ─────────────────────────

async function pickerAddSelectedAsAdjoiner() {
  if (!_propPicker.selectedProps) return;
  const name = (_propPicker.selectedProps.owner || '').trim();
  if (!name) { showToast('No owner name for this parcel', 'warn'); return; }

  // Always add to the visual queue
  const parcelData = { name, upc: _propPicker.selectedProps.upc || '', plat: _propPicker.selectedProps.plat || '' };
  if (!_propPicker.mapAddedNames.some(e => (typeof e === 'object' ? e.name : e) === name)) {
    _propPicker.mapAddedNames.push(parcelData);
    _updatePickerAddedList();
  }

  // If no session yet, queue for deferred addition when session starts
  if (!state.researchSession) {
    showToast(`${name} queued — will be added when you start the session`, 'info');
    // Style the parcel as "on board" (purple) even though session doesn't exist yet
    _propPicker.selectedLayer && _propPicker.selectedLayer.setStyle &&
      _propPicker.selectedLayer.setStyle({
        fillColor: '#b080e0', fillOpacity: 0.15, color: 'rgba(176,128,224,0.2)', weight: 0.5,
      });
    // Draw precise ArcGIS purple highlight
    if (_propPicker.selectedProps.upc) _fetchArcgisHighlight(_propPicker.selectedProps.upc, '#b080e0', 0.25);
    return;
  }

  // Session exists — persist immediately
  const ok = await addFoundAdjoiner(name);

  if (ok) {
    _propPicker.selectedLayer && _propPicker.selectedLayer.setStyle &&
      _propPicker.selectedLayer.setStyle({
        fillColor: '#b080e0', fillOpacity: 0.15, color: 'rgba(176,128,224,0.2)', weight: 0.5,
      });
    // Draw precise ArcGIS purple highlight
    if (_propPicker.selectedProps.upc) _fetchArcgisHighlight(_propPicker.selectedProps.upc, '#b080e0', 0.25);
  }
}

// ── "Adjoiners Added" sidebar list ──────────────────────────────────────────
function _updatePickerAddedList() {
  const el = document.getElementById('pickerAddedList');
  if (!el) return;
  if (!_propPicker.mapAddedNames.length) {
    el.innerHTML = '<div style="font-size:11px;color:var(--text3);font-style:italic">None yet</div>';
    return;
  }
  el.innerHTML = _propPicker.mapAddedNames.map(entry => {
    const n = typeof entry === 'object' ? entry.name : entry;
    return `<div style="font-size:11px;padding:4px 8px;background:rgba(176,128,224,.12);border:1px solid rgba(176,128,224,.25);border-radius:6px;color:#b080e0">${escHtml(n)}</div>`;
  }).join('');
}

// ─────────────────────────────────────────────────────────────────────────────
// GLO RECORDS URL BUILDER (BLM General Land Office original survey plats)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Build a URL to the BLM GLO Records site for a given TRS reference.
 * Works with either a parsed TRS object ({township,range,section,trs})
 * or a raw TRS string like "T25N R13E S12".
 * @param {Object|string} trsInput - TRS data
 * @returns {string} URL to GLO Records, or '' if invalid
 */
function buildGloUrl(trsInput) {
  let twpNum = '', twpDir = '', rngNum = '', rngDir = '', sec = '';

  if (typeof trsInput === 'string') {
    // Parse from string like "T25N R13E S12" or "T25N R13E"
    const m = trsInput.match(/T\.?\s*(\d+)\s*([NS])\b.*?R\.?\s*(\d+)\s*([EW])\b(?:.*?S(?:ec(?:tion)?)?\s*(\d+))?/i);
    if (!m) return '';
    twpNum = m[1]; twpDir = m[2].toUpperCase();
    rngNum = m[3]; rngDir = m[4].toUpperCase();
    sec = m[5] || '';
  } else if (trsInput && typeof trsInput === 'object') {
    // Parse from object {township: "T25N", range: "R13E", section: "12"}
    const twpMatch = (trsInput.township || '').match(/(\d+)\s*([NS])/i);
    const rngMatch = (trsInput.range || '').match(/(\d+)\s*([EW])/i);
    if (!twpMatch || !rngMatch) return '';
    twpNum = twpMatch[1]; twpDir = twpMatch[2].toUpperCase();
    rngNum = rngMatch[1]; rngDir = rngMatch[2].toUpperCase();
    sec = trsInput.section || '';
  } else {
    return '';
  }

  // Build GLO survey plat search URL
  // This searches for original survey plats covering the specified township
  const params = new URLSearchParams({
    searchCriteria: `type=survey|st=NM|twp_nr=${twpNum}|twp_dir=${twpDir}|rng_nr=${rngNum}|rng_dir=${rngDir}` + (sec ? `|sec=${sec}` : ''),
  });
  return `https://glorecords.blm.gov/results/default.aspx?${params.toString()}`;
}

/**
 * Build a GLO patent search URL for a TRS reference (finds land patents/deeds).
 */
function buildGloPatentUrl(trsInput) {
  let twpNum = '', twpDir = '', rngNum = '', rngDir = '', sec = '';

  if (typeof trsInput === 'string') {
    const m = trsInput.match(/T\.?\s*(\d+)\s*([NS])\b.*?R\.?\s*(\d+)\s*([EW])\b(?:.*?S(?:ec(?:tion)?)?\s*(\d+))?/i);
    if (!m) return '';
    twpNum = m[1]; twpDir = m[2].toUpperCase();
    rngNum = m[3]; rngDir = m[4].toUpperCase();
    sec = m[5] || '';
  } else if (trsInput && typeof trsInput === 'object') {
    const twpMatch = (trsInput.township || '').match(/(\d+)\s*([NS])/i);
    const rngMatch = (trsInput.range || '').match(/(\d+)\s*([EW])/i);
    if (!twpMatch || !rngMatch) return '';
    twpNum = twpMatch[1]; twpDir = twpMatch[2].toUpperCase();
    rngNum = rngMatch[1]; rngDir = rngMatch[2].toUpperCase();
    sec = trsInput.section || '';
  } else {
    return '';
  }

  const params = new URLSearchParams({
    searchCriteria: `type=patent|st=NM|twp_nr=${twpNum}|twp_dir=${twpDir}|rng_nr=${rngNum}|rng_dir=${rngDir}` + (sec ? `|sec=${sec}` : ''),
  });
  return `https://glorecords.blm.gov/results/default.aspx?${params.toString()}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// BLM PLSS SPATIAL QUERY (identify section from coordinates)
// ─────────────────────────────────────────────────────────────────────────────

const _plssCache = {};  // "lat,lon" -> { trs, township, range, section, ... }

/**
 * Query the BLM CadNSDI MapServer to identify which PLSS section
 * a given coordinate falls in.
 * @param {Array} centroid - [lon, lat] or null
 * @returns {Promise<Object|null>} - { trs, township, range, section, plssId } or null
 */
async function _queryPlssSection(centroid) {
  if (!centroid || !centroid[0] || !centroid[1]) return null;

  const lon = centroid[0];
  const lat = centroid[1];
  const cacheKey = `${lat.toFixed(5)},${lon.toFixed(5)}`;
  if (_plssCache[cacheKey]) return _plssCache[cacheKey];

  try {
    const params = new URLSearchParams({
      geometry: `${lon},${lat}`,
      geometryType: 'esriGeometryPoint',
      spatialRel: 'esriSpatialRelIntersects',
      outFields: 'FRSTDIVNO,TWNSHPNO,TWNSHPDIR,RANGENO,RANGEDIR,PLSSID,FRSTDIVID,FRSTDIVTXT',
      returnGeometry: 'false',
      f: 'json',
      inSR: '4326',
    });

    const url = `https://gis.blm.gov/arcgis/rest/services/Cadastral/BLM_Natl_PLSS_CadNSDI/MapServer/2/query?${params.toString()}`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(8000) });
    const data = await resp.json();

    if (!data.features || !data.features.length) return null;

    const attrs = data.features[0].attributes;
    const twpNum = attrs.TWNSHPNO || '';
    const twpDir = attrs.TWNSHPDIR || '';
    const rngNum = attrs.RANGENO || '';
    const rngDir = attrs.RANGEDIR || '';
    const secNum = attrs.FRSTDIVNO || '';

    const result = {
      trs: `T${twpNum}${twpDir} R${rngNum}${rngDir}` + (secNum ? ` S${secNum}` : ''),
      township: `T${twpNum}${twpDir}`,
      range: `R${rngNum}${rngDir}`,
      section: String(secNum),
      plssId: attrs.PLSSID || '',
      frstDivId: attrs.FRSTDIVID || '',
    };

    _plssCache[cacheKey] = result;
    return result;
  } catch (e) {
    console.warn('[PLSS] Section query failed:', e.message);
    return null;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// PROPERTY ADDRESS LOOKUP  (shared by all map pickers)
// ─────────────────────────────────────────────────────────────────────────────

const _addressCache = {};  // { upc|"ll:lat,lon" : { short_address, source, ... } }

/**
 * Look up property address for a parcel.
 * Uses ArcGIS (by UPC) first, then Nominatim (by centroid) as fallback.
 * Caches results so repeated lookups are instant.
 * @param {Object} opts  - { upc, lat, lon }
 * @returns {Promise<Object>}  - { success, short_address, source, ... }
 */
async function lookupPropertyAddress(opts) {
  const upc = (opts.upc || '').trim();
  const lat = opts.lat || 0;
  const lon = opts.lon || 0;

  // Check cache
  const cacheKey = upc ? `upc:${upc}` : `ll:${lat.toFixed(5)},${lon.toFixed(5)}`;
  if (_addressCache[cacheKey]) return _addressCache[cacheKey];

  try {
    const res = await apiFetch('/property-address', 'POST', { upc, lat, lon });
    if (res && res.success) {
      _addressCache[cacheKey] = res;
    }
    return res || { success: false, short_address: '' };
  } catch (e) {
    return { success: false, short_address: '', error: e.message };
  }
}

/**
 * Look up address for a KML parcel card and update the chip in-place.
 */
async function _lookupKmlCardAddress(pi) {
  const kmlHits = state._kmlHits;
  if (!kmlHits || !kmlHits[pi]) return;
  const p = kmlHits[pi];
  const el = document.getElementById('kml-addr-' + pi);
  if (!el) return;

  el.textContent = '📍 Loading…';
  el.style.opacity = '0.6';

  const centroid = p.centroid || [];
  const res = await lookupPropertyAddress({
    upc: p.upc || '',
    lat: centroid[1] || 0,
    lon: centroid[0] || 0,
  });

  if (res && res.success) {
    const srcIcon = res.source === 'arcgis' ? '🏛️' : '📍';
    el.innerHTML = `${srcIcon} ${escHtml(res.short_address)}`;
    el.title = res.legal_description
      ? `${res.short_address}\n${res.legal_description}`
      : res.short_address;
    el.style.opacity = '1';
    el.style.cursor = 'default';
    el.onclick = null;
  } else {
    el.textContent = '📍 No address';
    el.style.opacity = '0.4';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// OLD KML MAP PICKER — REMOVED (merged into unified _propPicker above)
// Backward-compat aliases for any leftover references:
// ─────────────────────────────────────────────────────────────────────────────
const _kmlMap = _propPicker;  // alias so any stray _kmlMap.xxx access works
function showKmlMapPicker() { showPropertyPicker(); }
function closeKmlMapPicker() { closePropPicker(); }
function kmlAddSelectedAsAdjoiner() { pickerAddSelectedAsAdjoiner(); }
function kmlMarkSelectedAsClient() { confirmPropertySelection(); }
function kmlMapOwnerSearch(q) { onPropPickerSearch(q); }
function kmlMapResetView() { propPickerResetView(); }
function onKmlMapSourceChange(val) { onPropPickerSourceChange(val); }
function _updateKmlAddedList() { _updatePickerAddedList(); }
function _kmlParcelFill(f, b) { return _pickerParcelFill(f, b); }
function _kmlParcelStyle(f, b) { return _pickerParcelStyle(f, b); }
function _initKmlLeafletMap() { _initPropPickerMap(); }
function _loadKmlMapData() { return _loadPropPickerMapData(); }
function _renderKmlParcelLayer() { _renderPropPickerLayer(); }
function _onKmlParcelClick(f, l) { _onPropParcelClick(f, l); }
function _doKmlOwnerSearch(q) { _doPropPickerSearch(q); }
function _addClientPulseRings() { /* pulse rings removed */ }

/** Build KML index on demand (triggered from "No parcels" message) */
async function showKmlIndexModal() {
  showToast('🗺️ Building parcel index from KML/KMZ files…', 'info');
  try {
    const res = await apiFetch('/xml/build-index', 'POST');
    if (res.success) {
      showToast(`✓ Index built: ${res.total || 0} parcels in ${res.elapsed_sec || '?'}s`, 'success');
      // Reload map data
      await _loadPropPickerMapData();
    } else {
      showToast('Index build failed: ' + (res.error || 'Unknown error'), 'error');
    }
  } catch (e) {
    showToast('Index build error: ' + e.message, 'error');
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// PLAT PREVIEW PANEL (Feature #1)
// ─────────────────────────────────────────────────────────────────────────────

/** Show plat preview panel with a rendered image from the backend */
function showPlatPreview(filePath, filename, saveAction) {
  const panel = document.getElementById('platPreviewPanel');
  const body = document.getElementById('platPreviewBody');
  const title = document.getElementById('platPreviewTitle');
  const actions = document.getElementById('platPreviewActions');

  title.textContent = '📄 ' + (filename || 'Plat Preview');
  body.innerHTML = '<div class="loading-state"><div class="spinner"></div> Rendering PDF…</div>';

  // Build action buttons — download always works (even for LAN clients)
  let actionsHtml = '';
  if (saveAction) {
    actionsHtml += `<button class="btn btn-success flex-1" onclick="${saveAction}">⬇ Save to Project</button>`;
  }
  actionsHtml += `<button class="btn btn-primary flex-1" onclick="downloadLocalFileToBrowser('${escHtml(filePath).replace(/'/g, "\\'")}')">⬇ Download</button>`;
  actionsHtml += `<button class="btn btn-outline" onclick="closePlatPreview()">Close</button>`;
  actions.innerHTML = actionsHtml;

  // Open the panel
  panel.classList.add('open');

  // Load preview image from backend
  const url = `/api/preview-pdf?path=${encodeURIComponent(filePath)}`;
  const img = new Image();
  img.onload = () => {
    body.innerHTML = '';
    body.appendChild(img);
  };
  img.onerror = () => {
    body.innerHTML = `<div class="empty-state" style="padding:30px">
      <div class="empty-icon">⚠️</div>
      <p>Could not render PDF preview.</p>
      <div style="display:flex;gap:8px;margin-top:12px">
        <button class="btn btn-primary btn-sm" onclick="downloadLocalFileToBrowser('${escHtml(filePath).replace(/'/g, "\\'")}')">⬇ Download to View</button>
        <button class="btn btn-outline btn-sm" onclick="openFile('${escHtml(filePath).replace(/'/g, "\\'")}')">Open on Server</button>
      </div>
      <div style="font-size:10px;color:var(--text3);margin-top:8px">Download sends the file to your browser. “Open on Server” only works if you’re on the server machine.</div>
    </div>`;
  };
  img.src = url;
  img.alt = filename || 'Plat PDF preview';
  img.style.cssText = 'max-width:100%;cursor:zoom-in;';
  img.onclick = () => { window.open(url, '_blank'); };
}

/** Close the plat preview panel */
function closePlatPreview() {
  document.getElementById('platPreviewPanel').classList.remove('open');
}

// ─────────────────────────────────────────────────────────────────────────────
// FILE BADGES IN CONTEXT BAR (Feature #6)
// ─────────────────────────────────────────────────────────────────────────────

function updateFileBadges() {
  const container = document.getElementById('ctxFileBadges');
  if (!container || !state.researchSession) { if (container) container.innerHTML = ''; return; }

  const client = state.researchSession.subjects.find(s => s.type === 'client');
  if (!client) { container.innerHTML = ''; return; }

  let html = '';
  if (client.deed_saved && client.deed_path) {
    const fname = client.deed_path.split(/[/\\]/).pop();
    html += `<span class="file-badge file-badge-deed" onclick="downloadLocalFileToBrowser('${escHtml(client.deed_path).replace(/'/g, "\\'")}')" title="${escHtml(client.deed_path)}">✅ ${escHtml(fname)}</span>`;
  }
  if (client.plat_saved && client.plat_path) {
    const fname = client.plat_path.split(/[/\\]/).pop();
    html += `<span class="file-badge file-badge-plat" onclick="downloadLocalFileToBrowser('${escHtml(client.plat_path).replace(/'/g, "\\'")}')" title="${escHtml(client.plat_path)}">📄 ${escHtml(fname)}</span>`;
  }
  container.innerHTML = html;
}

// ─────────────────────────────────────────────────────────────────────────────
// CONFIDENCE SCORING (Feature #7)
// ─────────────────────────────────────────────────────────────────────────────

function _calcPlatConfidence(hit) {
  const strat = hit.strategy || '';
  if (strat === 'doc_number') return 97;
  if (strat === 'kml_plat_name') return 92;
  if (strat === 'kml_cab_ref') return 90;
  if (strat === 'deed_cab_ref') return 82;
  if (strat === 'client_name') return 65;
  if (strat === 'prior_owner') return 55;
  if (strat === 'name_match') return 35;
  if (strat === 'page_ref') return 25;
  return 20;
}

function _confidenceRingHtml(pct) {
  const r = 15, circ = 2 * Math.PI * r;
  const offset = circ * (1 - pct / 100);
  const cls = pct >= 80 ? 'confidence-high' : pct >= 50 ? 'confidence-med' : 'confidence-low';
  const strokeColor = pct >= 80 ? '#56d3a0' : pct >= 50 ? '#e3c55a' : '#6e7681';
  return `<div class="confidence-ring">
    <svg width="38" height="38" viewBox="0 0 38 38">
      <circle cx="19" cy="19" r="${r}" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="3"/>
      <circle cx="19" cy="19" r="${r}" fill="none" stroke="${strokeColor}" stroke-width="3"
        stroke-dasharray="${circ}" stroke-dashoffset="${offset}" stroke-linecap="round"
        style="transition: stroke-dashoffset 0.6s ease"/>
    </svg>
    <div class="confidence-ring-text ${cls}">${pct}</div>
  </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// SESSION SYNC GUARD (Feature #5)
// ─────────────────────────────────────────────────────────────────────────────

// Auto-persist session every 60 seconds to reduce drift
setInterval(() => {
  if (state.researchSession) {
    persistSession().catch(() => { });
  }
}, 60000);

// ─────────────────────────────────────────────────────────────────────────────
// DARK / LIGHT THEME TOGGLE (Feature #11)
// ─────────────────────────────────────────────────────────────────────────────

function toggleTheme() {
  const isDark = document.getElementById('optDarkTheme')?.checked ?? true;
  if (isDark) {
    document.documentElement.removeAttribute('data-theme');
  } else {
    document.documentElement.setAttribute('data-theme', 'light');
  }
  localStorage.setItem('dph_theme', isDark ? 'dark' : 'light');
}

// Restore theme on load
(function _restoreTheme() {
  const saved = localStorage.getItem('dph_theme');
  if (saved === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
    setTimeout(() => {
      const cb = document.getElementById('optDarkTheme');
      if (cb) cb.checked = false;
    }, 500);
  }
})();

// ─────────────────────────────────────────────────────────────────────────────
// CHAIN-OF-TITLE AUTO-FOLLOW (Feature #3)
// ─────────────────────────────────────────────────────────────────────────────

async function runChainOfTitle() {
  const d = state.selectedDetail;
  if (!d) { showToast('Select a deed first', 'warn'); return; }

  const grantor = d.Grantor || d.grantor || state.selectedDoc?.grantor || '';
  if (!grantor) { showToast('No grantor found in deed to trace', 'warn'); return; }

  // Create/show the chain timeline container below the deed detail
  let chainEl = document.getElementById('chainTimelineWrap');
  if (!chainEl) {
    chainEl = document.createElement('div');
    chainEl.id = 'chainTimelineWrap';
    chainEl.style.cssText = 'margin-top:12px;';
    document.getElementById('s2DetailContainer')?.appendChild(chainEl);
  }

  chainEl.innerHTML = `
    <div class="glass-card" style="padding:12px 16px">
      <div class="row-layout justify-between mb-2">
        <div style="font-size:13px;font-weight:700;color:var(--accent2)">🔗 Chain of Title</div>
        <button class="btn btn-outline btn-sm" onclick="document.getElementById('chainTimelineWrap').remove()">✕ Close</button>
      </div>
      <div class="loading-state" style="padding:20px"><div class="spinner"></div> Tracing ownership chain from ${escHtml(grantor.split(',')[0])}…</div>
    </div>`;

  try {
    const res = await apiFetch('/chain-search', 'POST', {
      start_grantor: grantor,
      max_hops: 10,
    });

    if (!res.success) throw new Error(res.error);

    const chain = res.chain || [];
    if (!chain.length) {
      chainEl.querySelector('.glass-card').innerHTML = `
        <div class="row-layout justify-between mb-2">
          <div style="font-size:13px;font-weight:700;color:var(--accent2)">🔗 Chain of Title</div>
          <button class="btn btn-outline btn-sm" onclick="document.getElementById('chainTimelineWrap').remove()">✕ Close</button>
        </div>
        <div class="empty-state" style="padding:16px"><p>No prior deeds found for ${escHtml(grantor)}</p></div>`;
      return;
    }

    let nodesHtml = '';
    chain.forEach((link, i) => {
      const isCurrent = i === 0;
      const hasPlatRef = link.has_plat_ref;
      const cls = isCurrent ? 'chain-current' : hasPlatRef ? 'chain-plat-found' : '';
      if (i > 0) nodesHtml += '<div class="chain-arrow">←</div>';
      nodesHtml += `
        <div class="chain-node ${cls}" onclick="loadS2Detail('${link.doc_no}', -1, null)" title="Click to load this deed">
          <div class="chain-node-name">${escHtml((link.grantor || '').split(',')[0])}</div>
          <div class="chain-node-meta">${escHtml(link.doc_no)}</div>
          <div class="chain-node-meta">${escHtml(link.date || '')}</div>
          ${hasPlatRef ? '<div style="font-size:9px;color:#e3c55a;margin-top:4px">📄 Plat ref found</div>' : ''}
        </div>`;
    });

    chainEl.querySelector('.glass-card').innerHTML = `
      <div class="row-layout justify-between mb-2">
        <div style="font-size:13px;font-weight:700;color:var(--accent2)">🔗 Chain of Title — ${chain.length} link${chain.length !== 1 ? 's' : ''}</div>
        <button class="btn btn-outline btn-sm" onclick="document.getElementById('chainTimelineWrap').remove()">✕ Close</button>
      </div>
      <div class="chain-timeline">${nodesHtml}</div>
      ${res.stop_reason ? `<div class="text-xs text-text3 mt-2" style="padding:0 4px">${escHtml(res.stop_reason)}</div>` : ''}`;

  } catch (e) {
    chainEl.querySelector('.glass-card').innerHTML = `
      <div class="text-danger p-3">Chain search error: ${e.message}</div>`;
    showToast('Chain search failed: ' + e.message, 'error');
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// KEYBOARD SHORTCUTS
// ─────────────────────────────────────────────────────────────────────────────

function _handleGlobalKeyboard(e) {
  const tag = (document.activeElement?.tagName || '').toLowerCase();
  const isInputFocused = tag === 'input' || tag === 'textarea' || tag === 'select' || document.activeElement?.isContentEditable;

  // ── Escape: close any open modal ─────────────────────────────────────────
  if (e.key === 'Escape') {
    const modals = ['settingsOverlay', 'cabinetOverlay', 'kmlIndexOverlay', 'propPickerOverlay'];
    for (const id of modals) {
      const el = document.getElementById(id);
      if (el && !el.classList.contains('hidden')) {
        el.classList.add('hidden');
        e.preventDefault();
        return;
      }
    }
    // Also close any dynamically created modals
    const dynOverlay = document.querySelector('.modal-overlay:not(.hidden)');
    if (dynOverlay) { dynOverlay.classList.add('hidden'); e.preventDefault(); return; }
  }

  // ── Ctrl+Enter: Save & Continue (primary action for current step) ────────
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    const step = state.currentStep;
    if (step === 1) {
      document.getElementById('btnStartSession')?.click();
    } else if (step === 2) {
      const saveBtn = document.getElementById('btnS2Save');
      if (saveBtn && !saveBtn.disabled) saveBtn.click();
      else doStep2Search();
    } else if (step === 4) {
      // Add all discovered adjoiners and continue
      const addAllBtn = document.querySelector('[onclick="addAllAndContinue()"]');
      if (addAllBtn) addAllBtn.click();
    } else if (step === 6) {
      document.getElementById('btnGenerateDxf')?.click();
    }
    return;
  }

  // ── Ctrl+F: Focus search field ───────────────────────────────────────────
  if (e.key === 'f' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    const step = state.currentStep;
    if (step === 2) document.getElementById('s2SearchName')?.focus();
    else if (step === 4) document.getElementById('s4ManualName')?.focus();
    return;
  }

  // ── Number keys 1-6: Jump to step (when no input focused) ────────────────
  if (!isInputFocused && !e.ctrlKey && !e.altKey && !e.metaKey) {
    const num = parseInt(e.key);
    if (num >= 1 && num <= 6) {
      e.preventDefault();
      goToStep(num);
      return;
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// UTILITIES
// ─────────────────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/**
 * Clean an owner name from ArcGIS / KML / deed text:
 *   - Strip trailing conjunctions: " &", " OR", " AND"
 *   - Strip trailing commas/whitespace
 *   - Returns cleaned name (preserves original casing)
 */
function cleanOwnerName(name) {
  if (!name) return '';
  let n = name.trim();
  // Iteratively strip trailing conjunctions (handles "NAME &" or "NAME, &")
  for (let i = 0; i < 3; i++) {
    n = n.replace(/[,\s]+(?:&|AND|OR)\s*$/i, '').trim();
  }
  // Strip trailing comma
  n = n.replace(/,\s*$/, '').trim();
  return n;
}

/**
 * Check if a string is a raw UPC code (all digits, possibly with spaces/dashes).
 */
function isUpcCode(name) {
  return /^[\d\s\-]+$/.test(name.trim()) && name.trim().length >= 3;
}

/**
 * Resolve a UPC code to an owner name via ArcGIS.
 * Returns the owner name, or null if resolution fails.
 */
async function resolveUpcToOwner(upc) {
  try {
    const res = await apiFetch('/property-address', 'POST', { upc: upc.trim() });
    if (res.success && res.owner_official && res.owner_official.trim().length > 2) {
      return cleanOwnerName(res.owner_official.trim());
    }
  } catch (e) {
    console.warn(`[resolveUpc] Failed to resolve UPC ${upc}:`, e.message);
  }
  return null;
}

function getTypeClass(type) {
  const t = (type || "").toLowerCase();
  if (t.includes("deed") || t.includes("warranty") || t.includes("quitclaim")) return "badge-deed";
  if (t.includes("mortgage") || t.includes("assignment")) return "badge-online";
  return "badge-other";
}

async function apiFetch(path, method = "GET", body = null, { signal } = {}) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  if (signal) opts.signal = signal;
  const res = await fetch(API + path, opts);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── Browser Download Helpers ─────────────────────────────────────────────────
// These stream files to the user's own computer via the browser's download
// mechanism, rather than saving to the server's filesystem.

/**
 * Download a deed/plat PDF from the county portal → user's browser.
 * @param {string} docNo  - Document number
 * @param {object} detail - Deed detail object (Grantor, Grantee, Location)
 * @param {string} [customFilename] - Optional override filename
 */
async function downloadDeedToBrowser(docNo, detail, customFilename) {
  if (!docNo) { showToast('No document number', 'error'); return; }
  showToast(`⬇ Downloading ${docNo} to your computer…`, 'info');
  try {
    const res = await fetch(API + '/download-to-browser', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        doc_no: docNo,
        grantor: detail?.Grantor || detail?.grantor || '',
        grantee: detail?.Grantee || detail?.grantee || '',
        location: detail?.Location || detail?.location || '',
        filename: customFilename || '',
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      showToast('Download failed: ' + (err.error || res.statusText), 'error');
      return;
    }
    const blob = await res.blob();
    const cd = res.headers.get('Content-Disposition') || '';
    const filenameMatch = cd.match(/filename="(.+?)"/);
    const fname = filenameMatch ? filenameMatch[1] : `${docNo}.pdf`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fname;
    a.click();
    URL.revokeObjectURL(url);
    showToast(`✓ Downloaded ${fname}`, 'success');
  } catch (e) {
    showToast('Download error: ' + e.message, 'error');
  }
}

/**
 * Download a local file (cabinet plat, saved deed, DXF) → user's browser.
 * @param {string} filePath - Server-side absolute path to the file
 * @param {string} [customFilename] - Optional override filename
 */
async function downloadLocalFileToBrowser(filePath, customFilename) {
  if (!filePath) { showToast('No file path', 'error'); return; }
  showToast(`⬇ Downloading to your computer…`, 'info');
  try {
    const res = await fetch(API + '/serve-local-file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: filePath, filename: customFilename || '' }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      showToast('Download failed: ' + (err.error || res.statusText), 'error');
      return;
    }
    const blob = await res.blob();
    const cd = res.headers.get('Content-Disposition') || '';
    const filenameMatch = cd.match(/filename="(.+?)"/);
    const fname = filenameMatch ? filenameMatch[1] : (customFilename || filePath.split(/[/\\]/).pop());
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fname;
    a.click();
    URL.revokeObjectURL(url);
    showToast(`✓ Downloaded ${fname}`, 'success');
  } catch (e) {
    showToast('Download error: ' + e.message, 'error');
  }
}

/**
 * Generate DXF on server, then stream it to the user's browser.
 * @param {object} dxfResult - The result from /api/generate-dxf (must have saved_to, filename)
 */
async function downloadDxfToBrowser(dxfResult) {
  if (!dxfResult?.saved_to) { showToast('No DXF file path', 'error'); return; }
  await downloadLocalFileToBrowser(dxfResult.saved_to, dxfResult.filename);
}


// ─────────────────────────────────────────────────────────────────────────────
// ONLINE SURVEY RESULTS — FILTER BAR (Sticky Note: "Refine Search")
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Render online survey results with a compact filter bar above them.
 * Stores raw hits in state._onlineSurveyHits for client-side filtering.
 */
function _renderOnlineSurveyHits(container, hits, searchName) {
  state._onlineSurveyHits = hits;

  // Filter bar
  const filterBar = '<div style="display:flex;gap:6px;padding:6px 8px;border-bottom:1px solid var(--border);align-items:center;flex-wrap:wrap">' +
    '<input id="s3OnlineFilter" class="inp" style="flex:1;min-width:100px;font-size:11px;padding:4px 8px;height:26px" ' +
    'placeholder="Filter name or doc#…" oninput="filterOnlineSurveyHits()">' +
    '<select id="s3OnlineYearFilter" class="inp" style="width:80px;font-size:11px;padding:4px;height:26px" onchange="filterOnlineSurveyHits()">' +
    '<option value="">All Years</option>' +
    _buildYearOptions(hits) +
    '</select>' +
    '<span style="font-size:10px;color:var(--text3)" id="s3OnlineCount">' + hits.length + ' records</span>' +
    '</div>';

  container.innerHTML = filterBar + _buildOnlineHitRows(hits);
}

/** Build year <option> tags from distinct years in the hits */
function _buildYearOptions(hits) {
  const years = new Set();
  hits.forEach(r => {
    const d = r.recorded_date || r.date || '';
    const m = d.match(/(\d{4})/);
    if (m) years.add(m[1]);
  });
  return [...years].sort().reverse().map(y => '<option value="' + y + '">' + y + '</option>').join('');
}

/** Build the HTML rows for online survey hits */
function _buildOnlineHitRows(hits) {
  return hits.map(r => {
    const name = escHtml((r.grantor || '').split(',')[0] || r.grantor || '');
    const docNo = escHtml(r.doc_no || '');
    const loc = escHtml(r.location || '');
    const itype = escHtml(r.instrument_type || '');
    const date = escHtml(r.recorded_date || r.date || '');

    return '<div class="plat-item">' +
      '<div class="plat-info">' +
      '<span class="plat-name" title="' + loc + '">' + name + '</span>' +
      '<span class="plat-meta">' + itype +
      ' &nbsp;&nbsp; ' + date +
      ' &nbsp;&nbsp; Doc <span class="text-accent2">' + docNo + '</span></span>' +
      '</div>' +
      '<div style="display:flex;gap:4px;flex-wrap:wrap">' +
      '<button class="btn btn-outline btn-sm" ' +
      'onclick="saveClientPlatOnline(\'' + r.doc_no + '\',\'' + escHtml(r.location || '').replace(/'/g, "\\\\'") + '\')">' +
      '\u2B07 Save</button>' +
      '<button class="btn btn-sm" style="background:var(--accent2);color:#fff;font-size:10px" ' +
      'onclick="downloadDeedToBrowser(\'' + r.doc_no + '\', {grantor:\'' + escHtml((r.grantor||'').replace(/'/g, "\\\\'")) + '\',location:\'' + escHtml((r.location||'').replace(/'/g, "\\\\'")) + '\'})" ' +
      'title="Download to your computer">\u2B07 Download</button>' +
      '</div>' +
      '</div>';
  }).join('');
}

/** Client-side filter for online survey results */
function filterOnlineSurveyHits() {
  const hits = state._onlineSurveyHits || [];
  const q = (document.getElementById('s3OnlineFilter')?.value || '').trim().toLowerCase();
  const yearFilter = document.getElementById('s3OnlineYearFilter')?.value || '';
  const countEl = document.getElementById('s3OnlineCount');
  const container = document.getElementById('s3OnlinePlats');

  let filtered = hits;
  if (q) {
    filtered = filtered.filter(r =>
      (r.grantor || '').toLowerCase().includes(q) ||
      (r.doc_no || '').toLowerCase().includes(q) ||
      (r.location || '').toLowerCase().includes(q) ||
      (r.grantee || '').toLowerCase().includes(q)
    );
  }
  if (yearFilter) {
    filtered = filtered.filter(r => {
      const d = r.recorded_date || r.date || '';
      return d.includes(yearFilter);
    });
  }

  if (countEl) countEl.textContent = filtered.length + ' of ' + hits.length + ' records';

  // Re-render just the results (keep the filter bar intact)
  const filterBar = container.querySelector('div:first-child');
  const rows = _buildOnlineHitRows(filtered);
  // Remove everything after the filter bar
  while (container.children.length > 1) container.removeChild(container.lastChild);
  container.insertAdjacentHTML('beforeend', rows || '<div class="empty-state text-text3 text-sm p-3">No matches for filter.</div>');
}


// ─────────────────────────────────────────────────────────────────────────────
// STEP 5: RESEARCH EXPORT (Sticky Note: "View what it's found & Save it")
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Export the current research session as a printable HTML report.
 * Opens in a new tab with all findings formatted for printing.
 */
function exportResearchReport() {
  const rs = state.researchSession;
  if (!rs) { showToast('No active session', 'warn'); return; }

  const subjects = rs.subjects || [];
  const client = subjects.find(s => s.type === 'client');
  const adjoiners = subjects.filter(s => s.type === 'adjoiner');

  let html = '<!DOCTYPE html><html><head>';
  html += '<title>Research Report — Job #' + rs.job_number + ' ' + escHtml(rs.client_name) + '</title>';
  html += '<style>';
  html += 'body{font-family:system-ui,-apple-system,sans-serif;max-width:900px;margin:auto;padding:30px;color:#222}';
  html += 'h1{border-bottom:3px solid #2d8a6e;padding-bottom:8px;color:#1a3028}';
  html += 'h2{color:#2d8a6e;margin-top:24px}';
  html += 'table{width:100%;border-collapse:collapse;margin:12px 0}';
  html += 'th,td{border:1px solid #ddd;padding:6px 10px;text-align:left;font-size:13px}';
  html += 'th{background:#f5f5f5;font-weight:700;text-transform:uppercase;font-size:11px;letter-spacing:.3px}';
  html += '.status-done{color:#2d8a6e;font-weight:700} .status-pending{color:#999}';
  html += '.badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600}';
  html += '.badge-ok{background:#e6f7f1;color:#2d8a6e} .badge-todo{background:#f5f5f5;color:#999}';
  html += '@media print{body{padding:10px}h1{font-size:20px}}';
  html += '</style></head><body>';

  html += '<h1>\ud83d\udcc1 Research Report</h1>';
  html += '<table>';
  html += '<tr><th>Job Number</th><td>#' + rs.job_number + '</td></tr>';
  html += '<tr><th>Client</th><td>' + escHtml(rs.client_name) + '</td></tr>';
  html += '<tr><th>Job Type</th><td>' + escHtml(rs.job_type || 'BDY') + '</td></tr>';
  html += '<tr><th>Date</th><td>' + new Date().toLocaleDateString() + '</td></tr>';
  html += '<tr><th>Total Subjects</th><td>' + subjects.length + ' (' + adjoiners.length + ' adjoiners)</td></tr>';
  html += '</table>';

  // Client section
  if (client) {
    html += '<h2>\u2605 Client: ' + escHtml(client.name) + '</h2>';
    html += '<table>';
    html += '<tr><th>Deed</th><td>' + (client.deed_saved ? '<span class="badge badge-ok">\u2713 Saved</span>' : '<span class="badge badge-todo">Pending</span>');
    if (client.deed_path) html += ' <br><small>' + escHtml(client.deed_path.split(/[/\\]/).pop()) + '</small>';
    html += '</td></tr>';
    html += '<tr><th>Plat</th><td>' + (client.plat_saved ? '<span class="badge badge-ok">\u2713 Saved</span>' : '<span class="badge badge-todo">Pending</span>');
    if (client.plat_path) html += ' <br><small>' + escHtml(client.plat_path.split(/[/\\]/).pop()) + '</small>';
    html += '</td></tr>';
    if (client.property_description) html += '<tr><th>Description</th><td style="font-size:11px;font-family:monospace;white-space:pre-wrap">' + escHtml(client.property_description.substring(0, 500)) + '</td></tr>';
    if (client.doc_no) html += '<tr><th>Doc#</th><td>' + escHtml(client.doc_no) + '</td></tr>';
    html += '</table>';
  }

  // Adjoiners table
  if (adjoiners.length) {
    html += '<h2>\u2B21 Adjoiners (' + adjoiners.length + ')</h2>';
    html += '<table><tr><th>#</th><th>Name</th><th>Deed</th><th>Plat</th><th>Status</th><th>Notes</th></tr>';
    adjoiners.forEach((s, i) => {
      const st = s.status || 'pending';
      const stClass = st === 'done' ? 'status-done' : 'status-pending';
      html += '<tr>';
      html += '<td>' + (i + 1) + '</td>';
      html += '<td><strong>' + escHtml(s.name) + '</strong>' + (s.upc ? '<br><small>UPC ' + escHtml(s.upc) + '</small>' : '') + '</td>';
      html += '<td>' + (s.deed_saved ? '\u2713' : '\u2014') + '</td>';
      html += '<td>' + (s.plat_saved ? '\u2713' : '\u2014') + '</td>';
      html += '<td class="' + stClass + '">' + st.charAt(0).toUpperCase() + st.slice(1) + '</td>';
      html += '<td style="font-size:11px">' + escHtml(s.notes || '') + '</td>';
      html += '</tr>';
    });
    html += '</table>';
  }

  html += '<div style="margin-top:40px;padding-top:12px;border-top:1px solid #ddd;font-size:11px;color:#999">';
  html += 'Generated by Deed & Plat Helper \u2014 ' + new Date().toLocaleString();
  html += '</div>';
  html += '</body></html>';

  const blob = new Blob([html], { type: 'text/html' });
  const url = URL.createObjectURL(blob);
  window.open(url, '_blank');
  showToast('\ud83d\udcca Research report opened in new tab \u2014 use Ctrl+P to print', 'success');
}

/**
 * View a saved deed or plat file from the research board.
 * Shows it in the plat preview panel (reuses existing infrastructure).
 */
function viewSubjectFile(subjectId, fileType) {
  const rs = state.researchSession;
  if (!rs) return;
  const subj = rs.subjects.find(s => s.id === subjectId);
  if (!subj) return;

  const path = fileType === 'deed' ? subj.deed_path : subj.plat_path;
  if (!path) {
    showToast('No ' + fileType + ' file saved yet', 'warn');
    return;
  }
  const filename = path.split(/[/\\]/).pop();
  showPlatPreview(path, filename, '');
}


//  Toast 
let _toastEl;
function showToast(msg, type = "info") {
  if (!_toastEl) {
    _toastEl = document.createElement("div");
    _toastEl.style.cssText = "position:fixed;bottom:80px;right:24px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-width:360px;pointer-events:none";
    document.body.appendChild(_toastEl);
  }
  const c = { success: ["#1a3028", "#2d8a6e", "#56d3a0"], error: ["#2d1015", "#da3633", "#ff7b72"], warn: ["#2a2108", "#c9a227", "#e3c55a"], info: ["#1c2340", "#3b5e99", "#79a8e0"] };
  const [bg, border, color] = c[type] || c.info;
  const t = document.createElement("div");
  t.style.cssText = `background:${bg};border:1px solid ${border};color:${color};padding:12px 16px;border-radius:10px;font-size:13px;font-weight:500;box-shadow:0 4px 24px rgba(0,0,0,.5);animation:toastIn .25s ease;pointer-events:auto`;
  t.textContent = msg;
  _toastEl.appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(() => t.remove(), 300); }, 3500);
}

// Inject toast animation
const _toastStyle = document.createElement("style");
_toastStyle.textContent = `
  @keyframes toastIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:none} }
  .badge-deed   { background:rgba(86,211,160,.15); color:#56d3a0; border:1px solid rgba(86,211,160,.3); }
  .badge-other  { background:rgba(201,162,39,.15);  color:#c9a227; border:1px solid rgba(201,162,39,.3); }
  .bg-green { background:rgba(45,138,110,.1); }
  .bg-gold  { background:rgba(201,162,39,.1); }
  .bg-red   { background:rgba(218,54,51,.1);  }
  .text-gold { color:#e3c55a; }
  .chip { display:inline-flex;align-items:center;gap:4px;font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px; }
  .chip-done { background:rgba(45,138,110,.15);color:#56d3a0;border:1px solid rgba(45,138,110,.3); }
  .chip-todo { background:rgba(0,0,0,.2);color:var(--text3);border:1px solid var(--border); }
  .btn-icon-sm { background:none;border:none;cursor:pointer;font-size:13px;padding:0 4px; }
  .exc-chip { display:inline-flex;align-items:center;font-size:9px;font-weight:700;padding:2px 6px;border-radius:6px;cursor:pointer;transition:all .15s;border:1px solid transparent; }
  .exc-off   { background:var(--bg);color:var(--text3);border-color:var(--border); }
  .exc-active { background:rgba(201,162,39,.15);color:#c9a227;border-color:rgba(201,162,39,.3); }
  .chain-box { background:rgba(0,0,0,.2);border:1px solid var(--border);border-radius:6px;padding:8px 10px;font-size:11px; }
  .chain-years { display:flex;gap:4px;flex-wrap:wrap;margin-top:5px; }
  .year-chip { background:rgba(45,138,110,.15);color:var(--accent2);border:1px solid var(--accent);border-radius:4px;font-size:10px;padding:1px 6px;font-family:monospace;cursor:pointer; }
  .link-btn { background:none;border:none;cursor:pointer;color:var(--text3);font-size:10px;padding:0;font-family:inherit; }
  .parcel-client-card,.parcel-card { background:rgba(0,0,0,.2);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:10px; }
  .parcel-client-card { border-left:3px solid var(--accent); }
  .parcel-card  { border-left:3px solid #7a4f9a; }
  .status-dot.loading { background:var(--gold);animation:pulse 1s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .spinner { border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;width:18px;height:18px;animation:spin .8s linear infinite;display:inline-block; }
  @keyframes spin { to{transform:rotate(360deg)} }
  .font-bold { font-weight:700; }
  .highlight  { color:var(--accent2) !important; }
`;
document.head.appendChild(_toastStyle);


// ═══════════════════════════════════════════════════════════════════════════
// AI INSIGHTS PANEL — DASHBOARD & PREDICTIONS
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Load all AI insight data and populate the dashboard.
 * Called on page load (after DOMContentLoaded) and on manual refresh.
 */
async function refreshAiInsights() {
  // Fetch all three endpoints in parallel
  const [healthRes, conflictsRes, analyticsRes] = await Promise.allSettled([
    apiFetch('/index-health'),
    apiFetch('/data-conflicts'),
    apiFetch('/research-analytics'),
  ]);

  // ── Index Health Metrics ───────────────────────────────────────────────
  if (healthRes.status === 'fulfilled' && healthRes.value.success) {
    const h = healthRes.value;
    const totalEl = document.getElementById('aiTotalParcels');
    const arcEl = document.getElementById('aiArcgisPct');
    const staleRow = document.getElementById('aiStaleWarning');
    const staleText = document.getElementById('aiStaleText');

    if (totalEl) {
      totalEl.textContent = h.total_parcels ? h.total_parcels.toLocaleString() : '0';
      // Animate the counter
      totalEl.style.transition = 'color 0.3s';
      totalEl.style.color = h.total_parcels > 0 ? '#56d3a0' : '#ff7b72';
    }
    if (arcEl) {
      arcEl.textContent = h.total_parcels ? h.pct_with_arcgis + '%' : '—';
      arcEl.style.color = h.pct_with_arcgis >= 50 ? '#56d3a0' : h.pct_with_arcgis >= 20 ? '#e3c55a' : '#ff7b72';
    }

    // Stale warning
    if (staleRow && staleText) {
      if (h.stale_warning) {
        staleRow.classList.remove('hidden');
        staleText.textContent = `Index is ${h.index_age_days} days old — consider rebuilding`;
      } else if (h.newer_xml_files && h.newer_xml_files.length > 0) {
        staleRow.classList.remove('hidden');
        staleText.textContent = `${h.newer_xml_files.length} KML file(s) are newer than the index`;
      } else {
        staleRow.classList.add('hidden');
      }
    }
  }

  // ── Data Conflicts Count ───────────────────────────────────────────────
  if (conflictsRes.status === 'fulfilled' && conflictsRes.value.success) {
    const c = conflictsRes.value;
    const el = document.getElementById('aiConflictCount');
    if (el) {
      const total = c.conflict_count || 0;
      el.textContent = total;
      el.style.color = total === 0 ? '#56d3a0' : total <= 10 ? '#e3c55a' : '#ff7b72';
    }
  }

  // ── Research Analytics ─────────────────────────────────────────────────
  if (analyticsRes.status === 'fulfilled' && analyticsRes.value.success) {
    const a = analyticsRes.value;
    const jobsEl = document.getElementById('aiJobsScanned');
    if (jobsEl) {
      jobsEl.textContent = a.scanned_jobs || 0;
      jobsEl.style.color = a.scanned_jobs > 0 ? '#79a8e0' : 'var(--text3)';
    }

    // Show prediction if we have data
    if (a.predictions) {
      _showPrediction(a.predictions);
    }
  }
}

/** Show/update the complexity prediction row */
function _showPrediction(pred) {
  const row = document.getElementById('aiPredictionRow');
  if (!row) return;

  row.classList.remove('hidden');

  const compEl = document.getElementById('aiPredComplexity');
  const adjEl = document.getElementById('aiPredAdjoiners');
  const rangeEl = document.getElementById('aiPredRange');
  const cabsEl = document.getElementById('aiPredCabinets');
  const confEl = document.getElementById('aiPredConfidence');
  const simEl = document.getElementById('aiPredSimilar');

  if (compEl) {
    compEl.textContent = pred.predicted_complexity;
    compEl.className = 'ai-pred-complexity pred-' + pred.predicted_complexity;
  }
  if (adjEl) adjEl.textContent = pred.predicted_adjoiners;
  if (rangeEl && pred.adjoiner_range) {
    rangeEl.textContent = `(range: ${pred.adjoiner_range.p25}–${pred.adjoiner_range.p75})`;
  }
  if (cabsEl) cabsEl.textContent = (pred.likely_cabinets || []).map(c => 'Cab ' + c).join(', ');
  if (confEl) {
    confEl.textContent = pred.confidence;
    confEl.style.color = pred.confidence === 'high' ? '#56d3a0'
                        : pred.confidence === 'medium' ? '#e3c55a'
                        : '#ff7b72';
  }
  if (simEl) simEl.textContent = pred.similar_jobs_count || 0;
}

/** Re-predict when job type changes in Step 1 */
async function _onJobTypeChanged() {
  const typeEl = document.getElementById('setupJobType');
  if (!typeEl) return;
  try {
    const res = await apiFetch('/research-analytics/predict', 'POST', {
      job_type: typeEl.value,
    });
    if (res.success) _showPrediction(res);
  } catch (_) {}
}

// Wire up job type dropdown to update predictions
document.addEventListener('DOMContentLoaded', () => {
  const typeEl = document.getElementById('setupJobType');
  if (typeEl) typeEl.addEventListener('change', _onJobTypeChanged);

  // Load AI insights after a brief delay (don't block initial render)
  setTimeout(() => refreshAiInsights(), 800);
});


// ═══════════════════════════════════════════════════════════════════════════
// LEGAL DESCRIPTION SIMILARITY SEARCH
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Search for parcels with similar legal descriptions.
 * Uses the property description extracted from the current deed.
 */
async function findSimilarDescriptions() {
  // Get the extracted description from the current session
  const descWrap = document.getElementById('deedPropertyDescArea');
  if (!descWrap) {
    showToast('No property description available', 'warn');
    return;
  }

  // Try to get the description text from the prop-desc-text element
  const descEl = descWrap.querySelector('.prop-desc-text');
  const text = descEl ? descEl.textContent.trim() : '';

  if (!text || text.length < 20) {
    showToast('Extract the property description first (📜 Get Description)', 'warn');
    return;
  }

  const container = document.getElementById('deedTabAnalysis');
  if (container) {
    container.innerHTML = `<div class="loading-state flex-col gap-2"><div class="spinner"></div>Searching for similar descriptions…</div>`;
    // Switch to analysis tab
    switchDeedTab('analysis');
  }

  try {
    const res = await apiFetch('/similar-descriptions', 'POST', {
      text: text,
      min_score: 15,
      limit: 15,
    });

    if (!container) return;

    if (!res.success) {
      container.innerHTML = `<div class="empty-state text-danger">Error: ${escHtml(res.error || 'Unknown')}</div>`;
      return;
    }

    if (res.count === 0) {
      container.innerHTML = `<div class="empty-state"><div class="empty-icon">🔍</div><p>No similar descriptions found in the parcel index.</p></div>`;
      return;
    }

    let html = `
      <div style="padding:14px 16px;border-bottom:1px solid var(--border);background:rgba(121,168,224,0.05)">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:#79a8e0;margin-bottom:4px">
          🔍 Similar Descriptions Found
        </div>
        <div style="font-size:12px;color:var(--text2)">${res.count} parcels match this legal description</div>
      </div>
      <div style="padding:8px;overflow-y:auto;max-height:500px">`;

    for (const r of res.results) {
      const s = r.similarity;
      const scoreColor = s.score >= 60 ? '#56d3a0' : s.score >= 30 ? '#e3c55a' : '#79a8e0';
      const shared = [];
      if (s.shared_trs.length) shared.push('TRS: ' + s.shared_trs.join(', '));
      if (s.shared_cabs.length) shared.push('Cabs: ' + s.shared_cabs.join(', '));
      if (s.shared_names.length) shared.push('Names: ' + s.shared_names.join(', '));

      html += `
        <div style="padding:10px 12px;margin:4px;border-radius:8px;background:rgba(0,0,0,0.2);border:1px solid rgba(255,255,255,0.06);cursor:default;transition:all .15s"
             onmouseenter="this.style.borderColor='rgba(121,168,224,0.3)'" onmouseleave="this.style.borderColor='rgba(255,255,255,0.06)'">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
            <div style="font-size:13px;font-weight:700;color:var(--text)">${escHtml(r.owner || 'Unknown')}</div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:800;color:${scoreColor}">${s.score}%</div>
          </div>
          <div style="font-size:11px;color:var(--text3);line-height:1.6">
            ${r.upc ? `<span style="font-family:monospace;color:var(--accent2)">UPC ${escHtml(r.upc)}</span> · ` : ''}
            ${r.plat ? `Plat: ${escHtml(r.plat)}` : ''}
            ${r.trs ? ` · TRS: ${escHtml(r.trs)}` : ''}
            ${r.book ? ` · Bk ${escHtml(r.book)}/${escHtml(r.page || '')}` : ''}
          </div>
          ${shared.length ? `
          <div style="margin-top:4px;display:flex;flex-wrap:wrap;gap:4px">
            ${shared.map(s => `<span style="font-size:9px;padding:2px 6px;border-radius:6px;background:rgba(121,168,224,0.1);border:1px solid rgba(121,168,224,0.2);color:#79a8e0">${escHtml(s)}</span>`).join('')}
          </div>` : ''}
          <div style="display:flex;gap:8px;margin-top:6px;font-size:10px;color:var(--text3)">
            <span title="TRS Match">🏠 ${s.components.trs_match}%</span>
            <span title="Text Similarity">📝 ${s.components.text_similarity}%</span>
            <span title="Cabinet Overlap">🗄️ ${s.components.cab_overlap}%</span>
            <span title="Name Overlap">👤 ${s.components.name_overlap}%</span>
          </div>
        </div>`;
    }

    html += '</div>';
    container.innerHTML = html;

  } catch (e) {
    if (container) {
      container.innerHTML = `<div class="empty-state text-danger">Search failed: ${e.message}</div>`;
    }
  }
}


// ─────────────────────────────────────────────────────────────────────────────
// SAAS AUTH — Login, Register, Account Badge, Upgrade Modal
// ─────────────────────────────────────────────────────────────────────────────

let _saasUser = null;   // current logged-in SaaS user, or null

/** Check if we have a valid SaaS session on page load. */
async function initSaasAuth() {
  try {
    const res = await fetch('/auth/me', { credentials: 'include' });
    if (res.ok) {
      const data = await res.json();
      if (data.success) { _saasUser = data.user; _updateSaasBadge(); return; }
    }
  } catch (_) { }
  // Not logged in — show Sign In button
  _saasUser = null;
  _updateSaasBadge();
}

/** Update the nav bar badge to reflect login state. */
function _updateSaasBadge() {
  const badge   = document.getElementById('saasBadge');
  const loginBtn= document.getElementById('btnSaasLogin');
  if (!badge || !loginBtn) return;

  if (_saasUser) {
    const email   = _saasUser.email || '';
    const tier    = _saasUser.tier  || 'free';
    const initials= email.slice(0,2).toUpperCase();
    document.getElementById('saasAvatar').textContent   = initials;
    document.getElementById('saasEmail').textContent    = email;
    const tierEl = document.getElementById('saasTierBadge');
    tierEl.textContent  = tier.charAt(0).toUpperCase() + tier.slice(1);
    tierEl.className    = 'saas-tier-badge' + (tier !== 'free' ? ` tier-${tier}` : '');
    badge.classList.remove('hidden');
    loginBtn.style.display = 'none';
    // Account dropdown — show the right action buttons based on tier
    const upgradeBtn     = document.getElementById('acctMenuUpgrade');
    const teamBtn        = document.getElementById('acctMenuTeamUpgrade');
    const manageBillBtn  = document.getElementById('acctMenuManageBilling');
    if (upgradeBtn)    upgradeBtn.style.display    = tier === 'free' ? '' : 'none';
    if (teamBtn)       teamBtn.style.display       = tier === 'pro'  ? '' : 'none';
    if (manageBillBtn) manageBillBtn.style.display = tier !== 'free' ? '' : 'none';
  } else {
    badge.classList.add('hidden');
    loginBtn.style.display = '';
  }
  // Re-apply pro feature locks whenever session state changes
  if (typeof _applyProFeatureLocks === 'function') _applyProFeatureLocks();
  // Show/hide Team button in Settings footer based on tier
  if (typeof _applyTeamVisibility === 'function') {
    const tier = _saasUser?.tier || 'free';
    const role = _saasUser?.team_role || null;
    _applyTeamVisibility(tier, role);
  }
}

/** Show the auth modal. mode = 'login' | 'register' | 'upgrade' */
function showAuthModal(mode) {
  document.getElementById('authOverlay').classList.remove('hidden');
  switchAuthTab(mode === 'register' ? 'register' : 'login');
  setTimeout(() => document.getElementById('authEmail')?.focus(), 100);
}

function closeAuthModal() {
  document.getElementById('authOverlay').classList.add('hidden');
  // Clear fields
  ['authEmail','authPassword','authPasswordConfirm'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  document.getElementById('authError').classList.add('hidden');
}

function switchAuthTab(tab) {
  const isRegister = tab === 'register';
  document.getElementById('authTabLogin').classList.toggle('auth-tab-active', !isRegister);
  document.getElementById('authTabRegister').classList.toggle('auth-tab-active', isRegister);
  document.getElementById('authRegisterExtra').style.display = isRegister ? '' : 'none';
  document.getElementById('btnAuthSubmit').textContent = isRegister ? 'Create Free Account' : 'Sign In';
  document.getElementById('authModalTitle').textContent = isRegister ? '✨ Create Account' : '🔑 Sign In';
  document.getElementById('authSwitchText').textContent = isRegister ? 'Already have an account?' : "Don't have an account?";
  document.getElementById('authSwitchLink').textContent = isRegister ? 'Sign in' : 'Create one free';
  document.getElementById('authError').classList.add('hidden');
}

async function doAuthSubmit() {
  const isRegister = document.getElementById('authTabRegister').classList.contains('auth-tab-active');
  const email      = (document.getElementById('authEmail')?.value || '').trim();
  const password   = document.getElementById('authPassword')?.value || '';
  const confirm    = document.getElementById('authPasswordConfirm')?.value || '';
  const errEl      = document.getElementById('authError');
  const btn        = document.getElementById('btnAuthSubmit');

  errEl.classList.add('hidden');
  if (!email || !password) { errEl.textContent = 'Email and password are required.'; errEl.classList.remove('hidden'); return; }
  if (isRegister && password !== confirm) { errEl.textContent = 'Passwords do not match.'; errEl.classList.remove('hidden'); return; }

  btn.disabled = true;
  btn.textContent = isRegister ? 'Creating account…' : 'Signing in…';

  try {
    const endpoint = isRegister ? '/auth/register' : '/auth/login';
    const res = await fetch(endpoint, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();

    if (data.success) {
      _saasUser = data.user;
      _updateSaasBadge();
      closeAuthModal();
      showToast(isRegister ? '✅ Account created! Welcome to Deed Helper.' : `Welcome back, ${email.split('@')[0]}!`, 'success');
      // Load search history so recent queries are ready
      if (typeof _loadSearchHistory === 'function') _loadSearchHistory();
      // If user arrived via a team invite link, auto-accept the invite now
      const pendingTeamToken = sessionStorage.getItem('pendingTeamToken');
      if (pendingTeamToken) {
        sessionStorage.removeItem('pendingTeamToken');
        apiFetch('/api/team/join', 'POST', { token: pendingTeamToken }).then(res => {
          if (res.success) {
            showToast('🎉 ' + res.message + ' You now have Team access!', 'success');
            if (res.user) { _saasUser = res.user; _updateSaasBadge(); }
          } else {
            showToast('⚠ Team invite: ' + (res.error || 'Could not join team.'), 'warn');
          }
        }).catch(() => {});
      }
    } else {
      errEl.textContent = data.error || 'Something went wrong.';
      errEl.classList.remove('hidden');
    }
  } catch (e) {
    errEl.textContent = 'Network error: ' + e.message;
    errEl.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    btn.textContent = isRegister ? 'Create Free Account' : 'Sign In';
  }
}

async function doSaasLogout() {
  closeAccountMenu();
  await fetch('/auth/logout', { method: 'POST', credentials: 'include' });
  _saasUser = null;
  _updateSaasBadge();
  showToast('Signed out.', 'info');
}

// ── Account dropdown ──────────────────────────────────────────────────────────
function showAccountMenu(e) {
  if (e && e.stopPropagation) e.stopPropagation();
  if (!_saasUser) { showAuthModal('login'); return; }
  const menu = document.getElementById('accountMenu');
  if (!menu) return;

  const isOpen = !menu.classList.contains('hidden');
  if (isOpen) { menu.classList.add('hidden'); return; }

  // Refresh usage stats
  document.getElementById('acctMenuEmail').textContent = _saasUser.email || '';
  const tier = _saasUser.tier || 'free';
  document.getElementById('acctMenuTier').textContent =
    tier === 'free' ? 'Free Plan' : tier === 'pro' ? 'Pro Plan — $29/mo' : 'Team Plan — $79/mo';

  const used  = _saasUser.search_count_this_month || 0;
  const limit = tier === 'free' ? 10 : null;
  document.getElementById('acctMenuSearches').textContent = used;
  document.getElementById('acctMenuLimit').textContent    = limit === null ? '∞' : limit;

  // Usage bar
  const bar = document.getElementById('acctMenuUsageBar');
  if (bar) bar.style.width = limit ? Math.min(100, Math.round((used / limit) * 100)) + '%' : '0%';

  // Show/hide Manage Team (team tier only)
  const teamBtn = document.getElementById('acctMenuTeamManage');
  if (teamBtn) teamBtn.style.display = (tier === 'team') ? '' : 'none';

  // Show/hide Manage Billing (paid tiers)
  const billingBtn = document.getElementById('acctMenuManageBilling');
  if (billingBtn) billingBtn.style.display = (tier !== 'free') ? '' : 'none';

  // Show/hide Upgrade
  const upgradeBtn = document.getElementById('acctMenuUpgrade');
  if (upgradeBtn) upgradeBtn.style.display = (tier === 'free') ? '' : 'none';
  const teamUpBtn = document.getElementById('acctMenuTeamUpgrade');
  if (teamUpBtn) teamUpBtn.style.display = (tier === 'pro') ? '' : 'none';

  menu.classList.remove('hidden');
  // Delay registering the outside-click listener so this click doesn't immediately close it
  requestAnimationFrame(() => {
    document.addEventListener('click', _closeMenuOnClickOutside, { once: true });
  });
}

function closeAccountMenu() {
  document.getElementById('accountMenu')?.classList.add('hidden');
}

function _closeMenuOnClickOutside(e) {
  const menu  = document.getElementById('accountMenu');
  const badge = document.getElementById('saasBadge');
  if (menu && !menu.contains(e.target) && !badge?.contains(e.target)) {
    menu.classList.add('hidden');
  }
}

// ── Upgrade modal ─────────────────────────────────────────────────────────────
function showUpgradeModal(featureName, message) {
  document.getElementById('upgradeFeatureName').textContent = featureName || 'Pro Feature';
  document.getElementById('upgradeMsg').textContent = message || 'This feature requires a Pro subscription.';
  document.getElementById('upgradeOverlay').classList.remove('hidden');
}

function closeUpgradeModal() {
  document.getElementById('upgradeOverlay').classList.add('hidden');
}

/** Detect if we're running on a local/LAN server (no SaaS billing applies). */
function _isLocalMode() {
  const h = window.location.hostname;
  return h === 'localhost' || h === '127.0.0.1' || h.startsWith('192.168.') || h.startsWith('10.') || h.endsWith('.local');
}

/**
 * Returns true if the current SaaS user has Pro or Team.
 * In local/LAN mode, always returns true — no payment required.
 */
function _hasPro() {
  if (_isLocalMode()) return true;
  if (!_saasUser) return false;
  const tier = _saasUser.tier || 'free';
  return tier === 'pro' || tier === 'team';
}

/** Call this when an API returns upgrade_required: true */
function handleUpgradeRequired(res, featureName) {
  if (_isLocalMode()) return;   // No paywall on LAN
  const msg = res.error || 'This feature requires a Pro subscription.';
  if (!_saasUser) {
    showAuthModal('login');
    showToast('Please sign in to use this feature.', 'warn');
  } else {
    handleUpgradeClick();
  }
}

// ── Stripe Checkout & Billing Portal ─────────────────────────────────────

/** Route the upgrade button based on current tier */
function handleUpgradeClick() {
  const tier = _saasUser?.tier || 'free';
  if (tier === 'free') {
    startCheckout('pro');
  } else if (tier === 'pro') {
    startCheckout('team');
  } else {
    openBillingPortal();
  }
}

/** Redirect to Stripe Checkout for the given tier */
async function startCheckout(tier) {
  if (!_saasUser) { showAuthModal('login'); return; }
  showToast('Opening secure checkout…', 'info');
  try {
    const res = await apiFetch('/stripe/checkout', 'POST', { tier });
    if (res.success && res.checkout_url) {
      window.location.href = res.checkout_url;  // redirect to Stripe Hosted Checkout
    } else {
      showToast('Checkout error: ' + (res.error || 'Unknown error'), 'error');
    }
  } catch (e) {
    showToast('Checkout failed: ' + e.message, 'error');
  }
}

/** Open the Stripe Customer Portal so the user can manage / cancel their subscription */
async function openBillingPortal() {
  if (!_saasUser) return;
  showToast('Opening billing portal…', 'info');
  try {
    const res = await apiFetch('/stripe/portal', 'POST');
    if (res.success && res.portal_url) {
      window.open(res.portal_url, '_blank');
    } else {
      showToast('Portal error: ' + (res.error || 'Unknown error'), 'error');
    }
  } catch (e) {
    showToast('Portal failed: ' + e.message, 'error');
  }
}

// (upgrade-success detection is handled by _checkUpgradeSuccess in DOMContentLoaded)

// ── Account Detail Modal ───────────────────────────────────────────────────────

async function showAccountDetail() {
  if (!_saasUser) { showAuthModal('login'); return; }
  document.getElementById('accountDetailOverlay')?.classList.remove('hidden');
  document.getElementById('acctDetailLoading').style.display = '';
  document.getElementById('acctDetailContent').style.display = 'none';

  try {
    const res = await apiFetch('/auth/me');
    if (!res.success) throw new Error(res.error || 'Failed to load');
    _renderAccountDetail(res.user, res.limits);
  } catch (e) {
    document.getElementById('acctDetailLoading').textContent = '⚠ ' + e.message;
  }
}

function closeAccountDetail() {
  document.getElementById('accountDetailOverlay')?.classList.add('hidden');
}

function _renderAccountDetail(user, limits) {
  const tier   = user.tier  || 'free';
  const used   = user.search_count_this_month || 0;
  const max    = limits?.searches_per_month ?? 10;   // null = unlimited
  const pct    = max ? Math.min(100, Math.round((used / max) * 100)) : 0;
  const isPaid = tier !== 'free';

  // Plan labels
  const planNames  = { free: 'Free',     pro: 'Pro',          team: 'Team'    };
  const planPrices = { free: '$0 / month', pro: '$29 / month', team: '$79 / month' };
  const badgeColors = {
    free: 'background:rgba(255,255,255,.08);color:var(--text2)',
    pro:  'background:linear-gradient(135deg,#e3c55a,#c9a227);color:#1a1200',
    team: 'background:linear-gradient(135deg,#b080e0,#7a4f9a);color:#fff',
  };
  const barColors = {
    free: 'linear-gradient(90deg,#4facfe,#2563eb)',
    pro:  'linear-gradient(90deg,#e3c55a,#c9a227)',
    team: 'linear-gradient(90deg,#b080e0,#7a4f9a)',
  };

  document.getElementById('acctPlanName').textContent  = planNames[tier]  || tier;
  document.getElementById('acctPlanPrice').textContent = planPrices[tier] || '';
  document.getElementById('acctPlanBadge').style.cssText += `;${badgeColors[tier] || ''}`;
  document.getElementById('acctPlanBadge').textContent = (planNames[tier] || tier).toUpperCase();

  document.getElementById('acctSearchUsed').textContent  = used;
  document.getElementById('acctSearchLimit').textContent = max === null ? '∞' : max;
  document.getElementById('acctUsageBar').style.width    = max === null ? '0%' : pct + '%';
  document.getElementById('acctUsageBar').style.background = barColors[tier] || barColors.free;
  document.getElementById('acctResetDate').textContent   = (user.search_reset_date || '').slice(0,10) || 'Next month';

  // Stripe badge
  const stripeRow = document.getElementById('acctStripeRow');
  if (user.stripe_subscription_id) {
    stripeRow.style.display = 'flex';
    document.getElementById('acctStripeId').textContent =
      'sub: ' + (user.stripe_subscription_id || '').slice(0, 24) + '…';
  } else {
    stripeRow.style.display = 'none';
  }

  // CTA buttons
  document.getElementById('acctBtnUpgradePro').style.display  = tier === 'free' ? '' : 'none';
  document.getElementById('acctBtnUpgradeTeam').style.display = tier === 'pro'  ? '' : 'none';
  document.getElementById('acctBtnPortal').style.display      = isPaid            ? '' : 'none';
  const teamManageBtn = document.getElementById('acctBtnTeamManage');
  if (teamManageBtn) teamManageBtn.style.display = (tier === 'team') ? '' : 'none';

  // Security grid
  document.getElementById('acctDetailEmail').textContent  = user.email || '';
  const statusEl = document.getElementById('acctDetailStatus');
  statusEl.textContent = user.active !== false ? '✓ Active' : '✗ Inactive';
  statusEl.style.color = user.active !== false ? '#56d3a0' : 'var(--danger)';
  document.getElementById('acctDetailJoined').textContent = (user.created_at || '').slice(0,10) || '—';

  document.getElementById('acctDetailLoading').style.display  = 'none';
  document.getElementById('acctDetailContent').style.display  = '';
}

// ── County Registry ──────────────────────────────────────────────────────

let _countySearchTimer = null;

/** Debounced search of the county registry — called on oninput */
function searchCountyRegistry(q) {
  clearTimeout(_countySearchTimer);
  const resultsEl = document.getElementById('countyResults');
  const selectedEl = document.getElementById('countySelected');
  if (!q || q.trim().length < 2) {
    if (resultsEl) resultsEl.style.display = 'none';
    return;
  }
  _countySearchTimer = setTimeout(async () => {
    try {
      const res = await apiFetch('/county-registry?q=' + encodeURIComponent(q.trim()));
      if (!res.success || !resultsEl) return;
      const counties = res.counties || [];
      if (!counties.length) {
        resultsEl.style.display = '';
        resultsEl.innerHTML = '<div style="padding:6px 8px;font-size:11px;color:var(--text3)">No counties found. Try a different search term.</div>';
        return;
      }
      resultsEl.style.display = '';
      resultsEl.innerHTML = counties.slice(0, 12).map(c => `
        <div onclick="applyCountyConfig('${escHtml(c.fips)}')"
          style="padding:5px 8px;border-radius:5px;cursor:pointer;font-size:12px;display:flex;justify-content:space-between;align-items:center"
          onmouseover="this.style.background='rgba(79,172,254,.1)'" onmouseout="this.style.background=''"
        >
          <span>${escHtml(c.name)}</span>
          <span style="font-size:10px;color:var(--text3);margin-left:8px">${escHtml(c.portal_type)}</span>
        </div>
      `).join('');
    } catch(e) {
      console.warn('County registry search failed', e);
    }
  }, 280);
}

/** Fetch a county's full config and apply portal URL + ArcGIS URL to the Settings form */
async function applyCountyConfig(fips) {
  try {
    const res = await apiFetch('/county-registry/' + fips);
    if (!res.success || !res.county) return;
    const c = res.county;

    // Fill portal URL
    const portalEl = document.getElementById('cfgUrl');
    if (portalEl && c.portal_url) portalEl.value = c.portal_url;

    // Fill ArcGIS URL (expand the section automatically)
    const arcgisEl = document.getElementById('arcgisUrl');
    if (arcgisEl && c.arcgis_url) {
      arcgisEl.value = c.arcgis_url;
      // Auto-expand ArcGIS section so the user can see it filled in
      const section = document.getElementById('arcgisSection');
      const chevron = document.getElementById('arcgisSectionChevron');
      if (section && section.style.display === 'none') {
        section.style.display = 'block';
        if (chevron) chevron.style.transform = 'rotate(180deg)';
      }
      // Pre-fill field dropdowns with the registry's known fields
      if (c.arcgis_fields) {
        _populateArcgisUI({ arcgis_url: c.arcgis_url, arcgis_fields: c.arcgis_fields, arcgis_is_default: false });
      }
    }

    // Show confirmation
    const resultsEl  = document.getElementById('countyResults');
    const selectedEl = document.getElementById('countySelected');
    if (resultsEl)  resultsEl.style.display  = 'none';
    if (selectedEl) {
      selectedEl.style.display = '';
      selectedEl.innerHTML = `✓ ${escHtml(c.name)} applied`
        + (c.notes ? ` &nbsp;<span style="font-weight:400;color:var(--text3);font-size:10px">${escHtml(c.notes)}</span>` : '');
    }
    const searchEl = document.getElementById('countySearch');
    if (searchEl) searchEl.value = '';

    showToast(`✓ ${c.name} config loaded — review URLs below then click Connect`, 'success');
  } catch(e) {
    showToast('Failed to load county config: ' + e.message, 'error');
  }
}

// ── Intercept apiFetch to handle 401/403 upgrade responses ───────────────────
const _originalApiFetch = apiFetch;
async function apiFetch(path, method = 'GET', body = null, opts = {}) {
  const fetchOpts = { method, headers: { 'Content-Type': 'application/json' }, credentials: 'include' };
  if (body) fetchOpts.body = JSON.stringify(body);
  if (opts.signal) fetchOpts.signal = opts.signal;
  const res = await fetch(API + path, fetchOpts);

  // Safely parse the response — guard against HTML error pages
  let data;
  const ct = (res.headers.get('content-type') || '').toLowerCase();
  if (ct.includes('application/json')) {
    try { data = await res.json(); }
    catch (_) { data = { success: false, error: `Invalid JSON from ${path} (HTTP ${res.status})` }; }
  } else {
    // Server returned HTML (error page, redirect, etc.) — don't try to parse as JSON
    const snippet = (await res.text()).slice(0, 120);
    console.warn(`[apiFetch] ${path} returned non-JSON (${res.status}):`, snippet);
    data = { success: false, error: `Server error (HTTP ${res.status})` };
  }

  // Surface auth/upgrade errors before caller sees them (production only)
  if (!res.ok && !_isLocalMode()) {
    if (data.auth_required) { showAuthModal('login'); }
    else if (data.upgrade_required) { handleUpgradeRequired(data); }
  }
  return data;
}

// ── Init on page load ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initSaasAuth();
  _checkUpgradeSuccess();
  _applyProFeatureLocks();
  // Show onboarding wizard if no portal URL is configured yet
  setTimeout(checkOnboarding, 1500);
});

// ─────────────────────────────────────────────────────────────────────────────
// PRO FEATURE GATING — UI locks and Stripe Checkout launch
// ─────────────────────────────────────────────────────────────────────────────

/** Check ?upgraded=1 query param set by /upgrade-success redirect */
function _checkUpgradeSuccess() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('upgraded') === '1') {
    showToast('🎉 Welcome to Pro! Your account has been upgraded.', 'success');
    // Remove param from URL without reloading
    history.replaceState(null, '', window.location.pathname);
    // Refresh session to get new tier
    initSaasAuth();
  }
}

/**
 * Guard a pro-only action.
 * If user has pro → call fn(). Otherwise show auth/upgrade modal.
 * @param {string} featureName  - Display name for the feature
 * @param {string} featureKey   - Key from UPGRADE_MESSAGES ('ocr', 'dxf_export', etc.)
 * @param {Function} fn         - The action to run if allowed
 */
function requirePro(featureName, featureKey, fn) {
  if (_isLocalMode()) { fn(); return; }   // No paywall on LAN
  if (!_saasUser) {
    showAuthModal('login');
    showToast('Sign in to use ' + featureName, 'warn');
    return;
  }
  if (!_hasPro()) {
    const msgs = {
      ocr:        'OCR text extraction is a Pro feature. Upgrade to extract text from scanned deeds.',
      dxf_export: 'DXF boundary export requires a Pro subscription.',
      adjoiners:  'Adjoiner auto-discovery requires a Pro subscription.',
      parcel_map: 'Live parcel maps require a Pro subscription.',
      chain:      'Chain of title tracing requires a Pro subscription.',
    };
    const msg = msgs[featureKey] || 'This feature requires a Pro subscription.';
    showUpgradeModal(featureName, msg);
    return;
  }
  fn();
}

/** Apply visual pro-locks to buttons that require upgrade */
function _applyProFeatureLocks() {
  // Re-run whenever tier changes
  const isPro = _hasPro();

  // DXF Generate button
  const dxfBtn = document.getElementById('btnGenerateDxf');
  if (dxfBtn) {
    if (!isPro) {
      dxfBtn.setAttribute('data-original-onclick', dxfBtn.getAttribute('onclick') || 'doGenerateDxf()');
      dxfBtn.setAttribute('onclick', "requirePro('DXF Export','dxf_export',doGenerateDxf)");
      if (!dxfBtn.querySelector('.pro-lock-icon')) {
        dxfBtn.innerHTML = '<span class="btn-icon">🔒</span> Generate & Save DXF <span class="pro-badge-inline">PRO</span>';
      }
    } else {
      // Restore if they just upgraded
      const orig = dxfBtn.getAttribute('data-original-onclick');
      if (orig) { dxfBtn.setAttribute('onclick', orig); dxfBtn.removeAttribute('data-original-onclick'); }
      if (!dxfBtn.querySelector('.btn-icon')) {
        dxfBtn.innerHTML = '<span class="btn-icon">💾</span> Generate & Save DXF';
      }
    }
  }

  // Adjoiner Discovery button
  const adjBtn = document.getElementById('btnDiscoverAdjoiners');
  if (adjBtn && !isPro) {
    adjBtn.setAttribute('onclick', "requirePro('Adjoiner Discovery','adjoiners',runAdjoinerDiscovery)");
  } else if (adjBtn && isPro) {
    adjBtn.setAttribute('onclick', 'runAdjoinerDiscovery()');
  }

  // ArcGIS Spatial button
  const arcBtn = document.getElementById('btnArcgisSpatial');
  if (arcBtn && !isPro) {
    arcBtn.setAttribute('onclick', "requirePro('ArcGIS Spatial Discovery','adjoiners',runArcgisSpatialDiscovery)");
  } else if (arcBtn && isPro) {
    arcBtn.setAttribute('onclick', 'runArcgisSpatialDiscovery()');
  }

  // Bulk Search button (Step 5)
  const bulkBtn = document.querySelector('[onclick="bulkSearchAdjoiners()"]');
  if (bulkBtn && !isPro) {
    bulkBtn.setAttribute('onclick', "requirePro('Bulk Adjoiner Search','adjoiners',bulkSearchAdjoiners)");
  } else if (bulkBtn && isPro) {
    bulkBtn.setAttribute('onclick', 'bulkSearchAdjoiners()');
  }

  // Add PRO badge to relevant section headings
  const s6Tabs = document.getElementById('s6Tabs');
  if (s6Tabs && !isPro) {
    const dxfTab = [...s6Tabs.querySelectorAll('.tab-btn')].find(b => b.textContent.includes('DXF'));
    if (dxfTab && !dxfTab.querySelector('.pro-badge-inline')) {
      dxfTab.innerHTML += ' <span class="pro-badge-inline">PRO</span>';
    }
  }
}

/**
 * Launch Stripe Checkout for upgrading.
 * Called from the upgrade modal "Join Waitlist" area when Stripe is configured.
 */
async function launchStripeCheckout(tier = 'pro') {
  if (!_saasUser) { showAuthModal('login'); return; }

  const btn = event?.target;
  if (btn) { btn.textContent = 'Redirecting to Stripe…'; btn.disabled = true; }

  try {
    const res = await fetch('/api/stripe/checkout', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tier }),
    });
    const data = await res.json();
    if (data.success && data.checkout_url) {
      window.location.href = data.checkout_url;
    } else {
      showToast(data.error || 'Checkout failed. Please try again.', 'error');
      if (btn) { btn.textContent = 'Upgrade to Pro'; btn.disabled = false; }
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
    if (btn) { btn.textContent = 'Upgrade to Pro'; btn.disabled = false; }
  }
}

// _applyProFeatureLocks is called inside _updateSaasBadge (see definition above)

// ─────────────────────────────────────────────────────────────────────────────
// ONBOARDING WIZARD
// ─────────────────────────────────────────────────────────────────────────────

let _obSelectedCounty = null;
let _obSearchTimer    = null;

/** Check if onboarding is needed and show if so. */
function checkOnboarding() {
  if (sessionStorage.getItem('ob_done')) return;
  fetch(API + '/config', { credentials: 'include' })
    .then(r => r.json())
    .then(d => {
      if (!d.url || d.url.length < 5) {
        setTimeout(() => document.getElementById('onboardingOverlay')?.classList.remove('hidden'), 800);
      }
    }).catch(() => {});
}

function closeOnboarding() {
  document.getElementById('onboardingOverlay')?.classList.add('hidden');
  sessionStorage.setItem('ob_done', '1');
}

function obGoStep(n) {
  for (let i = 1; i <= 4; i++) {
    const step = document.getElementById('obStep' + i);
    const tab  = document.getElementById('obTab'  + i);
    if (!step || !tab) continue;
    step.style.display = i === n ? '' : 'none';
    tab.style.color = i <= n ? 'var(--accent)' : 'var(--text3)';
    tab.style.borderBottom = i === n ? '2px solid var(--accent)' : '2px solid transparent';
  }
}

function obSearchCounty(q) {
  clearTimeout(_obSearchTimer);
  const resultsEl  = document.getElementById('obCountyResults');
  const noCountyEl = document.getElementById('obNoCounty');
  if (!q || q.trim().length < 2) { if (resultsEl) resultsEl.style.display = 'none'; return; }
  _obSearchTimer = setTimeout(async () => {
    try {
      const res = await apiFetch('/county-registry?q=' + encodeURIComponent(q.trim()));
      if (!res.success || !resultsEl) return;
      const counties = res.counties || [];
      if (noCountyEl) noCountyEl.style.display = counties.length ? 'none' : '';
      if (!counties.length) { resultsEl.style.display = 'none'; return; }
      resultsEl.style.display = '';
      resultsEl.innerHTML = counties.slice(0, 10).map(c => `
        <div onclick="obSelectCounty('${escHtml(c.fips)}')"
          style="padding:7px 10px;cursor:pointer;font-size:12px;display:flex;justify-content:space-between"
          onmouseover="this.style.background='rgba(79,172,254,.1)'" onmouseout="this.style.background=''">
          <span>${escHtml(c.name)}</span><span style="font-size:10px;color:var(--text3)">${escHtml(c.portal_type)}</span>
        </div>`).join('');
    } catch(e) { console.warn('ob search', e); }
  }, 280);
}

async function obSelectCounty(fips) {
  const res = await apiFetch('/county-registry/' + fips);
  if (!res.success) return;
  const c = res.county;
  _obSelectedCounty = c;
  document.getElementById('obCountyResults').style.display = 'none';
  const selEl = document.getElementById('obCountySelected');
  if (selEl) { selEl.style.display = ''; selEl.textContent = '✓ ' + c.name + ' selected'; }
  if (c.portal_url) {
    const pEl = document.getElementById('obPortalUrl');
    if (pEl) pEl.value = c.portal_url;
    const lbl = document.getElementById('obCountyUrlLabel');
    if (lbl) lbl.textContent = c.portal_url;
    const row = document.getElementById('obCountyUrlRow');
    if (row) row.style.display = '';
  }
  const nextBtn = document.getElementById('obNextCounty');
  if (nextBtn) nextBtn.disabled = false;
}

async function obTestConnection() {
  const url  = (document.getElementById('obPortalUrl')?.value || '').trim();
  const user = (document.getElementById('obUsername')?.value || '').trim();
  const pass = (document.getElementById('obPassword')?.value || '').trim();
  const resultEl = document.getElementById('obConnectResult');
  const btn = document.getElementById('btnObTest');
  if (!url) { showToast('Enter a portal URL first', 'warn'); return; }
  if (btn) { btn.textContent = 'Testing…'; btn.disabled = true; }
  try {
    const res = await apiFetch('/test-connection', 'POST', { url, username: user, password: pass });
    if (resultEl) {
      resultEl.style.display = '';
      resultEl.style.background = res.success ? 'rgba(86,211,160,.12)' : 'rgba(255,107,107,.12)';
      resultEl.style.color = res.success ? '#56d3a0' : 'var(--danger)';
      resultEl.textContent = res.success ? '✓ Connection successful!' : '✗ ' + (res.error || 'Connection failed');
    }
  } catch(e) {
    if (resultEl) { resultEl.style.display=''; resultEl.textContent='Error: '+e.message; }
  } finally {
    if (btn) { btn.textContent='Test Connection'; btn.disabled=false; }
  }
}

async function obSaveAndFinish() {
  const url  = (document.getElementById('obPortalUrl')?.value || '').trim();
  const user = (document.getElementById('obUsername')?.value || '').trim();
  const pass = (document.getElementById('obPassword')?.value || '').trim();
  const btn  = document.getElementById('btnObSave');
  if (btn) { btn.textContent = 'Saving…'; btn.disabled = true; }
  try {
    const payload = { url, username: user, password: pass };
    if (_obSelectedCounty?.arcgis_url) {
      payload.arcgis_url    = _obSelectedCounty.arcgis_url;
      payload.arcgis_fields = _obSelectedCounty.arcgis_fields || {};
    }
    const res = await apiFetch('/config', 'POST', payload);
    if (res.success) {
      obGoStep(4);
    } else {
      showToast('Save failed: ' + (res.error || 'Unknown'), 'error');
      if (btn) { btn.textContent='Save & Finish'; btn.disabled=false; }
    }
  } catch(e) {
    showToast('Error: '+e.message, 'error');
    if (btn) { btn.textContent='Save & Finish'; btn.disabled=false; }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// ADMIN PANEL
// ─────────────────────────────────────────────────────────────────────────────

let _adminPassword = '';

async function _loadSearchHistory() {
  try {
    const res = await fetch('/auth/history', { credentials: 'include' });
    if (res.ok) {
      const data = await res.json();
      if (data.success) _searchHistory = data.history || [];
    }
  } catch(e) {}
}

function showAdminPanel() {
  document.getElementById('adminOverlay')?.classList.remove('hidden');
  document.getElementById('adminAuthGate').style.display = '';
  document.getElementById('adminDashboard').style.display = 'none';
  document.getElementById('adminAuthError').style.display = 'none';
  document.getElementById('adminPwdInput').value = '';
  setTimeout(() => document.getElementById('adminPwdInput')?.focus(), 100);
}
function closeAdminPanel() {
  document.getElementById('adminOverlay')?.classList.add('hidden');
  _adminPassword = '';
}

async function adminLogin() {
  const pwd   = document.getElementById('adminPwdInput')?.value || '';
  const errEl = document.getElementById('adminAuthError');
  try {
    const res = await apiFetch('/admin/auth', 'POST', { password: pwd });
    if (res.success) {
      _adminPassword = pwd;
      document.getElementById('adminAuthGate').style.display = 'none';
      document.getElementById('adminDashboard').style.display = '';
      errEl.style.display = 'none';
      _renderAdminStats(res.stats);
      await _loadAdminUsers();
    } else {
      errEl.style.display = '';
      errEl.textContent = res.error || 'Invalid password';
    }
  } catch(e) {
    errEl.style.display = '';
    errEl.textContent = 'Error: ' + e.message;
  }
}

function _renderAdminStats(stats) {
  if (!stats) return;
  const grid = document.getElementById('adminStatsGrid');
  if (!grid) return;
  const items = [
    { label: 'Total Users', value: stats.total_users  || 0, color: 'var(--accent)' },
    { label: 'Active',      value: stats.active_users || 0, color: '#56d3a0' },
    { label: 'MRR',         value: '$' + (stats.mrr_usd || 0), color: '#e3c55a' },
    { label: 'Pro / Team',  value: (stats.by_tier?.pro||0) + ' / ' + (stats.by_tier?.team||0), color: '#b080e0' },
  ];
  grid.innerHTML = items.map(it => `
    <div style="background:var(--surface2);border-radius:8px;padding:12px 10px;text-align:center">
      <div style="font-size:22px;font-weight:700;color:${it.color}">${it.value}</div>
      <div style="font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.4px;margin-top:2px">${it.label}</div>
    </div>`).join('');
}

async function _loadAdminUsers() {
  const res = await apiFetch('/admin/users?password=' + encodeURIComponent(_adminPassword));
  if (!res.success) return;
  _renderAdminStats(res.stats);
  const tbody = document.getElementById('adminUserRows');
  if (!tbody) return;
  const tierColors = { free:'var(--text3)', pro:'#e3c55a', team:'#b080e0' };
  tbody.innerHTML = (res.users || []).map(u => `
    <tr style="border-bottom:1px solid var(--border)">
      <td style="padding:6px 8px">${escHtml(u.email)}</td>
      <td style="padding:6px 8px">
        <span style="font-weight:700;color:${tierColors[u.tier]||'var(--text2)'}">${u.tier}</span>
        <select onchange="adminChangeTier('${u.id}',this.value)" style="margin-left:6px;font-size:10px;background:var(--surface2);color:var(--text1);border:1px solid var(--border);border-radius:3px;padding:1px">
          <option value="">change…</option>
          <option value="free">→ Free</option>
          <option value="pro">→ Pro</option>
          <option value="team">→ Team</option>
        </select>
      </td>
      <td style="padding:6px 8px;text-align:center">${u.searches_used}${u.search_limit===null?' / ∞':' / '+u.search_limit}</td>
      <td style="padding:6px 8px;font-size:10px;font-family:monospace;color:var(--text3)">${u.stripe_cus_id ? u.stripe_cus_id.slice(0,18)+'…' : '—'}</td>
      <td style="padding:6px 8px;font-size:10px;color:var(--text3)">${(u.created_at||'').slice(0,10)}</td>
      <td style="padding:6px 8px;white-space:nowrap">
        <button onclick="adminResetSearches('${u.id}')" class="btn btn-outline btn-sm" style="font-size:10px;padding:2px 5px">↺ Reset</button>
        <button onclick="adminToggleActive('${u.id}',${!u.active})" class="btn btn-outline btn-sm" style="font-size:10px;padding:2px 5px;margin-left:3px">${u.active?'Disable':'Enable'}</button>
      </td>
    </tr>`).join('');
}

async function adminChangeTier(userId, tier) {
  if (!tier) return;
  await apiFetch('/admin/users/'+userId, 'PATCH', { password: _adminPassword, tier });
  showToast('Tier updated → ' + tier, 'success');
  await _loadAdminUsers();
}
async function adminResetSearches(userId) {
  await apiFetch('/admin/users/'+userId, 'PATCH', { password: _adminPassword, reset_searches: true });
  showToast('Search counter reset', 'success');
  await _loadAdminUsers();
}
async function adminToggleActive(userId, active) {
  await apiFetch('/admin/users/'+userId, 'PATCH', { password: _adminPassword, active });
  showToast(active ? 'Account enabled' : 'Account disabled', 'success');
  await _loadAdminUsers();
}

// ─────────────────────────────────────────────────────────────────────────────
// CONFIG EXPORT / IMPORT
// ─────────────────────────────────────────────────────────────────────────────

function exportConfig() {
  const profile = state.researchSession?.profile || 'default';
  const a = document.createElement('a');
  a.href = API + '/config/export?profile=' + encodeURIComponent(profile);
  a.download = 'deed_config.json';
  a.click();
  showToast('County config saved as deed_config.json', 'success');
}

function showConfigImport() {
  document.getElementById('configImportOverlay')?.classList.remove('hidden');
  document.getElementById('configImportJson').value = '';
  document.getElementById('configImportResult').style.display = 'none';
}
function closeConfigImport() {
  document.getElementById('configImportOverlay')?.classList.add('hidden');
}

async function doConfigImport() {
  const raw = (document.getElementById('configImportJson')?.value || '').trim();
  const resultEl = document.getElementById('configImportResult');
  let cfg;
  try { cfg = JSON.parse(raw); } catch {
    resultEl.style.display=''; resultEl.style.background='rgba(255,107,107,.12)';
    resultEl.style.color='var(--danger)'; resultEl.textContent='✗ Invalid JSON'; return;
  }
  const profile = state.researchSession?.profile || 'default';
  const res = await apiFetch('/config/import', 'POST', { profile, config: cfg });
  resultEl.style.display = '';
  if (res.success) {
    resultEl.style.background='rgba(86,211,160,.12)'; resultEl.style.color='#56d3a0';
    resultEl.textContent = '✓ ' + (res.message || 'Imported');
    setTimeout(closeConfigImport, 1200);
    showToast('Config imported — reload Settings to confirm', 'success');
  } else {
    resultEl.style.background='rgba(255,107,107,.12)'; resultEl.style.color='var(--danger)';
    resultEl.textContent = '✗ ' + (res.error || 'Import failed');
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// FORGOT / RESET PASSWORD
// ─────────────────────────────────────────────────────────────────────────────

function showForgotPassword() {
  document.getElementById('authForgotForm').style.display = '';
  document.getElementById('authForgotRow').style.display  = 'none';
  document.getElementById('forgotEmail').focus();
}
function hideForgotPassword() {
  document.getElementById('authForgotForm').style.display = 'none';
  document.getElementById('authForgotRow').style.display  = '';
  document.getElementById('forgotResult').style.display   = 'none';
}

async function doForgotPassword() {
  const email    = (document.getElementById('forgotEmail')?.value || '').trim();
  const resultEl = document.getElementById('forgotResult');
  if (!email) { showToast('Enter your email address', 'warn'); return; }

  const btn = document.querySelector('#authForgotForm .btn-accent');
  if (btn) { btn.textContent = 'Sending…'; btn.disabled = true; }

  const res = await apiFetch('/auth/forgot-password', 'POST', { email });

  if (resultEl) {
    resultEl.style.display    = '';
    resultEl.style.background = 'rgba(86,211,160,.12)';
    resultEl.style.color      = '#56d3a0';
    resultEl.textContent      = '✓ ' + (res.message || 'Check your email for a reset link.');
  }
  if (btn) { btn.textContent = 'Send Reset Link'; btn.disabled = false; }
}

async function doResetPassword() {
  const newPwd     = document.getElementById('resetNewPassword')?.value || '';
  const confirmPwd = document.getElementById('resetConfirmPassword')?.value || '';
  const resultEl   = document.getElementById('resetResult');
  const token      = new URLSearchParams(window.location.search).get('token') || '';

  if (!newPwd || newPwd.length < 8) {
    showResult(resultEl, false, 'Password must be at least 8 characters.');
    return;
  }
  if (newPwd !== confirmPwd) {
    showResult(resultEl, false, 'Passwords do not match.');
    return;
  }
  if (!token) {
    showResult(resultEl, false, 'No reset token found in URL. Request a new reset link.');
    return;
  }

  const btn = document.querySelector('#authResetForm .btn-primary');
  if (btn) { btn.textContent = 'Saving…'; btn.disabled = true; }

  const res = await apiFetch('/auth/reset-password', 'POST', { token, password: newPwd });

  if (res.success) {
    showResult(resultEl, true, '✓ ' + res.message);
    showToast('Password updated! Signing you in…', 'success');
    // Remove token from URL then trigger login
    history.replaceState(null, '', '/');
    setTimeout(() => {
      document.getElementById('authResetForm').style.display = 'none';
      document.getElementById('authForgotRow').style.display = '';
      document.getElementById('authEmail')?.focus();
    }, 1500);
  } else {
    showResult(resultEl, false, '✗ ' + (res.error || 'Reset failed.'));
  }
  if (btn) { btn.textContent = 'Set New Password'; btn.disabled = false; }
}

function showResult(el, success, msg) {
  if (!el) return;
  el.style.display    = '';
  el.style.background = success ? 'rgba(86,211,160,.12)' : 'rgba(255,107,107,.12)';
  el.style.color      = success ? '#56d3a0' : 'var(--danger)';
  el.textContent      = msg;
}

/** On page load, if ?token= in URL, open auth modal with reset form showing */
function _checkResetToken() {
  const token = new URLSearchParams(window.location.search).get('token');
  if (!token) return;
  // Show auth modal with the reset form
  showAuthModal('login');
  setTimeout(() => {
    document.getElementById('authResetForm').style.display  = '';
    document.getElementById('authForgotRow').style.display  = 'none';
    document.getElementById('btnAuthSubmit').style.display  = 'none';
    document.getElementById('authForgotForm').style.display = 'none';
    // Hide tabs — this is the reset flow only
    const tabs = document.querySelectorAll('.auth-tab');
    tabs.forEach(t => t.style.display = 'none');
  }, 50);
}

document.addEventListener('DOMContentLoaded', _checkResetToken);

// ─────────────────────────────────────────────────────────────────────────────
// USAGE BAR — update when account dropdown opens
// ─────────────────────────────────────────────────────────────────────────────

async function _refreshUsageBar() {
  try {
    const res = await apiFetch('/auth/usage');
    if (!res.success) return;
    const searchesEl = document.getElementById('acctMenuSearches');
    const limitEl    = document.getElementById('acctMenuLimit');
    const barEl      = document.getElementById('acctMenuUsageBar');
    const rowEl      = document.getElementById('acctMenuUsageRow');

    if (searchesEl) searchesEl.textContent = res.used ?? 0;
    if (limitEl)    limitEl.textContent    = res.limit === null ? '∞' : (res.limit ?? 10);

    if (barEl && rowEl) {
      if (res.limit === null) {
        // Unlimited — hide the bar row for Pro/Team
        rowEl.style.display = 'none';
      } else {
        rowEl.style.display = '';
        const pct = Math.min(100, Math.round((res.used / res.limit) * 100));
        barEl.style.width      = pct + '%';
        // Color: green <60%, amber 60-80%, red ≥80%
        barEl.style.background = pct >= 80 ? '#da3633' : pct >= 60 ? '#c9a227' : 'var(--accent)';
      }
    }
  } catch(e) { /* silently ignore — usage bar is best-effort */ }
}

// Patch openAccountMenu (if it exists) to refresh usage bar on open
const _origOpenAccountMenu = window.openAccountMenu;
window.openAccountMenu = function(...args) {
  if (_origOpenAccountMenu) _origOpenAccountMenu.apply(this, args);
  _refreshUsageBar();
};

// ─────────────────────────────────────────────────────────────────────────────
// SEARCH HISTORY — recent queries panel
// ─────────────────────────────────────────────────────────────────────────────

let _searchHistory = [];



/** Render a compact history dropdown below the name search input. */
function _renderSearchHistory(inputEl, containerEl) {
  if (!containerEl || !_searchHistory.length) return;
  containerEl.innerHTML = _searchHistory.slice(0, 8).map(h => `
    <div onclick="_fillSearch(${JSON.stringify(h.query)})"
      style="padding:6px 10px;cursor:pointer;font-size:12px;display:flex;justify-content:space-between;align-items:center"
      onmouseover="this.style.background='rgba(79,172,254,.1)'" onmouseout="this.style.background=''">
      <span style="color:var(--text1)">${escHtml(h.query)}</span>
      <span style="font-size:10px;color:var(--text3)">${h.count ?? ''} results · ${(h.at||'').slice(0,10)}</span>
    </div>`).join('');
  containerEl.style.display = '';
}

function _fillSearch(query) {
  const nameInput = document.getElementById('s2SearchName');
  if (nameInput) {
    nameInput.value = query;
    nameInput.dispatchEvent(new Event('input', { bubbles: true }));
  }
  const hist = document.getElementById('searchHistoryDropdown');
  if (hist) hist.style.display = 'none';
}

let _historyLoadAttempted = false;
function _showSearchHistory() {
  if (!_searchHistory.length) {
    // Try loading once if not yet populated — guard against infinite loop
    if (!_historyLoadAttempted && typeof _loadSearchHistory === 'function') {
      _historyLoadAttempted = true;
      _loadSearchHistory().then(() => _showSearchHistory());
    }
    return;
  }
  const containerEl = document.getElementById('searchHistoryDropdown');
  const inputEl     = document.getElementById('s2SearchName');
  _renderSearchHistory(inputEl, containerEl);
}

let _historyHideTimer = null;
function _hideSearchHistoryDelay() {
  // Small delay so clicks inside the dropdown register before it disappears
  _historyHideTimer = setTimeout(() => {
    const hist = document.getElementById('searchHistoryDropdown');
    if (hist) hist.style.display = 'none';
  }, 200);
}

// ─────────────────────────────────────────────────────────────────────────────
// FRIENDLY QUOTA MODAL — intercept upgrade_required on search
// ─────────────────────────────────────────────────────────────────────────────

function handleUpgradeRequired(data) {
  const msg     = data.error || 'This feature requires a Pro subscription.';
  const isQuota = msg.includes('searches');

  // Populate the upgrade modal's message element (id may be upgradeMsg or upgradeModalMsg)
  const msgEl = document.getElementById('upgradeMsg') || document.getElementById('upgradeModalMsg');
  if (msgEl) msgEl.textContent = msg;

  if (isQuota) {
    const ovl = document.getElementById('upgradeOverlay');
    if (ovl) ovl.classList.remove('hidden');
  } else {
    showToast('⚡ ' + msg, 'warn');
    setTimeout(() => {
      const ovl = document.getElementById('upgradeOverlay');
      if (ovl) ovl.classList.remove('hidden');
    }, 600);
  }
}

// Patch switchAuthTab to show/hide forgot link
const _origSwitchAuthTab = window.switchAuthTab;
window.switchAuthTab = function(tab) {
  if (_origSwitchAuthTab) _origSwitchAuthTab.apply(this, arguments);
  const forgotRow = document.getElementById('authForgotRow');
  if (forgotRow) forgotRow.style.display = tab === 'login' ? '' : 'none';
};

// ─────────────────────────────────────────────────────────────────────────────
// TEAM MANAGEMENT
// ─────────────────────────────────────────────────────────────────────────────

let _teamData = null;

async function showTeamPanel() {
  document.getElementById('teamOverlay')?.classList.remove('hidden');
  await _loadTeamData();
}
function closeTeamPanel() {
  document.getElementById('teamOverlay')?.classList.add('hidden');
}

async function _loadTeamData() {
  const listEl = document.getElementById('teamMemberList');
  if (listEl) listEl.innerHTML = '<div style="font-size:12px;color:var(--text3);text-align:center;padding:20px 0">Loading…</div>';

  try {
    const res = await apiFetch('/api/team/members');
    if (!res.success) {
      if (listEl) listEl.innerHTML = `<div style="color:var(--danger);font-size:12px;padding:8px">${escHtml(res.error||'Error loading team.')}</div>`;
      return;
    }
    _teamData = res;
    _renderTeamPanel(res);
  } catch(e) {
    if (listEl) listEl.innerHTML = '<div style="color:var(--danger);font-size:12px;padding:8px">Failed to load team data.</div>';
  }
}

function _renderTeamPanel(data) {
  // Update seat counter
  const su = document.getElementById('teamSeatsUsed');
  const sm = document.getElementById('teamSeatsMax');
  if (su) su.textContent = data.seats_used ?? '—';
  if (sm) sm.textContent = data.seats_max ?? 5;

  const isOwner = data.role === 'owner';
  const isTeamMember = data.role === 'member';

  // Show/hide invite section (owners only)
  const inviteSection = document.getElementById('teamInviteSection');
  if (inviteSection) inviteSection.style.display = isOwner ? '' : 'none';

  // Show/hide leave section (members only, not owner)
  const leaveSection = document.getElementById('teamLeaveSection');
  if (leaveSection) leaveSection.style.display = isTeamMember ? '' : 'none';

  // Member list
  const listEl = document.getElementById('teamMemberList');
  if (!listEl) return;

  if (!data.members || !data.members.length) {
    listEl.innerHTML = '<div style="font-size:12px;color:var(--text3);text-align:center;padding:20px 0">No team members yet. Invite someone above.</div>';
    return;
  }

  listEl.innerHTML = data.members.map(m => `
    <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">
      <div style="flex:1">
        <div style="font-size:12px;font-weight:600;color:var(--text1)">${escHtml(m.email)}</div>
        <div style="font-size:10px;color:var(--text3)">
          ${m.role === 'owner' ? '👑 Owner' : '👤 Member'}
          ${m.joined_at ? ' · Joined ' + (m.joined_at||'').slice(0,10) : ''}
          ${m.active === false ? ' · <span style="color:var(--danger)">Inactive</span>' : ''}
        </div>
      </div>
      ${isOwner && m.role !== 'owner' ? `
        <button class="btn btn-outline btn-sm" style="color:var(--danger);border-color:var(--danger);font-size:11px"
          onclick="doRemoveTeamMember('${m.id}', '${escHtml(m.email)}')">Remove</button>
      ` : ''}
    </div>
  `).join('');
}

async function doTeamInvite() {
  const email    = (document.getElementById('teamInviteEmail')?.value || '').trim();
  const resultEl = document.getElementById('teamInviteResult');
  if (!email) { showToast('Enter an email address', 'warn'); return; }

  const btn = document.getElementById('btnTeamInvite');
  if (btn) { btn.textContent = 'Sending…'; btn.disabled = true; }

  const res = await apiFetch('/api/team/invite', 'POST', { email });

  if (resultEl) {
    resultEl.style.display    = '';
    resultEl.style.background = res.success ? 'rgba(86,211,160,.12)' : 'rgba(255,107,107,.12)';
    resultEl.style.color      = res.success ? '#56d3a0' : 'var(--danger)';
    resultEl.textContent      = res.success ? `✓ ${res.message}` : `✗ ${res.error || 'Invite failed.'}`;
  }
  if (btn) { btn.textContent = 'Send Invite'; btn.disabled = false; }
  if (res.success) {
    document.getElementById('teamInviteEmail').value = '';
    await _loadTeamData();
  }
}

async function doRemoveTeamMember(memberId, email) {
  if (!confirm(`Remove ${email} from the team? They will be downgraded to Free.`)) return;
  const res = await apiFetch(`/api/team/members/${memberId}`, 'DELETE');
  if (res.success) {
    showToast(`✓ ${res.message}`, 'success');
    await _loadTeamData();
  } else {
    showToast(`✗ ${res.error || 'Remove failed.'}`, 'error');
  }
}

async function doLeaveTeam() {
  if (!confirm('Leave this team? You will be downgraded to Free immediately.')) return;
  const res = await apiFetch('/api/team/leave', 'POST');
  if (res.success) {
    showToast('✓ ' + res.message, 'success');
    closeTeamPanel();
    initSaasAuth();  // re-check auth state / update badge
  } else {
    showToast('✗ ' + (res.error || 'Failed to leave team.'), 'error');
  }
}

/** Auto-detect /team/join?token= URL on page load and accept invitation */
async function _checkTeamJoinToken() {
  if (!window.location.pathname.includes('team/join')) return;
  const token = new URLSearchParams(window.location.search).get('token');
  if (!token) return;

  // User must be logged in first — require auth
  const user = await apiFetch('/auth/me');
  if (!user?.success) {
    // Store token and show login
    sessionStorage.setItem('pendingTeamToken', token);
    showAuthModal('login');
    showToast('Log in to accept your team invitation', 'info');
    return;
  }

  // Accept the invite
  const res = await apiFetch('/api/team/join', 'POST', { token });
  history.replaceState(null, '', '/');  // clean URL
  if (res.success) {
    showToast('🎉 ' + res.message + ' You now have Team access!', 'success');
    initSaasAuth();
  } else {
    showToast('✗ ' + (res.error || 'Could not join team.'), 'error');
  }
}

document.addEventListener('DOMContentLoaded', _checkTeamJoinToken);

/** Show Team button in Settings footer when user has team tier */
function _applyTeamVisibility(userTier, userRole) {
  const teamBtn = document.getElementById('btnSettingsTeam');
  if (teamBtn) teamBtn.style.display = (userTier === 'team') ? '' : 'none';
}

// ─────────────────────────────────────────────────────────────────────────────
// ?plan= LANDING PAGE -> APP FLOW
// Handles users arriving from deedplathelper.netlify.app pricing buttons.
// ?plan=pro or ?plan=team -> show auth modal, then upgrade modal after login.
// ─────────────────────────────────────────────────────────────────────────────

(function _handlePlanParam() {
  const plan = new URLSearchParams(window.location.search).get('plan');
  if (!plan || !['pro', 'team'].includes(plan)) return;
  history.replaceState(null, '', window.location.pathname);
  const planNames = { pro: 'Pro - $29/mo', team: 'Team - $79/mo' };
  const planMsgs  = {
    pro:  'Unlock unlimited searches, OCR, live parcel maps, adjoiner discovery, and DXF export.',
    team: 'Everything in Pro plus 5 seats, shared session library, and priority support.',
  };
  function _showUpgradeForPlan() {
    if (typeof showUpgradeModal === 'function') {
      showUpgradeModal(planNames[plan], planMsgs[plan]);
    }
  }
  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
      if (_saasUser) {
        _showUpgradeForPlan();
      } else {
        showAuthModal('register');
        showToast('Create a free account to start your ' + (plan === 'pro' ? 'Pro' : 'Team') + ' trial', 'info');
        const poll = setInterval(() => {
          if (_saasUser) { clearInterval(poll); setTimeout(_showUpgradeForPlan, 500); }
        }, 500);
        setTimeout(() => clearInterval(poll), 300000);
      }
    }, 900);
  });
})();

// ─────────────────────────────────────────────────────────────────────────────
// AI CHAT PANEL (Nova)
// ─────────────────────────────────────────────────────────────────────────────

let _aiAvailable = false;
let _aiChatOpen   = false;
let _aiHistory    = [];  // { role: 'user'|'ai', text }

/** Check AI backend status on startup */
async function _initAiChat() {
  try {
    const res = await fetch(API + '/ai/status', { credentials: 'include' });
    const data = await res.json();
    _aiAvailable = data.available === true;

    const bubble = document.getElementById('aiChatBubble');
    if (!bubble) return;
    if (!_aiAvailable) {
      bubble.style.display = 'none';
      return;
    }
    bubble.style.display = 'flex';

    // Update subtitle with model info
    const sub = document.getElementById('aiChatSubtitle');
    if (sub && data.ollama?.model) {
      sub.textContent = data.ollama.available
        ? `${data.ollama.model} · Online`
        : 'ML & Graph · Ollama offline';
    }
  } catch (e) {
    // AI not available — hide bubble quietly
    const bubble = document.getElementById('aiChatBubble');
    if (bubble) bubble.style.display = 'none';
  }
}

/** Toggle the chat panel open/closed */
function toggleAiChat() {
  const panel  = document.getElementById('aiChatPanel');
  const bubble = document.getElementById('aiChatBubble');
  if (!panel) return;

  _aiChatOpen = !_aiChatOpen;
  panel.classList.toggle('hidden', !_aiChatOpen);
  if (bubble) bubble.style.display = _aiChatOpen ? 'none' : 'flex';

  if (_aiChatOpen) {
    setTimeout(() => document.getElementById('aiChatInput')?.focus(), 100);
    // Hide notification badge
    const badge = document.getElementById('aiBubbleBadge');
    if (badge) badge.classList.add('hidden');
  }
}

/** Clear chat history */
function clearAiChat() {
  _aiHistory = [];
  const msgs = document.getElementById('aiChatMessages');
  if (msgs) msgs.innerHTML = `
    <div class="ai-message ai-message-ai">
      <div class="ai-msg-content">
        👋 Chat cleared. Ask me anything about your surveys!
      </div>
    </div>`;
}

/** Add a message to the chat UI */
function _addAiMessage(role, text) {
  const msgs = document.getElementById('aiChatMessages');
  if (!msgs) return;

  const div = document.createElement('div');
  div.className = 'ai-message ' + (role === 'user' ? 'ai-message-user' : 'ai-message-ai');

  // Simple markdown-like formatting for AI responses
  let formatted = text;
  if (role === 'ai') {
    formatted = formatted
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code style="background:rgba(0,0,0,.3);padding:1px 4px;border-radius:3px;font-size:12px;font-family:monospace">$1</code>')
      .replace(/\n/g, '<br>');
  } else {
    formatted = escHtml(text);
  }

  div.innerHTML = `<div class="ai-msg-content">${formatted}</div>`;
  msgs.appendChild(div);

  // Auto-scroll to bottom
  msgs.scrollTop = msgs.scrollHeight;

  _aiHistory.push({ role, text });
}

/** Show/hide typing indicator */
function _setAiTyping(show) {
  const el = document.getElementById('aiTyping');
  if (el) el.classList.toggle('hidden', !show);
}

/** Send user message to AI */
async function sendAiMessage() {
  const input = document.getElementById('aiChatInput');
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  _addAiMessage('user', text);
  _setAiTyping(true);

  try {
    // Try LLM first, fall back to simpler endpoints
    const res = await fetch(API + '/ai/ask', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: text,
        context: _buildAiContext()
      })
    });
    const data = await res.json();
    _setAiTyping(false);

    if (data.available === false) {
      _addAiMessage('ai', '⚠️ Ollama is offline — LLM chat requires `ollama serve` running on this machine. Try the quick action buttons above for ML features that work without Ollama.');
    } else if (data.answer) {
      _addAiMessage('ai', data.answer);
    } else if (data.error) {
      _addAiMessage('ai', '❌ ' + data.error);
    } else {
      _addAiMessage('ai', '🤔 I didn\'t get a response. Try rephrasing your question.');
    }
  } catch (e) {
    _setAiTyping(false);
    _addAiMessage('ai', '❌ Connection error: ' + e.message);
  }
}

/** Build context string from current session state */
function _buildAiContext() {
  const parts = [];
  if (typeof state !== 'undefined' && state.researchSession) {
    const s = state.researchSession;
    if (s.clientName) parts.push('Client: ' + s.clientName);
    if (s.jobType)    parts.push('Job type: ' + s.jobType);
    if (s.jobNumber)  parts.push('Job #' + s.jobNumber);
  }
  return parts.join(', ') || 'General surveying question';
}

/** Quick action buttons */
async function aiQuickAction(action) {
  switch (action) {
    case 'predict': await _aiPredictJob(); break;
    case 'graph':   await _aiGraphStats(); break;
    case 'anomaly': await _aiAnomalyCheck(); break;
  }
}

async function _aiPredictJob() {
  const s = typeof state !== 'undefined' ? state.researchSession : null;
  const jobType    = s?.jobType    || 'BDY';
  const clientName = s?.clientName || '';

  _addAiMessage('user', '📊 Predict complexity for ' + (clientName || 'current job'));
  _setAiTyping(true);

  try {
    const res = await fetch(API + '/ai/predict', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_type: jobType, client_name: clientName })
    });
    const data = await res.json();
    _setAiTyping(false);

    if (!data.available) {
      _addAiMessage('ai', '⚠️ ML prediction not available. Models may need training.');
      return;
    }

    let msg = '📊 **Job Complexity Prediction**\n\n';
    if (data.prediction) {
      const p = data.prediction;
      if (p.adjoiners)  msg += `🏘️ Predicted adjoiners: **${p.adjoiners.predicted}** (±${p.adjoiners.std_dev || '?'})\n`;
      if (p.cabinet)    msg += `📁 Predicted cabinet: **${p.cabinet.predicted}** (${Math.round((p.cabinet.confidence||0)*100)}% conf)\n`;
      if (p.complexity) msg += `⚡ Complexity: **${p.complexity}**\n`;
    }
    _addAiMessage('ai', msg);
  } catch (e) {
    _setAiTyping(false);
    _addAiMessage('ai', '❌ Prediction error: ' + e.message);
  }
}

async function _aiGraphStats() {
  _addAiMessage('user', '🕸️ Show knowledge graph stats');
  _setAiTyping(true);

  try {
    const res = await fetch(API + '/ai/graph/stats', { credentials: 'include' });
    const data = await res.json();
    _setAiTyping(false);

    if (!data.available) {
      _addAiMessage('ai', '⚠️ Knowledge graph is not loaded.');
      return;
    }

    let msg = '🕸️ **Knowledge Graph**\n\n';
    msg += `📍 Nodes: **${data.stats?.nodes?.toLocaleString() || '?'}**\n`;
    msg += `🔗 Edges: **${data.stats?.edges?.toLocaleString() || '?'}**\n`;
    if (data.stats?.by_type) {
      const bt = data.stats.by_type;
      if (bt.owner)    msg += `👤 Owners: **${bt.owner}**\n`;
      if (bt.property) msg += `🏠 Properties: **${bt.property}**\n`;
      if (bt.survey)   msg += `📐 Surveys: **${bt.survey}**\n`;
    }
    _addAiMessage('ai', msg);
  } catch (e) {
    _setAiTyping(false);
    _addAiMessage('ai', '❌ Graph error: ' + e.message);
  }
}

async function _aiAnomalyCheck() {
  const s = typeof state !== 'undefined' ? state.researchSession : null;
  const clientName = s?.clientName || '';
  const jobType    = s?.jobType || 'BDY';
  const adjoiners  = s?.subjects?.filter(x => x.type === 'adjoiner')?.length || 0;

  _addAiMessage('user', '⚠️ Run QA anomaly check');
  _setAiTyping(true);

  try {
    const res = await fetch(API + '/ai/analyze', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        job_type: jobType,
        client_name: clientName,
        adjoiner_count: adjoiners,
        has_deed: s?.subjects?.[0]?.deed_saved || false,
        has_plat: s?.subjects?.[0]?.plat_saved || false,
      })
    });
    const data = await res.json();
    _setAiTyping(false);

    if (!data.available) {
      _addAiMessage('ai', '⚠️ Anomaly detection not available.');
      return;
    }

    let msg = '⚠️ **QA Anomaly Check**\n\n';
    if (data.anomalies && data.anomalies.length > 0) {
      data.anomalies.forEach(a => {
        const icon = a.severity === 'high' ? '🔴' : a.severity === 'medium' ? '🟡' : '🟢';
        msg += `${icon} ${a.message}\n`;
      });
    } else {
      msg += '✅ No anomalies detected — looking good!';
    }
    if (data.score != null) {
      msg += `\n\n📈 Completeness score: **${Math.round(data.score * 100)}%**`;
    }
    _addAiMessage('ai', msg);
  } catch (e) {
    _setAiTyping(false);
    _addAiMessage('ai', '❌ Anomaly check error: ' + e.message);
  }
}

// Init on page load
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(_initAiChat, 1500);  // Delayed init to not block startup
});

// ─────────────────────────────────────────────────────────────────────────────
// AUTO-PREDICT ON SESSION START
// ─────────────────────────────────────────────────────────────────────────────
/**
 * Fire ML prediction right after a session is created.
 * Called from startSession() after state.researchSession is set.
 * Gracefully does nothing if AI is unavailable.
 */
async function _autoPredict(jobType, clientName) {
  try {
    const res = await fetch(API + '/ai/predict', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_type: jobType, client_name: clientName }),
    });
    const data = await res.json();
    if (data.available === false || !data.prediction) return;

    // _showPrediction is defined earlier in the AI Insights section
    if (typeof _showPrediction === 'function') {
      _showPrediction(data.prediction);
    }
  } catch (_) { /* AI offline — silent */ }
}

// ─────────────────────────────────────────────────────────────────────────────
// KG ADJOINER SUGGESTIONS (Step 4)
// ─────────────────────────────────────────────────────────────────────────────
let _kgSuggestionsCache = null;  // cache per session

/**
 * Load KG adjoiners for the current client from the knowledge graph.
 * Called when the user enters Step 4.
 */
async function loadKgAdjoiners() {
  const rs = state.researchSession;
  const panel = document.getElementById('s4KgSuggestions');
  const grid  = document.getElementById('s4KgGrid');
  if (!rs || !panel || !grid) return;

  // Don't re-fetch if already cached for this session
  if (_kgSuggestionsCache !== null) {
    _renderKgSuggestions(_kgSuggestionsCache);
    return;
  }

  const clientName = encodeURIComponent(rs.client_name);
  try {
    const res = await fetch(`${API}/ai/graph/adjoiners/${clientName}`, {
      credentials: 'include',
    });
    const data = await res.json();

    if (!data.available && data.available !== undefined) return; // KG offline
    const adjoiners = data.adjoiners || [];
    _kgSuggestionsCache = adjoiners;
    _renderKgSuggestions(adjoiners);
  } catch (_) { /* KG unavailable — silent */ }
}

function _renderKgSuggestions(adjoiners) {
  const panel = document.getElementById('s4KgSuggestions');
  const grid  = document.getElementById('s4KgGrid');
  if (!panel || !grid) return;

  // Filter out names already on the board
  const rs = state.researchSession;
  const onBoard = new Set((rs?.subjects || []).map(s => s.name.toLowerCase()));
  const fresh = adjoiners.filter(a => !onBoard.has(a.name?.toLowerCase()));

  if (!fresh.length) return;  // nothing new to suggest

  panel.classList.remove('hidden');
  grid.innerHTML = fresh.map(a => `
    <button class="ai-quick-btn" onclick="addFoundAdjoiner(${JSON.stringify(a.name)}).then(() => { this.disabled=true; this.style.opacity='0.4'; })"
      title="Job #${a.job_discovered || '?'}">
      + ${escHtml(a.name || '')}
    </button>
  `).join('');
}

async function kgAddAllSuggestions() {
  const rs = state.researchSession;
  const panel = document.getElementById('s4KgSuggestions');
  if (!_kgSuggestionsCache || !rs) return;

  const onBoard = new Set(rs.subjects.map(s => s.name.toLowerCase()));
  const toAdd = _kgSuggestionsCache.filter(a => !onBoard.has(a.name?.toLowerCase()));
  if (!toAdd.length) { showToast('All suggestions already on board', 'info'); return; }

  let added = 0;
  for (const a of toAdd) {
    const ok = await addFoundAdjoiner(a.name);
    if (ok) added++;
  }
  if (panel) panel.classList.add('hidden');
  showToast(`✓ Added ${added} AI suggestion${added !== 1 ? 's' : ''} to board`, 'success');
}

// ─────────────────────────────────────────────────────────────────────────────
// LEGAL DESCRIPTION SUMMARIZER
// ─────────────────────────────────────────────────────────────────────────────
async function summarizeLegalDesc() {
  const descEl = document.getElementById('propDescText');
  if (!descEl) { showToast('No legal description loaded', 'warn'); return; }

  const text = descEl.textContent.trim();
  if (text.length < 20) { showToast('Legal description too short to summarize', 'warn'); return; }

  const summaryEl = document.getElementById('legalDescSummary');
  if (!summaryEl) return;

  summaryEl.classList.remove('hidden');
  summaryEl.textContent = '✨ Summarizing with Nova AI…';

  try {
    const res = await fetch(API + '/ai/summarize', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();

    if (data.available === false) {
      summaryEl.textContent = '⚠️ Ollama is offline — start "ollama serve" to enable AI summarization.';
      return;
    }
    if (data.summary) {
      summaryEl.textContent = data.summary;
    } else if (data.error) {
      summaryEl.textContent = '❌ ' + data.error;
    }
  } catch (e) {
    summaryEl.textContent = '❌ Connection error: ' + e.message;
  }
}

// ── Hook goToStep to fire KG lookup when entering Step 4 ──────────────────
// Patch the existing goToStep function to add a side-effect for step 4.
const _origGoToStep = window.goToStep;
if (typeof _origGoToStep === 'function') {
  window.goToStep = function(n) {
    _origGoToStep(n);
    if (n === 4) {
      _kgSuggestionsCache = null;  // reset so we re-fetch for each job
      loadKgAdjoiners();
    }
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// AI SIMILAR DESCRIPTIONS  (embeddings-based, inline results)
// ─────────────────────────────────────────────────────────────────────────────
/**
 * Find similar legal descriptions using the AI embeddings index (/api/ai/similar).
 * Falls back gracefully if sentence-transformers / chromadb aren't installed.
 * Results appear inline below the property description.
 */
async function aiSimilarDescriptions() {
  const descEl  = document.getElementById('propDescText');
  const resultEl = document.getElementById('aiSimilarResults');
  if (!descEl || !resultEl) return;

  const text = descEl.textContent.trim();
  if (text.length < 20) {
    showToast('Extract the property description first', 'warn');
    return;
  }

  resultEl.classList.remove('hidden');
  resultEl.innerHTML = `<div style="padding:10px;font-size:12px;color:var(--text3);display:flex;align-items:center;gap:8px">
    <span class="spinner" style="width:14px;height:14px"></span> Searching AI embeddings…
  </div>`;

  try {
    const res = await fetch(API + '/ai/similar', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: text, top_k: 8 }),
    });
    const data = await res.json();

    if (data.available === false) {
      resultEl.innerHTML = `
        <div style="padding:10px 12px;background:rgba(176,128,224,.06);border:1px solid rgba(176,128,224,.2);border-radius:8px;font-size:12px;color:var(--text3)">
          🔗 <strong style="color:#b080e0">AI Similar</strong> requires
          <code style="background:rgba(0,0,0,.3);padding:1px 4px;border-radius:3px">sentence-transformers</code>
          and <code style="background:rgba(0,0,0,.3);padding:1px 4px;border-radius:3px">chromadb</code>.
          Install them in the .venv to enable semantic search.
          <br><br>
          <button class="btn btn-outline btn-sm" onclick="findSimilarDescriptions()"
            style="font-size:11px">📋 Use TF-IDF Similar instead</button>
        </div>`;
      return;
    }

    if (!data.results || data.results.length === 0) {
      resultEl.innerHTML = `
        <div style="padding:10px 12px;border:1px solid var(--border);border-radius:8px;font-size:12px;color:var(--text3)">
          🔍 No similar descriptions found in the embeddings index.
          The index may be empty — descriptions get indexed when deeds are opened.
        </div>`;
      return;
    }

    // Build results
    let html = `
      <div style="border:1px solid rgba(176,128,224,.2);border-radius:8px;overflow:hidden">
        <div style="padding:10px 14px;background:rgba(176,128,224,.06);border-bottom:1px solid rgba(176,128,224,.15);display:flex;align-items:center;gap:8px">
          <span style="font-size:13px">🔗</span>
          <div>
            <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:#b080e0">AI Similar Descriptions</div>
            <div style="font-size:10px;color:var(--text3)">${data.count} semantically similar parcels · embeddings index</div>
          </div>
          <button class="btn btn-outline btn-sm" onclick="document.getElementById('aiSimilarResults').classList.add('hidden')"
            style="margin-left:auto;font-size:11px;padding:2px 8px">✕</button>
        </div>
        <div style="padding:8px;display:flex;flex-direction:column;gap:4px;max-height:280px;overflow-y:auto">`;

    for (const r of data.results) {
      const dist  = r.distance ?? 1;
      // Convert cosine distance to similarity % (lower distance = more similar)
      const sim   = Math.max(0, Math.min(100, Math.round((1 - dist) * 100)));
      const simColor = sim >= 70 ? '#56d3a0' : sim >= 40 ? '#e3c55a' : '#b080e0';
      const onBoard  = state.researchSession?.subjects?.some(
        s => s.name?.toLowerCase() === (r.metadata?.owner || '').toLowerCase()
      );

      html += `
        <div style="padding:8px 10px;border-radius:6px;background:rgba(0,0,0,.2);border:1px solid rgba(255,255,255,.04);display:flex;align-items:center;gap:10px">
          <div style="font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:800;color:${simColor};min-width:36px;text-align:center">${sim}%</div>
          <div style="flex:1;min-width:0">
            <div style="font-size:12px;font-weight:600;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
              ${escHtml(r.metadata?.owner || r.id || 'Unknown')}
            </div>
            <div style="font-size:10px;color:var(--text3)">
              ${r.metadata?.upc ? `UPC ${escHtml(r.metadata.upc)}` : ''}
              ${r.metadata?.plat ? ` · ${escHtml(r.metadata.plat)}` : ''}
            </div>
          </div>
          ${!onBoard && r.metadata?.owner ? `
          <button class="btn btn-outline btn-sm" style="font-size:10px;padding:2px 8px;white-space:nowrap"
            onclick="addFoundAdjoiner(${JSON.stringify(r.metadata.owner)})">+ Add</button>` : ''}
        </div>`;
    }

    html += `</div></div>`;
    resultEl.innerHTML = html;

  } catch (e) {
    resultEl.innerHTML = `<div style="padding:8px;font-size:12px;color:var(--danger)">❌ ${escHtml(e.message)}</div>`;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// AUTO QA ON DEED SAVE
// ─────────────────────────────────────────────────────────────────────────────
/**
 * Run anomaly detection silently after a deed is saved.
 * Shows a non-blocking toast banner — flags only, no modal.
 * Skips if no session or AI unavailable.
 */
async function _autoQaCheck() {
  const rs = state.researchSession;
  if (!rs) return;

  try {
    const adjoiners = rs.subjects.filter(s => s.type === 'adjoiner');
    const clientSubj = rs.subjects.find(s => s.type === 'client');

    const res = await fetch(API + '/ai/analyze', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        job_type: rs.job_type || 'BDY',
        client_name: rs.client_name || '',
        adjoiners_found: adjoiners.length,
        deed_found: !!clientSubj?.deed_saved,
        plat_found: !!clientSubj?.plat_saved,
        subjects: rs.subjects.map(s => ({
          type: s.type,
          name: s.name,
          deed_saved: !!s.deed_saved,
          plat_saved: !!s.plat_saved,
        })),
      }),
    });
    const data = await res.json();

    if (data.available === false) return;  // AI offline — silent

    const flags = data.flags || [];
    // Only surface warnings/errors (skip info-level)
    const important = flags.filter(f => f.level === 'warning' || f.level === 'error');
    if (!important.length) return;  // clean — no toast needed

    _renderQaFlags(important);
  } catch (_) { /* silent */ }
}

/**
 * Render QA flags as a compact stackable toast-like banner.
 * High-severity flags show in red, warnings in yellow.
 */
function _renderQaFlags(flags) {
  // Find or create a QA banner container
  let banner = document.getElementById('qaFlagBanner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'qaFlagBanner';
    banner.style.cssText = `
      position:fixed; bottom:90px; right:24px; z-index:7500;
      display:flex; flex-direction:column; gap:6px; max-width:320px;
    `;
    document.body.appendChild(banner);
  }

  for (const f of flags) {
    const isError = f.level === 'error';
    const color   = isError ? '#ff7b72' : '#e3c55a';
    const bg      = isError ? 'rgba(255,123,114,.1)' : 'rgba(227,197,90,.08)';
    const border  = isError ? 'rgba(255,123,114,.3)' : 'rgba(227,197,90,.3)';
    const icon    = isError ? '🔴' : '⚠️';

    const item = document.createElement('div');
    item.style.cssText = `
      padding:10px 12px; border-radius:8px; background:${bg};
      border:1px solid ${border}; font-size:12px; color:${color};
      display:flex; align-items:flex-start; gap:8px; line-height:1.5;
      animation:aiMsgIn .25s ease; box-shadow:0 4px 16px rgba(0,0,0,.3);
    `;
    item.innerHTML = `
      <span>${icon}</span>
      <span style="flex:1"><strong>QA:</strong> ${escHtml(f.message)}</span>
      <button onclick="this.parentElement.remove()" style="background:none;border:none;color:${color};cursor:pointer;font-size:14px;padding:0;line-height:1">✕</button>
    `;
    banner.appendChild(item);

    // Auto-dismiss after 12 seconds
    setTimeout(() => item.remove(), 12000);
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// AI INTEGRATION — Wires frontend AI panels to /api/ai/* endpoints
// ═══════════════════════════════════════════════════════════════════════════════

let _aiStatus = null;       // Cached AI subsystem status
let _aiPredCache = {};      // Prediction cache: "BDY|ClientName" -> result
let _kgSuggestions = [];    // Current KG adjoiner suggestions

/**
 * Refresh the AI Insights panel on Step 1.
 * Fetches: index health, research analytics, AI status, and predictions.
 * @param {boolean} force - If true, force a full refresh (not cached).
 */
async function refreshAiInsights(force = false) {
  try {
    // 1. Fetch AI subsystem status
    const statusRes = await fetch(API + '/api/ai/status', { credentials: 'include' });
    if (statusRes.ok) {
      _aiStatus = await statusRes.json();
    }

    // 2. Populate index health metrics from existing endpoints
    const [healthRes, analyticsRes] = await Promise.all([
      fetch(API + '/api/index-health', { credentials: 'include' }).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch(API + '/api/research-analytics', { credentials: 'include' }).then(r => r.ok ? r.json() : null).catch(() => null),
    ]);

    // Index health
    if (healthRes) {
      _setText('aiTotalParcels', _fmtNum(healthRes.total_parcels || 0));
      _setText('aiArcgisPct', (healthRes.arcgis_pct || 0) + '%');
    }

    // Research analytics → conflicts & jobs
    if (analyticsRes) {
      _setText('aiConflictCount', analyticsRes.total_conflicts || '0');
      _setText('aiJobsScanned', analyticsRes.jobs_scanned || '0');
    }

    // 3. Show KG stats in a tooltip-like way
    if (_aiStatus?.knowledge_graph?.available) {
      const kgNodes = _aiStatus.knowledge_graph.nodes || 0;
      const kgEdges = _aiStatus.knowledge_graph.edges || 0;
      if (kgNodes > 0) {
        _setText('aiJobsScanned', _fmtNum(kgNodes));
      }
    }

    // 4. Fetch predictions based on current job type
    const jobType = document.getElementById('setupJobType')?.value || 'BDY';
    const clientName = document.getElementById('setupClient')?.value || '';
    await fetchAiPredictions(jobType, clientName);

    // 5. Show/hide stale warning
    if (healthRes?.is_stale) {
      const warn = document.getElementById('aiStaleWarning');
      const text = document.getElementById('aiStaleText');
      if (warn) warn.classList.remove('hidden');
      if (text) text.textContent = `Index last updated ${healthRes.last_updated || 'unknown'}`;
    }

  } catch (e) {
    console.debug('[ai] refreshAiInsights failed:', e);
  }
}

/**
 * Fetch ML complexity predictions for the current job setup.
 */
async function fetchAiPredictions(jobType = 'BDY', clientName = '') {
  const cacheKey = `${jobType}|${clientName}`;
  if (_aiPredCache[cacheKey]) {
    _renderPredictions(_aiPredCache[cacheKey]);
    return;
  }

  try {
    const res = await fetch(API + '/api/ai/predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ job_type: jobType, client_name: clientName }),
    });
    if (!res.ok) return;
    const data = await res.json();
    if (data.available === false) return;

    _aiPredCache[cacheKey] = data;
    _renderPredictions(data);
  } catch (e) {
    console.debug('[ai] prediction failed:', e);
  }
}

function _renderPredictions(data) {
  const row = document.getElementById('aiPredictionRow');
  if (!row) return;
  row.classList.remove('hidden');

  // Complexity badge
  const complexity = data.complexity || 'moderate';
  const badge = document.getElementById('aiPredBadge');
  const compEl = document.getElementById('aiPredComplexity');
  if (compEl) compEl.textContent = complexity;
  if (badge) {
    badge.className = 'ai-prediction-badge';
    if (complexity === 'high') badge.style.borderColor = 'rgba(255,123,114,.4)';
    else if (complexity === 'low') badge.style.borderColor = 'rgba(86,211,160,.4)';
    else badge.style.borderColor = 'rgba(227,197,90,.4)';
  }

  // Adjoiners
  _setText('aiPredAdjoiners', (data.predicted_adjoiners ?? '—'));
  const rangeEl = document.getElementById('aiPredRange');
  if (rangeEl && data.predicted_adjoiners) {
    const adj = data.predicted_adjoiners;
    rangeEl.textContent = `(±${Math.max(1, Math.round(adj * 0.3))})`;
  }

  // Cabinets
  _setText('aiPredCabinets', data.predicted_cabinet || '—');

  // Confidence
  const conf = data.confidence || 'fallback';
  _setText('aiPredConfidence', conf === 'fallback' ? 'statistical' : conf);
}

/**
 * Fetch KG-based adjoiner suggestions for Step 4.
 * Called when entering Step 4 after a session is active.
 */
async function fetchKgSuggestions() {
  const clientName = state.researchSession?.client_name || '';
  if (!clientName) return;

  try {
    const res = await fetch(API + '/api/ai/graph/adjoiners/' + encodeURIComponent(clientName), {
      credentials: 'include',
    });
    if (!res.ok) return;
    const data = await res.json();

    _kgSuggestions = data.adjoiners || [];
    _renderKgSuggestions();
  } catch (e) {
    console.debug('[ai] KG suggestions failed:', e);
  }
}

function _renderKgSuggestions() {
  const panel = document.getElementById('s4KgSuggestions');
  const grid = document.getElementById('s4KgGrid');
  if (!panel || !grid) return;

  if (!_kgSuggestions.length) {
    panel.classList.add('hidden');
    return;
  }

  // Filter out names already on the research board
  const existing = new Set(
    (state.researchSession?.subjects || []).map(s => s.name?.toLowerCase())
  );
  const novel = _kgSuggestions.filter(a => !existing.has(a.name?.toLowerCase()));

  if (!novel.length) {
    panel.classList.add('hidden');
    return;
  }

  panel.classList.remove('hidden');
  grid.innerHTML = novel.map((a, i) => `
    <button class="kg-suggestion-chip" id="kgChip${i}"
      onclick="kgAddSuggestion(${i})"
      title="From job #${a.job_discovered || '?'}"
      style="background:rgba(121,168,224,.1);border:1px solid rgba(121,168,224,.25);
             color:#a8c8ec;border-radius:20px;padding:5px 14px;font-size:12px;
             cursor:pointer;font-weight:500;transition:all .15s;
             display:inline-flex;align-items:center;gap:6px">
      <span style="font-size:10px;opacity:.6">+</span>
      ${_escHtml(a.name)}
      <span style="font-size:9px;opacity:.5">#${a.job_discovered || '?'}</span>
    </button>
  `).join('');
}

function kgAddSuggestion(idx) {
  const adj = _kgSuggestions[idx];
  if (!adj) return;

  // Add to research session
  if (!state.researchSession) return;
  if (!state.researchSession.subjects) state.researchSession.subjects = [];

  // Avoid duplicates
  const exists = state.researchSession.subjects.some(
    s => s.name?.toLowerCase() === adj.name?.toLowerCase()
  );
  if (exists) {
    showToast(`${adj.name} already on board`, 'warn');
    return;
  }

  state.researchSession.subjects.push({
    name: adj.name,
    type: 'adjoiner',
    source: 'knowledge_graph',
    deed_saved: false,
    plat_saved: false,
  });

  // Visual feedback
  const chip = document.getElementById('kgChip' + idx);
  if (chip) {
    chip.style.background = 'rgba(86,211,160,.15)';
    chip.style.borderColor = 'rgba(86,211,160,.4)';
    chip.style.color = '#56d3a0';
    chip.innerHTML = `✓ ${_escHtml(adj.name)}`;
    chip.disabled = true;
    chip.style.cursor = 'default';
  }

  showToast(`Added ${adj.name} from knowledge graph`, 'success');
}

function kgAddAllSuggestions() {
  for (let i = 0; i < _kgSuggestions.length; i++) {
    kgAddSuggestion(i);
  }
}

// ── Nova AI Chat Panel ───────────────────────────────────────────────────────

let _novaChatOpen = false;
let _novaChatHistory = [];

function toggleNovaChat() {
  _novaChatOpen = !_novaChatOpen;
  const panel = document.getElementById('novaChatPanel');
  if (panel) {
    panel.classList.toggle('hidden', !_novaChatOpen);
    if (_novaChatOpen) {
      document.getElementById('novaChatInput')?.focus();
    }
  }
}

async function sendNovaMessage() {
  const input = document.getElementById('novaChatInput');
  const question = input?.value?.trim();
  if (!question) return;

  input.value = '';

  // Add user message to chat
  _addNovaMessage('user', question);

  // Build context from current session
  let context = '';
  if (state.researchSession) {
    const s = state.researchSession;
    context = `Job #${s.job_number} - ${s.client_name} (${s.job_type})`;
    const subjects = s.subjects || [];
    if (subjects.length) {
      context += `. Subjects: ${subjects.map(sub => sub.name).join(', ')}`;
    }
  }

  // Show typing indicator
  const typingId = _addNovaMessage('assistant', '⋯ thinking...');

  try {
    const res = await fetch(API + '/api/ai/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ question, context }),
    });
    const data = await res.json();

    // Remove typing indicator and show response
    document.getElementById(typingId)?.remove();

    if (data.answer) {
      _addNovaMessage('assistant', data.answer);
    } else if (data.available === false) {
      _addNovaMessage('assistant', '🔌 Ollama is not running. Start it with `ollama serve` to enable AI chat.');
    } else {
      _addNovaMessage('assistant', '❌ ' + (data.error || 'Unknown error'));
    }
  } catch (e) {
    document.getElementById(typingId)?.remove();
    _addNovaMessage('assistant', '❌ Network error: ' + e.message);
  }
}

function _addNovaMessage(role, text) {
  const container = document.getElementById('novaChatMessages');
  if (!container) return '';

  const id = 'nova_' + Date.now() + '_' + Math.random().toString(36).slice(2, 5);
  const isUser = role === 'user';

  const msg = document.createElement('div');
  msg.id = id;
  msg.style.cssText = `
    padding: 10px 14px; border-radius: 12px; font-size: 13px; line-height: 1.6;
    max-width: 85%; word-wrap: break-word; animation: aiMsgIn .2s ease;
    ${isUser
      ? 'background: rgba(79,172,254,.15); border: 1px solid rgba(79,172,254,.25); color: #a8d4ff; margin-left: auto;'
      : 'background: rgba(255,255,255,.05); border: 1px solid rgba(255,255,255,.08); color: var(--text2);'
    }
  `;
  msg.innerHTML = `
    <div style="font-size:10px;font-weight:600;color:${isUser ? 'var(--accent)' : '#b080e0'};margin-bottom:4px;text-transform:uppercase;letter-spacing:.4px">
      ${isUser ? '🧑 You' : '🤖 Nova'}
    </div>
    <div>${_escHtml(text)}</div>
  `;

  container.appendChild(msg);
  container.scrollTop = container.scrollHeight;

  _novaChatHistory.push({ role, text });
  return id;
}

// ── AI Helpers ───────────────────────────────────────────────────────────────

function _setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function _fmtNum(n) {
  return typeof n === 'number' ? n.toLocaleString() : String(n);
}

function _escHtml(s) {
  if (typeof escHtml === 'function') return escHtml(s);
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ── Hook into existing app lifecycle ─────────────────────────────────────────

// Refresh AI insights when Step 1 loads or job type changes
const _origGoToStep = window.goToStep;
if (typeof _origGoToStep === 'function') {
  window.goToStep = function(stepNum) {
    _origGoToStep.apply(this, arguments);
    if (stepNum === 1) {
      setTimeout(() => refreshAiInsights(), 500);
    }
    if (stepNum === 4) {
      setTimeout(() => fetchKgSuggestions(), 300);
    }
  };
}

// Watch job type dropdown for prediction updates
document.addEventListener('DOMContentLoaded', () => {
  const jobTypeSelect = document.getElementById('setupJobType');
  if (jobTypeSelect) {
    jobTypeSelect.addEventListener('change', () => {
      const jt = jobTypeSelect.value;
      const cn = document.getElementById('setupClient')?.value || '';
      fetchAiPredictions(jt, cn);
    });
  }

  // Auto-refresh AI insights after a short delay (let main app init first)
  setTimeout(() => refreshAiInsights(), 2000);
});

// ── CSS keyframe for message animation (inject once) ─────────────────────────
(function() {
  if (document.getElementById('aiAnimStyles')) return;
  const style = document.createElement('style');
  style.id = 'aiAnimStyles';
  style.textContent = `
    @keyframes aiMsgIn {
      from { opacity: 0; transform: translateY(8px); }
      to   { opacity: 1; transform: translateY(0); }
    }
  `;
  document.head.appendChild(style);
})();

// ── Functions referenced by HTML onclick handlers ────────────────────────────

/** Toggle the AI chat panel (called by the floating bubble button). */
function toggleAiChat() {
  const panel = document.getElementById('aiChatPanel');
  if (!panel) return;
  const isHidden = panel.classList.contains('hidden');
  panel.classList.toggle('hidden', !isHidden);
  if (isHidden) {
    document.getElementById('aiChatInput')?.focus();
  }
}

/** Send a message in the AI chat panel. */
async function sendAiMessage() {
  const input = document.getElementById('aiChatInput');
  const question = input?.value?.trim();
  if (!question) return;
  input.value = '';

  // Add user message
  _appendChatMsg('user', question);

  // Show typing
  const typing = document.getElementById('aiTyping');
  if (typing) typing.classList.remove('hidden');

  // Build context from current session
  let context = '';
  if (state.researchSession) {
    const s = state.researchSession;
    context = `Job #${s.job_number} - ${s.client_name} (${s.job_type})`;
    const subjects = s.subjects || [];
    if (subjects.length) {
      context += `. Subjects: ${subjects.map(sub => sub.name).join(', ')}`;
    }
  }

  try {
    const res = await fetch(API + '/api/ai/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ question, context }),
    });
    const data = await res.json();
    if (typing) typing.classList.add('hidden');

    if (data.answer) {
      _appendChatMsg('ai', data.answer);
    } else if (data.available === false) {
      _appendChatMsg('ai', '🔌 Ollama is not running. Start it with `ollama serve` to enable AI chat.');
    } else {
      _appendChatMsg('ai', '❌ ' + (data.error || 'No response'));
    }
  } catch (e) {
    if (typing) typing.classList.add('hidden');
    _appendChatMsg('ai', '❌ Network error: ' + e.message);
  }
}

/** Clear all messages in the AI chat. */
function clearAiChat() {
  const container = document.getElementById('aiChatMessages');
  if (container) {
    container.innerHTML = `
      <div class="ai-message ai-message-ai">
        <div class="ai-msg-content">
          👋 I'm Nova, your AI surveying assistant. Ask me about legal descriptions, adjoiners, predictions, or anything survey-related.
        </div>
      </div>`;
  }
}

/** Quick action buttons in the chat panel. */
async function aiQuickAction(action) {
  const typing = document.getElementById('aiTyping');

  if (action === 'predict') {
    const jt = document.getElementById('setupJobType')?.value || 'BDY';
    const cn = state.researchSession?.client_name || '';
    _appendChatMsg('user', `📊 Predict complexity for ${jt} job${cn ? ' — ' + cn : ''}`);
    if (typing) typing.classList.remove('hidden');
    try {
      const res = await fetch(API + '/api/ai/predict', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ job_type: jt, client_name: cn }),
      });
      const data = await res.json();
      if (typing) typing.classList.add('hidden');
      if (data.available === false) {
        _appendChatMsg('ai', '❌ ML predictor not available');
      } else {
        _appendChatMsg('ai',
          `Complexity: ${data.complexity || '?'}\n` +
          `Expected adjoiners: ${data.predicted_adjoiners ?? '?'}\n` +
          `Likely cabinet: ${data.predicted_cabinet || '?'}\n` +
          `Model: ${data.confidence || 'fallback'}`
        );
      }
    } catch (e) {
      if (typing) typing.classList.add('hidden');
      _appendChatMsg('ai', '❌ ' + e.message);
    }

  } else if (action === 'graph') {
    _appendChatMsg('user', '🕸️ Show knowledge graph stats');
    if (typing) typing.classList.remove('hidden');
    try {
      const res = await fetch(API + '/api/ai/graph/stats', { credentials: 'include' });
      const data = await res.json();
      if (typing) typing.classList.add('hidden');
      if (data.available === false) {
        _appendChatMsg('ai', '❌ Knowledge graph not available');
      } else {
        const mc = (data.most_connected || []).slice(0, 5)
          .map(m => `  • ${m.name} (${m.adjoiners} adjoiners)`).join('\n');
        _appendChatMsg('ai',
          `Nodes: ${_fmtNum(data.total_nodes || 0)}\n` +
          `Edges: ${_fmtNum(data.total_edges || 0)}\n` +
          `Persons: ${data.node_types?.person || 0}\n` +
          `Jobs: ${data.node_types?.job || 0}\n` +
          (mc ? `\nMost connected:\n${mc}` : '')
        );
      }
    } catch (e) {
      if (typing) typing.classList.add('hidden');
      _appendChatMsg('ai', '❌ ' + e.message);
    }

  } else if (action === 'anomaly') {
    const cn = state.researchSession?.client_name || '';
    const jt = state.researchSession?.job_type || 'BDY';
    _appendChatMsg('user', '⚠️ Run QA check' + (cn ? ' for ' + cn : ''));
    if (typing) typing.classList.remove('hidden');
    try {
      const subjects = state.researchSession?.subjects || [];
      const adjCount = subjects.filter(s => s.type === 'adjoiner').length;
      const res = await fetch(API + '/api/ai/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          job_type: jt,
          client_name: cn,
          adjoiners_found: adjCount,
          deed_found: subjects.some(s => s.deed_saved),
          plat_found: subjects.some(s => s.plat_saved),
          subjects: subjects,
        }),
      });
      const data = await res.json();
      if (typing) typing.classList.add('hidden');
      if (data.available === false) {
        _appendChatMsg('ai', '❌ Anomaly detector not available');
      } else if (!data.flags?.length) {
        _appendChatMsg('ai', '✅ No anomalies detected — research looks good!');
      } else {
        const flags = data.flags.map(f => `${f.level === 'error' ? '🔴' : '⚠️'} ${f.message}`).join('\n');
        _appendChatMsg('ai', `Found ${data.count} issue(s):\n${flags}`);
      }
    } catch (e) {
      if (typing) typing.classList.add('hidden');
      _appendChatMsg('ai', '❌ ' + e.message);
    }
  }
}

/** Append a message to the AI chat panel. */
function _appendChatMsg(role, text) {
  const container = document.getElementById('aiChatMessages');
  if (!container) return;
  const isAi = role === 'ai';
  const div = document.createElement('div');
  div.className = `ai-message ai-message-${role}`;
  div.style.animation = 'aiMsgIn .2s ease';
  div.innerHTML = `<div class="ai-msg-content">${isAi ? '🤖 ' : ''}${_escHtml(text).replace(/\n/g, '<br>')}</div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}


// ─────────────────────────────────────────────────────────────────────────────
// KG CLIENT NAME AUTOCOMPLETE
// ─────────────────────────────────────────────────────────────────────────────
async function _kgClientSuggest(query) {
  const dropdown = document.getElementById('kgClientDropdown');
  if (!dropdown) return;
  if (!query || query.length < 2) { dropdown.classList.add('hidden'); dropdown.innerHTML = ''; return; }
  try {
    const res = await fetch(`${API}/ai/graph/search`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, limit: 8 }),
    });
    const data = await res.json();
    if (!data.results || data.results.length === 0 || data.available === false) { dropdown.classList.add('hidden'); return; }
    const clients = data.results.filter(r => r.jobs > 0);
    if (!clients.length) { dropdown.classList.add('hidden'); return; }
    dropdown.innerHTML = clients.map(r => `
      <div onclick="document.getElementById('setupClient').value=${JSON.stringify(r.name)};document.getElementById('kgClientDropdown').classList.add('hidden')"
        style="padding:8px 12px;cursor:pointer;font-size:12px;color:var(--text);border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;transition:background .1s"
        onmouseenter="this.style.background='rgba(121,168,224,.08)'" onmouseleave="this.style.background='none'">
        <span>${escHtml(r.name)}</span>
        <span style="font-size:10px;color:var(--text3)">${r.jobs} job${r.jobs !== 1 ? 's' : ''}${r.adjoiners ? ` · ${r.adjoiners} adj` : ''}</span>
      </div>`).join('');
    dropdown.classList.remove('hidden');
  } catch (_) { dropdown.classList.add('hidden'); }
}

// ─────────────────────────────────────────────────────────────────────────────
// KG POPULATE FROM ARCHIVE
// ─────────────────────────────────────────────────────────────────────────────
async function kgPopulateFromArchive() {
  const btn = document.getElementById('btnKgPopulate');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Building…'; }
  try {
    const res = await fetch(`${API}/ai/graph/populate`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
    });
    const data = await res.json();
    if (data.available === false) { showToast('Knowledge graph is offline', 'warn'); return; }
    if (!data.success) { showToast('KG populate failed: ' + (data.error || 'Unknown'), 'error'); return; }
    const { total_nodes, total_edges, persons_added, jobs_added, adjacencies_added } = data;
    showToast(`KG Updated — ${(total_nodes||0).toLocaleString()} nodes, ${(total_edges||0).toLocaleString()} edges · +${persons_added||0} people, +${jobs_added||0} jobs, +${adjacencies_added||0} adjacencies`, 'success');
    refreshAiInsights();
  } catch (e) {
    showToast('KG populate error: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '🕸️ Update KG'; }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// AUTO-INDEX DEED DESCRIPTIONS INTO EMBEDDINGS
// ─────────────────────────────────────────────────────────────────────────────
async function _indexDescriptionEmbedding(desc, docNo) {
  const text = desc?.legal_description || desc?.full_text || '';
  if (!text || text.length < 30) return;
  const rs = state.researchSession;
  const metadata = {
    doc_no: docNo || '',
    owner: rs?.client_name || '',
    upc: rs?.client_upc || '',
    trs: (desc.trs_refs || []).join(', '),
    job_number: rs?.job_number || '',
    desc_type: desc.desc_type || '',
  };
  await fetch(`${API}/ai/embed`, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: `deed_${docNo || Date.now()}`, text, metadata }),
  });
}
