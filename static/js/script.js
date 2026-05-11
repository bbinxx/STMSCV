'use strict';

/* ============================================================
   TRAFFIC HMI CONTROL PANEL — script.js
   All state persisted to localStorage under key "hmi_state"
   ============================================================ */

// ─── STATE ──────────────────────────────────────────────────
const DIRECTIONS = ['North', 'South', 'East', 'West'];
const DEFAULT_STATE = {
    fsBase: 13,
    windows: {
        'win-dashboard': { x: 30, y: 30, w: 420, h: 400, open: true, pinned: false },
        'win-connection': { x: 480, y: 30, w: 360, h: 500, open: false, pinned: false },
        'win-roi': { x: 100, y: 80, w: 560, h: 440, open: false, pinned: false },
        'win-tl': { x: 480, y: 80, w: 400, h: 500, open: false, pinned: false },
        'win-live': { x: 200, y: 200, w: 500, h: 360, open: false, pinned: false },
    },
    config: {
        controllerHost: '',
        controllerPort: '',
        timeout: '',
        yolo: '',
        cycleTimer: 30,
        flaskHost: '0.0.0.0',
        flaskPort: 5050,
        mode2Links: ''
    },

    tlIds: { 
        North: { id: '', api: '' }, 
        South: { id: '', api: '' }, 
        East: { id: '', api: '' }, 
        West: { id: '', api: '' } 
    },
    rois: {},
    localControlEnabled: false,
    localTLStates: { North: 'red', South: 'red', East: 'red', West: 'red' },
    logEntries: []
};

function loadState() {
    try {
        const raw = localStorage.getItem('hmi_state');
        if (raw) return JSON.parse(raw);
    } catch (_) { }
    return JSON.parse(JSON.stringify(DEFAULT_STATE));
}

function saveState() {
    try { localStorage.setItem('hmi_state', JSON.stringify(appState)); } catch (_) { }
}

let appState = loadState();
// Ensure all window keys exist
Object.keys(DEFAULT_STATE.windows).forEach(k => {
    if (!appState.windows[k]) appState.windows[k] = { ...DEFAULT_STATE.windows[k] };
});
if (!appState.config) appState.config = { ...DEFAULT_STATE.config };
if (!appState.tlIds) appState.tlIds = { ...DEFAULT_STATE.tlIds };
// Migrate old string-based tlIds to object-based if needed
['North', 'South', 'East', 'West'].forEach(l => {
    if (typeof appState.tlIds[l] === 'string') {
        appState.tlIds[l] = { id: appState.tlIds[l], api: '' };
    }
});

if (!appState.rois) appState.rois = {};
if (typeof appState.localControlEnabled !== 'boolean') appState.localControlEnabled = false;
if (!appState.localTLStates) appState.localTLStates = { ...DEFAULT_STATE.localTLStates };
if (!appState.logEntries) appState.logEntries = [];

// ─── FONT SIZE ───────────────────────────────────────────────
const FSMin = 10, FSMax = 22;

function applyFontSize(val) {
    appState.fsBase = Math.max(FSMin, Math.min(FSMax, val));
    document.documentElement.style.setProperty('--fs-base', appState.fsBase + 'px');
    document.getElementById('fs-val').textContent = appState.fsBase;
    saveState();
}

document.getElementById('fs-dec').addEventListener('click', () => applyFontSize(appState.fsBase - 1));
document.getElementById('fs-inc').addEventListener('click', () => applyFontSize(appState.fsBase + 1));
applyFontSize(appState.fsBase);

// ─── CLOCK ───────────────────────────────────────────────────
function updateClock() {
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    document.getElementById('tb-time').textContent =
        pad(now.getHours()) + ':' + pad(now.getMinutes()) + ':' + pad(now.getSeconds());
    document.getElementById('tb-date').textContent =
        now.getFullYear() + '/' + pad(now.getMonth() + 1) + '/' + pad(now.getDate());
}
setInterval(updateClock, 1000);
updateClock();

// ─── TOAST ───────────────────────────────────────────────────
function toast(msg, type = 'cyan') {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = 'toast toast-' + type;
    el.textContent = msg;
    container.appendChild(el);
    requestAnimationFrame(() => {
        requestAnimationFrame(() => { el.classList.add('show'); });
    });
    setTimeout(() => {
        el.classList.remove('show');
        setTimeout(() => el.remove(), 300);
    }, 3200);
}

// ─── LOG ─────────────────────────────────────────────────────
function addLog(level, msg) {
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const time = pad(now.getHours()) + ':' + pad(now.getMinutes()) + ':' + pad(now.getSeconds());
    const entry = { time, level, msg };
    appState.logEntries.unshift(entry);
    if (appState.logEntries.length > 200) appState.logEntries = appState.logEntries.slice(0, 200);
    renderLog();
    saveState();
}

function renderLog() {
    const tbody = document.getElementById('log-tbody');
    if (!tbody) return;
    if (!appState.logEntries.length) {
        tbody.innerHTML = '<tr><td colspan="3" style="color:var(--text-dim); text-align:center; padding:16px;">-- NO EVENTS --</td></tr>';
        return;
    }
    const colorMap = { INFO: 'var(--cyan)', WARN: 'var(--yellow)', ERR: 'var(--red)', OK: 'var(--green)' };
    tbody.innerHTML = appState.logEntries.map(e => `
    <tr>
      <td>${e.time}</td>
      <td style="color:${colorMap[e.level] || 'var(--text-mid)'};">${e.level}</td>
      <td>${e.msg}</td>
    </tr>`).join('');
}

const logClearBtn = document.getElementById('log-clear-btn');
if (logClearBtn) {
    logClearBtn.addEventListener('click', () => {
        appState.logEntries = [];
        renderLog();
        saveState();
        toast('Event log cleared', 'yellow');
    });
}

renderLog();

// ─── WINDOWS ─────────────────────────────────────────────────
const CANVAS_EL = document.getElementById('canvas');
let zCounter = 200;

function getCanvasBounds() {
    return { w: CANVAS_EL.clientWidth, h: CANVAS_EL.clientHeight };
}

function applyWindowState(winId) {
    const el = document.getElementById(winId);
    if (!el) return;
    const s = appState.windows[winId];
    el.style.display = s.open ? 'flex' : 'none';
    el.style.left = s.x + 'px';
    el.style.top = s.y + 'px';
    el.style.width = s.w + 'px';
    el.style.height = s.h + 'px';
    el.classList.toggle('pinned', !!s.pinned);
    const pinBtn = el.querySelector('.pin-btn');
    if (pinBtn) pinBtn.classList.toggle('active', !!s.pinned);
}

function openWindow(winId) {
    const s = appState.windows[winId];
    s.open = true;
    applyWindowState(winId);
    bringToFront(winId);
    updateSidebarBtns();
    saveState();
}

function closeWindow(winId) {
    const s = appState.windows[winId];
    s.open = false;
    applyWindowState(winId);
    updateSidebarBtns();
    saveState();
}

function toggleWindow(winId) {
    const s = appState.windows[winId];
    if (s.open) closeWindow(winId); else openWindow(winId);
}

function bringToFront(winId) {
    const el = document.getElementById(winId);
    if (el) el.style.zIndex = ++zCounter;
}

function constrainPos(x, y, w, h) {
    const b = getCanvasBounds();
    return {
        x: Math.max(0, Math.min(b.w - 40, x)),
        y: Math.max(0, Math.min(b.h - 30, y))
    };
}

function initDraggable(el) {
    const winId = el.id;
    const titlebar = el.querySelector('.fwin-titlebar');
    let dragging = false, ox = 0, oy = 0;

    titlebar.addEventListener('mousedown', (e) => {
        if (e.target.classList.contains('fwin-btn')) return;
        const s = appState.windows[winId];
        if (s.pinned) return;
        dragging = true;
        ox = e.clientX - s.x;
        oy = e.clientY - s.y;
        bringToFront(winId);
        document.body.style.userSelect = 'none';
    });

    document.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        const s = appState.windows[winId];
        const nx = e.clientX - ox;
        const ny = e.clientY - oy;
        const { x, y } = constrainPos(nx, ny, s.w, s.h);
        s.x = x; s.y = y;
        el.style.left = x + 'px';
        el.style.top = y + 'px';
    });

    document.addEventListener('mouseup', () => {
        if (dragging) { dragging = false; document.body.style.userSelect = ''; saveState(); }
    });
}

function initResizable(el) {
    const winId = el.id;
    const handle = el.querySelector('.fwin-resize');
    if (!handle) return;
    let resizing = false, ox = 0, oy = 0, ow = 0, oh = 0;

    handle.addEventListener('mousedown', (e) => {
        e.stopPropagation();
        const s = appState.windows[winId];
        resizing = true;
        ox = e.clientX; oy = e.clientY;
        ow = s.w; oh = s.h;
        bringToFront(winId);
        document.body.style.userSelect = 'none';
    });

    document.addEventListener('mousemove', (e) => {
        if (!resizing) return;
        const s = appState.windows[winId];
        const nw = Math.max(200, ow + (e.clientX - ox));
        const nh = Math.max(120, oh + (e.clientY - oy));
        s.w = nw; s.h = nh;
        el.style.width = nw + 'px';
        el.style.height = nh + 'px';
        if (winId === 'win-roi') resizeROICanvas();
    });

    document.addEventListener('mouseup', () => {
        if (resizing) { resizing = false; document.body.style.userSelect = ''; saveState(); }
    });
}

function initWindowControls(el) {
    const winId = el.id;
    el.querySelector('.close-btn').addEventListener('click', () => closeWindow(winId));
    el.querySelector('.pin-btn').addEventListener('click', () => {
        const s = appState.windows[winId];
        s.pinned = !s.pinned;
        el.classList.toggle('pinned', s.pinned);
        el.querySelector('.pin-btn').classList.toggle('active', s.pinned);
        toast(s.pinned ? 'Window pinned' : 'Window unpinned', s.pinned ? 'cyan' : 'yellow');
        saveState();
    });

    el.addEventListener('mousedown', () => bringToFront(winId));
}

Object.keys(appState.windows).forEach(winId => {
    const el = document.getElementById(winId);
    if (!el) return;
    applyWindowState(winId);
    initDraggable(el);
    initResizable(el);
    initWindowControls(el);
});

// ─── SIDEBAR BUTTONS ─────────────────────────────────────────
const SB_MAP = {
    'sb-dash': 'win-dashboard',
    'sb-conn': 'win-connection',
    'sb-roi': 'win-roi',
    'sb-tl': 'win-tl',
    'sb-live': 'win-live'
};

function updateSidebarBtns() {
    Object.entries(SB_MAP).forEach(([btnId, winId]) => {
        const btn = document.getElementById(btnId);
        if (btn) btn.classList.toggle('active', !!appState.windows[winId]?.open);
    });
}

Object.entries(SB_MAP).forEach(([btnId, winId]) => {
    const btn = document.getElementById(btnId);
    if (btn) btn.addEventListener('click', () => toggleWindow(winId));
});

updateSidebarBtns();

const sbMode2 = document.getElementById('sb-mode2');
if (sbMode2) {
    sbMode2.addEventListener('click', () => {
        const m2Panel = document.getElementById('mode2-panel');
        const canvas = document.getElementById('canvas');
        const isActive = m2Panel.classList.toggle('active');
        canvas.style.display = isActive ? 'none' : 'block';
        sbMode2.classList.toggle('active', isActive);
        if (isActive) {
            // Close all windows when entering Mode 2
            Object.keys(appState.windows).forEach(winId => closeWindow(winId));
        }
    });
}

// ─── CONFIG FORM ─────────────────────────────────────────────
function loadConfigToForm() {
    const c = appState.config;
    document.getElementById('cfg-controller-host').value = c.controllerHost || '';
    document.getElementById('cfg-controller-port').value = c.controllerPort || '';
    document.getElementById('cfg-timeout').value = c.timeout || '';
    document.getElementById('cfg-yolo').value = c.yolo || '';
    document.getElementById('cfg-cycle-timer').value = c.cycleTimer || 30;
    const liveUrlEl = document.getElementById('cfg-live-url');
    if (liveUrlEl) liveUrlEl.value = c.liveFeedUrl || '';
    const mode2LinksEl = document.getElementById('cfg-mode2-links');
    if (mode2LinksEl) mode2LinksEl.value = c.mode2Links || '';
    document.getElementById('cfg-flask-host').value = c.flaskHost || '0.0.0.0';
    document.getElementById('cfg-flask-port').value = c.flaskPort || 5050;
    loadMode2SourcesToInputs();
    updateTopbarFromConfig();
    updateApiEndpoints();
}

function saveConfigFromForm() {
    appState.config.controllerHost = document.getElementById('cfg-controller-host').value.trim();
    appState.config.controllerPort = document.getElementById('cfg-controller-port').value;
    appState.config.timeout = document.getElementById('cfg-timeout').value;
    appState.config.yolo = document.getElementById('cfg-yolo').value.trim();
    appState.config.cycleTimer = document.getElementById('cfg-cycle-timer').value || 30;
    appState.config.mode2Links = document.getElementById('cfg-mode2-links') ? document.getElementById('cfg-mode2-links').value.trim() : '';
    appState.config.flaskHost = document.getElementById('cfg-flask-host').value.trim() || '0.0.0.0';
    appState.config.flaskPort = document.getElementById('cfg-flask-port').value || 5050;
    saveState();
    updateTopbarFromConfig();
    updateApiEndpoints();
    applyMode2Links(); // Auto-map links on save
}

function updateTopbarFromConfig() {
    const c = appState.config;
    document.getElementById('tb-host').textContent = (c.controllerHost || '--') + ':' + (c.controllerPort || '--');
}

function updateApiEndpoints() {
    const base = window.location.origin;
    const elFeed = document.getElementById('api-ep-feed');
    const elCounts = document.getElementById('api-ep-counts');
    const elCam = document.getElementById('api-ep-cam');
    if (elFeed) elFeed.textContent = base + '/api/live_feed';
    if (elCounts) elCounts.textContent = base + '/api/lane_counts';
    if (elCam) elCam.textContent = base + '/api/camera/status';
}

function parseMode2Links(text) {
    const items = text.split(/[\n,]+/).map(l => l.trim()).filter(Boolean);
    const mapping = { North: '', South: '', East: '', West: '' };
    const order = ['North', 'South', 'East', 'West'];
    let idx = 0;

    items.forEach(item => {
        // Match "Direction: URL" or "Direction=URL"
        const match = item.match(/^(north|south|east|west)[:=]\s*(.*)$/i);
        if (match) {
            const lane = match[1].charAt(0).toUpperCase() + match[1].slice(1).toLowerCase();
            const url = match[2].trim();
            if (url) mapping[lane] = url;
        } else {
            // No label, use next available direction in N, S, E, W order
            // Check if it's a URL or a device index
            if (/^https?:\/\//i.test(item) || /^rtsp:/i.test(item) || /^\d+$/.test(item)) {
                // Find next empty slot
                while (idx < order.length && mapping[order[idx]]) idx++;
                if (idx < order.length) {
                    mapping[order[idx]] = item;
                    idx++;
                }
            }
        }
    });
    return mapping;
}

function applyMode2Links() {
    const txt = document.getElementById('cfg-mode2-links');
    if (!txt) return;
    const mapping = parseMode2Links(txt.value);
    appState.mode2Sources = mapping;
    
    const postData = {};
    ['North', 'South', 'East', 'West'].forEach(lane => {
        const val = mapping[lane];
        // 1. Update Connection Window inputs
        const inpt1 = document.getElementById('mode2-' + lane.toLowerCase());
        if (inpt1) inpt1.value = val;
        
        // 2. Update Mode 2 Panel inputs
        const inpt2 = document.getElementById('m2-src-' + lane);
        if (inpt2) inpt2.value = val;
        
        postData['src_' + lane.toLowerCase()] = val;
    });
    
    saveState();

    // Sync with server
    fetch('/api/mode2_config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(postData)
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            // Success toast is optional here if called from saveConfigFromForm
        }
    })
    .catch(err => console.error('Mode2 sync failed:', err));
}

function loadMode2SourcesToInputs() {
    const mapping = appState.mode2Sources || { North: '', South: '', East: '', West: '' };
    ['North', 'South', 'East', 'West'].forEach(lane => {
        const inpt = document.getElementById('mode2-' + lane.toLowerCase());
        if (inpt) inpt.value = mapping[lane] || '';
    });
}

function updateLiveFeedSrc() {
    const liveImg = document.getElementById('live-img');
    const roiImg = document.getElementById('roi-img');
    const timestamp = new Date().getTime();
    if (liveImg) liveImg.src = '/video_feed?t=' + timestamp;
    if (roiImg) roiImg.src = '/video_feed?t=' + timestamp;
}

function handleConnectionCommand(actionName) {
    saveConfigFromForm();
    toast(`${actionName.replace('_', ' ').toUpperCase()}...`, 'cyan');

    fetch('/panel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: actionName,
            controller_host: appState.config.controllerHost,
            controller_port: appState.config.controllerPort,
            controller_timeout: appState.config.timeout,
            yolo_model: appState.config.yolo,
            cycle_timer: appState.config.cycleTimer,
            live_feed_url: appState.config.liveFeedUrl,
            flask_host: appState.config.flaskHost,
            flask_port: appState.config.flaskPort
        })
    }).then(r => r.json()).then(data => {
        toast(data.message || 'Success', data.status === 'success' ? 'green' : 'red');
    }).catch(() => {
        toast('Network Error', 'red');
    });
}

document.getElementById('btn-save-only').addEventListener('click', () => handleConnectionCommand('save_only'));
document.getElementById('btn-toggle-connect').addEventListener('click', () => handleConnectionCommand('toggle_connect'));
const startBtn = document.getElementById('btn-start-system');
const stopBtn = document.getElementById('btn-stop-system');
if (startBtn) {
    startBtn.addEventListener('click', () => handleConnectionCommand('start_system'));
}
if (stopBtn) {
    stopBtn.addEventListener('click', () => handleConnectionCommand('stop_system'));
}
// Auto-apply logic for Mode 2 links
const mode2LinksTextArea = document.getElementById('cfg-mode2-links');
if (mode2LinksTextArea) {
    mode2LinksTextArea.addEventListener('input', () => {
        applyMode2Links();
        saveConfigFromForm();
    });
}

loadConfigToForm();
updateLocalControlUI();

function getConnectionStatus() {
    return document.getElementById('tb-control-status')?.textContent?.trim().toUpperCase() || 'DISCONNECTED';
}

function setLocalControlEnabled(enabled) {
    appState.localControlEnabled = enabled;
    saveState();
    updateLocalControlUI();
    toast(enabled ? 'Local traffic light override ENABLED' : 'Local traffic light override DISABLED', enabled ? 'green' : 'cyan');
    if (!enabled) pollStats();
}

function updateLocalControlUI() {
    const btn = document.getElementById('tb-toggle-local');
    if (!btn) return;
    btn.classList.toggle('active', !!appState.localControlEnabled);
    btn.textContent = appState.localControlEnabled ? 'LOCAL' : 'AUTO';
}

function updateLocalPhaseFromStates() {
    const active = Object.entries(appState.localTLStates).find(([_, state]) => state === 'green');
    const phaseEl = document.getElementById('tb-phase-lane');
    if (phaseEl) {
        phaseEl.textContent = active ? active[0].toUpperCase() : '--';
    }
}

function applyLocalTLState(lane, state) {
    appState.localTLStates[lane] = state;
    saveState();
    setTLState(lane, state);
    updateLocalPhaseFromStates();
}

const localToggleBtn = document.getElementById('tb-toggle-local');
if (localToggleBtn) {
    localToggleBtn.addEventListener('click', () => setLocalControlEnabled(!appState.localControlEnabled));
}

function loadExternalConfig() {
    fetch('/api/config')
        .then(r => r.json())
        .then(data => {
            appState.config.controllerHost = data.controller_host || '';
            appState.config.controllerPort = data.controller_port || '';
            appState.config.timeout = data.controller_timeout || '';
            appState.config.yolo = data.yolo_model || '';
            appState.config.cycleTimer = data.cycle_timer || 30;
            appState.config.flaskHost = data.flask_host || '0.0.0.0';
            appState.config.flaskPort = data.flask_port || 5050;
            appState.config.liveFeedUrl = data.live_feed_url || '';
            saveState();
            loadConfigToForm();
        })
        .catch(err => console.error("Could not fetch initial Config", err));
}
loadExternalConfig();

// ─── TRAFFIC LIGHT MAPPING ───────────────────────────────────
function loadTLForm() {
    ['North', 'South', 'East', 'West'].forEach(lane => {
        const apiInp = document.getElementById('api-' + lane.toLowerCase());
        const entry = appState.tlIds[lane] || { api: '' };
        if (apiInp) apiInp.value = entry.api || '';
    });
}

document.getElementById('btn-update-tl').addEventListener('click', () => {
    ['North', 'South', 'East', 'West'].forEach(lane => {
        const apiEl = document.getElementById('api-' + lane.toLowerCase());
        appState.tlIds[lane] = {
            id: null,
            api: apiEl ? apiEl.value.trim() : ''
        };
    });
    saveState();

    fetch('/tl_panel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            api_north: appState.tlIds.North.api,
            api_south: appState.tlIds.South.api,
            api_east:  appState.tlIds.East.api,
            api_west:  appState.tlIds.West.api
        })
    }).then(r => r.json()).then(data => {
        toast(data.message || 'Bindings updated', data.status === 'success' ? 'green' : 'red');
    }).catch(() => toast('Network Error', 'red'));
});

window.testTL = function (lane, state) {
    const apiBase = document.getElementById('api-' + lane.toLowerCase()).value.trim();

    if (appState.localControlEnabled) {
        applyLocalTLState(lane, state);
        toast(`Local override ${lane} -> ${state.toUpperCase()}`, 'green');
        return;
    }

    if (!apiBase) {
        toast("Enter an API URL first", "red");
        return;
    }

    toast(`Proxy Test: ${lane} -> ${state.toUpperCase()}`, 'cyan');
    
    fetch('/api/tl_proxy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: apiBase, state: state })
    })
    .then(r => r.json())
    .then(data => {
        if(data.status === 'success') toast(`API Success: ${lane} set to ${state.toUpperCase()}`, 'green');
        else toast(`API Error: ${data.message}`, 'red');
    })
    .catch(err => toast(`Proxy Error: ${err.message}`, 'red'));
};

// ─── UTILS ───────────────────────────────────────────────────
function getContainedImageBounds(img) {
    if (!img || !img.naturalWidth || !img.clientWidth) return null;
    const ratio = img.naturalWidth / img.naturalHeight;
    let w = img.clientWidth;
    let h = img.clientHeight;
    if (w / h > ratio) { w = h * ratio; } else { h = w / ratio; }
    const x = (img.clientWidth - w) / 2;
    const y = (img.clientHeight - h) / 2;
    return { x, y, w, h };
}

loadTLForm();

// ─── ROI DRAWING ─────────────────────────────────────────────
const roiCanvas = document.getElementById('roi-canvas');
const roiCtx = roiCanvas ? roiCanvas.getContext('2d') : null;
let roiPoints = [];

function resizeROICanvas() {
    const img = document.getElementById('roi-img');
    if (!roiCanvas || !img) return;
    const w = img.clientWidth || img.width;
    const h = img.clientHeight || img.height;
    if (w > 0 && h > 0) {
        roiCanvas.width = w;
        roiCanvas.height = h;
        drawROIScene();
    }
}

const roiImg = document.getElementById('roi-img');
if (roiImg) {
    roiImg.addEventListener('load', () => resizeROICanvas());
    setInterval(() => {
        if (roiCanvas && roiCanvas.width !== roiImg.clientWidth && roiImg.clientWidth > 0) {
            resizeROICanvas();
        }
    }, 1000);
}
window.addEventListener('resize', resizeROICanvas);

function drawROIScene() {
    if (!roiCtx) return;
    roiCtx.clearRect(0, 0, roiCanvas.width, roiCanvas.height);
    const img = document.getElementById('roi-img');
    const rect = getContainedImageBounds(img);
    if (!rect) return;

    Object.entries(appState.rois).forEach(([lane, pts_nat]) => {
        if (!pts_nat || pts_nat.length < 4) return;
        const sx = rect.w / (img.naturalWidth || 1);
        const sy = rect.h / (img.naturalHeight || 1);
        roiCtx.beginPath();
        roiCtx.moveTo(rect.x + pts_nat[0][0] * sx, rect.y + pts_nat[0][1] * sy);
        for (let i = 1; i < pts_nat.length; i++) {
            roiCtx.lineTo(rect.x + pts_nat[i][0] * sx, rect.y + pts_nat[i][1] * sy);
        }
        roiCtx.closePath();
        roiCtx.strokeStyle = 'rgba(0,212,245,0.73)';
        roiCtx.lineWidth = 2.5;
        roiCtx.stroke();
        roiCtx.fillStyle = 'rgba(0,212,245,0.15)';
        roiCtx.fill();
        roiCtx.fillStyle = '#00d4f5';
        roiCtx.font = 'bold 13px "Share Tech Mono"';
        roiCtx.fillText(lane.toUpperCase(), rect.x + pts_nat[0][0] * sx + 5, rect.y + pts_nat[0][1] * sy - 8);
    });

    roiPoints.forEach((pt, idx) => {
        roiCtx.beginPath();
        roiCtx.arc(pt.x, pt.y, 5, 0, Math.PI * 2);
        roiCtx.fillStyle = '#f5c400';
        roiCtx.fill();
        roiCtx.strokeStyle = '#fff';
        roiCtx.lineWidth = 1.5;
        roiCtx.stroke();
        roiCtx.fillText(idx + 1, pt.x + 8, pt.y + 9);
    });

    if (roiPoints.length > 1) {
        roiCtx.beginPath();
        roiCtx.moveTo(roiPoints[0].x, roiPoints[0].y);
        roiPoints.forEach(p => roiCtx.lineTo(p.x, p.y));
        if (roiPoints.length === 4) roiCtx.closePath();
        roiCtx.strokeStyle = '#f5c400';
        roiCtx.lineWidth = 2;
        roiCtx.stroke();
    }
}

if (roiCanvas) {
    roiCanvas.addEventListener('mousedown', (e) => {
        if (roiPoints.length >= 4) return;
        const rect = roiCanvas.getBoundingClientRect();
        const x = (e.clientX - rect.left);
        const y = (e.clientY - rect.top);
        roiPoints.push({ x: Math.round(x), y: Math.round(y) });
        drawROIScene();
        const remaining = 4 - roiPoints.length;
        const statusEl = document.getElementById('roi-status');
        if (statusEl) statusEl.textContent = remaining > 0 ? remaining + ' POINT(S) REMAINING...' : 'ROI SHAPE COMPLETE -- CLICK SAVE ROI';
    });
}

document.getElementById('roi-clear-btn').addEventListener('click', () => {
    roiPoints = [];
    drawROIScene();
    const s = document.getElementById('roi-status');
    if (s) s.textContent = 'POINTS CLEARED -- CLICK 4 POINTS';
});

document.getElementById('roi-save-btn').addEventListener('click', () => {
    if (roiPoints.length !== 4) { toast('Exactly 4 points required', 'red'); return; }
    const lane = document.getElementById('roi-lane').value;
    const img = document.getElementById('roi-img');
    const rect = getContainedImageBounds(img);
    if (!rect) return;
    const pts_nat = roiPoints.map(p => {
        const x_rel = p.x - rect.x;
        const y_rel = p.y - rect.y;
        return [ Math.round(x_rel * (img.naturalWidth / rect.w)), Math.round(y_rel * (img.naturalHeight / rect.h)) ];
    });
    appState.rois[lane] = pts_nat;
    roiPoints = [];
    drawROIScene();
    fetch('/roi_panel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lane, points: pts_nat })
    }).then(r => r.json()).then(data => {
        toast(data.message || 'ROI saved', data.status === 'success' ? 'green' : 'red');
    });
});

document.getElementById('roi-save-set-btn').addEventListener('click', () => {
    const name = document.getElementById('roi-set-name').value.trim();
    if (!name) { toast("Enter a set name", "red"); return; }
    fetch('/roi_panel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'save_set', name: name, rois: appState.rois })
    }).then(r => r.json()).then(data => {
        toast(data.message, data.status === 'success' ? 'green' : 'red');
        loadROISets();
    });
});

function loadROISets() {
    fetch('/roi_panel?action=list_sets')
        .then(r => r.json())
        .then(data => {
            const list = document.getElementById('roi-sets-list');
            if (!list) return;
            if (!data.sets || !data.sets.length) { list.innerHTML = '<span class="form-hint">No saved sets...</span>'; return; }
            list.innerHTML = data.sets.map(s => `<div class="status-row" style="cursor:pointer;" onclick="selectROISet('${s}')"><span class="status-key">${s}</span></div>`).join('');
        });
}

window.selectROISet = function(name) {
    document.getElementById('roi-set-name').value = name;
    toast(`Selected set: ${name}`, 'cyan');
};

document.getElementById('roi-load-btn').addEventListener('click', () => {
    const name = document.getElementById('roi-set-name').value.trim();
    if (!name) { toast("Select/Enter a set name", "red"); return; }
    fetch(`/roi_panel?action=load_set&name=${name}`)
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                appState.rois = data.rois;
                saveState();
                drawROIScene();
                toast(`Set "${name}" loaded`, 'green');
            } else toast(data.message, 'red');
        });
});

loadROISets();

// ─── STATS POLLING ──────────────────────────────────────────
let pollInterval = null;
function pollStats() {
    fetch('/api/system')
        .then(r => r.json())
        .then(data => {
            const cs = document.getElementById('tb-control-status');
            const ss = document.getElementById('tb-system-status');
            const ds = document.getElementById('tb-detect-status');
            const dControl = document.getElementById('dot-control');
            const sControl = document.getElementById('stat-control');
            
            if (cs) {
                cs.textContent = data.connection_status.toUpperCase();
                cs.className = 'tb-stat-value ' + (data.connection_status === 'Connected' ? 'green' : 'red');
            }
            if (dControl) dControl.className = 'dot ' + (data.connection_status === 'Connected' ? 'green' : 'red');
            if (sControl) sControl.textContent = data.connection_status.toUpperCase();

            if (ss) {
                ss.textContent = data.system_status.toUpperCase();
                ss.className = 'tb-stat-value ' + (data.system_status === 'Running' ? 'green' : 'red');
            }

            const isRunning = data.system_status === 'Running';
            const toggleBtn = document.getElementById('btn-toggle-system');
            if (toggleBtn) {
                toggleBtn.textContent = isRunning ? '■ STOP SYSTEM' : '▶ START SYSTEM';
                toggleBtn.className = 'btn ' + (isRunning ? 'btn-red' : 'btn-green');
            }
            const m2ToggleBtn = document.getElementById('m2-btn-toggle');
            if (m2ToggleBtn) {
                m2ToggleBtn.textContent = isRunning ? '■ STOP' : '▶ START';
                m2ToggleBtn.className = 'm2-sys-btn ' + (isRunning ? 'stop' : 'start');
            }

            const dSys = document.getElementById('dot-detect');
            const sSys = document.getElementById('stat-detect');
            if (dSys) dSys.className = 'dot ' + (isRunning ? 'green' : 'red');
            if (sSys) sSys.textContent = isRunning ? 'RUNNING' : 'STOPPED';

            if (ds) {
                ds.textContent = data.detection_active ? 'ACTIVE' : 'INACTIVE';
                ds.style.color = data.detection_active ? 'var(--green)' : 'var(--text-dim)';
            }

            const ct = document.getElementById('tb-cycle-timer');
            if (ct) ct.textContent = data.timer.toFixed(1) + 's';

            const ph = document.getElementById('tb-phase-lane');
            const ph_l = document.getElementById('ph-lane');
            const ph_t = document.getElementById('ph-timer');
            const ph_b = document.getElementById('ph-bar');
            if (ph) ph.textContent = data.current_lane.toUpperCase();
            if (ph_l) ph_l.textContent = data.current_lane.toUpperCase();
            if (ph_t) ph_t.textContent = data.timer.toFixed(1) + 's';
            if (ph_b) {
                const perc = Math.min(100, (data.timer / (data.max_timer || 30)) * 100);
                ph_b.style.width = perc + '%';
            }

            document.getElementById('tb-total-veh').textContent = data.total_vehicles || 0;

            if (data.logs && data.logs.length > 0) {
                data.logs.forEach(l => addLog(l.level, l.msg));
            }
        });

    fetch('/api/lane_counts')
        .then(r => r.json())
        .then(data => {
            const counts = data.counts || data;  // support both flat and nested
            const greenLane = data.green_lane || '';
            const timer = data.timer || 0;
            const cycleDur = data.cycle_duration || 30;

            // Update phase lane + timer from lane_counts (more accurate)
            const ph = document.getElementById('tb-phase-lane');
            const ph_l = document.getElementById('ph-lane');
            const ph_t = document.getElementById('ph-timer');
            const ph_b = document.getElementById('ph-bar');
            const ct  = document.getElementById('tb-cycle-timer');

            if (ph) ph.textContent = (greenLane || 'NONE').toUpperCase();
            if (ph_l) ph_l.textContent = (greenLane || 'NONE').toUpperCase();
            if (ph_t) ph_t.textContent = timer + 's';
            if (ct) ct.textContent = timer + 's';
            if (ph_b) ph_b.style.width = Math.min(100, (timer / (cycleDur || 30)) * 100) + '%';

            ['North', 'South', 'East', 'West'].forEach(l => {
                const el = document.getElementById('count-' + l);
                if (el) el.textContent = counts[l] || 0;
                const card = document.getElementById('lane-card-' + l);
                if (card) card.classList.toggle('active', l === greenLane);
            });

            // TL visual states
            const tl_states = data.tl_states || {};
            ['North', 'South', 'East', 'West'].forEach(l => {
                setTLState(l, tl_states[l] || 'red');
            });

            // Update total vehicle count
            const totalEl = document.getElementById('tb-total-veh');
            if (totalEl) totalEl.textContent = Object.values(counts).reduce((a,b) => a+b, 0);

            // Detection dot & label
            const dSys = document.getElementById('dot-detect');
            const sSys = document.getElementById('stat-detect');
            const dsBar = document.getElementById('tb-detect-status');
            const isRunning = data.system_started;
            const isDetecting = data.detect_status === 'ACTIVE';
            if (dSys) dSys.className = 'dot ' + (isRunning ? 'green' : 'red');
            if (sSys) sSys.textContent = isRunning ? 'RUNNING' : 'STOPPED';
            if (dsBar) { dsBar.textContent = data.detect_status || 'INACTIVE'; dsBar.style.color = isDetecting ? 'var(--green)' : 'var(--text-dim)'; }

            // System status topbar
            const ssSys = document.getElementById('tb-system-status');
            if (ssSys) {
                ssSys.textContent = isRunning ? 'RUNNING' : 'STOPPED';
                ssSys.className = 'tb-stat-value ' + (isRunning ? 'green' : 'red');
            }

            // Live Feed status row
            const feedOnline = data.feed_status === 'ONLINE';
            const dotFeed = document.getElementById('dot-feed');
            const statFeed = document.getElementById('stat-feed');
            if (dotFeed) dotFeed.className = 'dot ' + (feedOnline ? 'green' : 'red');
            if (statFeed) statFeed.textContent = data.feed_status || 'NO SIGNAL';

            // Controller row (distinct from API)
            const dotCtrl = document.getElementById('dot-ctrl');
            const statCtrl = document.getElementById('stat-ctrl');
            const ctrlOk = isRunning && isDetecting;
            if (dotCtrl) dotCtrl.className = 'dot ' + (ctrlOk ? 'green' : 'yellow');
            if (statCtrl) statCtrl.textContent = ctrlOk ? 'ACTIVE' : isRunning ? 'STANDBY' : 'OFFLINE';
        });
}

function updatePhaseVisuals(counts) {
    // This is now handled inside the lane_counts fetch above
    // Kept for legacy compatibility
}

// Updates the small TL lights in win-tl panel
function setTLState(lane, state) {
    ['r', 'y', 'g'].forEach(c => {
        const el = document.getElementById(`tl-${c}-${lane}`);
        if (el) {
            el.classList.remove('lit-red', 'lit-yellow', 'lit-green');
            if (state === 'red'    && c === 'r') el.classList.add('lit-red');
            if (state === 'yellow' && c === 'y') el.classList.add('lit-yellow');
            if (state === 'green'  && c === 'g') el.classList.add('lit-green');
        }
    });
}

// Updates the Mode 2 realistic traffic light bulbs
function setM2TLState(lane, state) {
    ['r', 'y', 'g'].forEach(c => {
        const el = document.getElementById(`m2-tl-${c}-${lane}`);
        if (el) {
            el.classList.remove('lit-red', 'lit-yellow', 'lit-green');
            if (state === 'red'    && c === 'r') el.classList.add('lit-red');
            if (state === 'yellow' && c === 'y') el.classList.add('lit-yellow');
            if (state === 'green'  && c === 'g') el.classList.add('lit-green');
        }
    });
}

// ─── MODE 2 LIVE STATS POLLING ────────────────────────────────
function pollMode2Stats() {
    fetch('/api/lane_counts')
        .then(r => r.json())
        .then(lc => {
            const counts    = lc.counts || {};
            const tl_states = lc.tl_states || {};
            const greenLane = lc.green_lane || '';
            const timer     = lc.timer || 0;
            const cycleDur  = lc.cycle_duration || 30;
            const m2counts  = lc.mode2_counts || counts;

            // Also pull intensity scores
            fetch('/api/intensity_scores')
                .then(r => r.json())
                .then(sc => {
                    const scores      = sc.scores || {};
                    const waits       = sc.wait_times || {};
                    const greenTimers = sc.green_timers || {};
                    const maxScore    = Math.max(1, ...Object.values(scores));

                    // Sort lanes by score for priority badges
                    const ranked = Object.entries(scores)
                        .sort((a, b) => b[1] - a[1])
                        .map(([lane], idx) => [lane, idx + 1]);
                    const rankMap = Object.fromEntries(ranked);

                    DIRECTIONS.forEach(lane => {
                        const state = tl_states[lane] || 'red';
                        const cnt   = m2counts[lane] || counts[lane] || 0;
                        const score = scores[lane] || 0;
                        const wait  = waits[lane] || 0;
                        const gtime = greenTimers[lane] || 30;
                        const rank  = rankMap[lane] || 4;
                        const loadPct = Math.min(100, (score / maxScore) * 100);

                        // TL bulbs (Mode 2 panel)
                        setM2TLState(lane, state);

                        // Vehicle count
                        const cntEl = document.getElementById('m2-cnt-' + lane);
                        if (cntEl) cntEl.textContent = cnt;

                        // Intensity score label
                        const intEl = document.getElementById('m2-intensity-' + lane);
                        if (intEl) intEl.textContent = score > 0 ? `SCR ${score.toFixed(0)}` : '';

                        // Wait time label
                        const waitEl = document.getElementById('m2-wait-' + lane);
                        if (waitEl) waitEl.textContent = wait > 0 ? `W ${wait.toFixed(0)}s` : '';

                        // Load bar (intensity as percentage)
                        const loadEl = document.getElementById('m2-load-' + lane);
                        if (loadEl) {
                            loadEl.style.width = loadPct + '%';
                            loadEl.className = 'm2-tl-load-bar' +
                                (loadPct > 70 ? ' hi' : loadPct > 40 ? ' med' : '');
                        }

                        // Countdown bar (timer as percentage of green time)
                        const cdEl = document.getElementById('m2-cd-' + lane);
                        if (cdEl) {
                            const isGreen  = state === 'green';
                            const isYellow = state === 'yellow';
                            if (isGreen) {
                                const pct = Math.min(100, (timer / (gtime || 30)) * 100);
                                cdEl.style.width = pct + '%';
                                cdEl.className = 'm2-tl-countdown-bar' +
                                    (pct < 20 ? ' critical' : pct < 40 ? ' warn' : '');
                            } else if (isYellow) {
                                cdEl.style.width = '50%';
                                cdEl.className = 'm2-tl-countdown-bar warn';
                            } else {
                                cdEl.style.width = '0%';
                                cdEl.className = 'm2-tl-countdown-bar';
                            }
                        }

                        // Priority badge
                        const priEl = document.getElementById('m2-pri-' + lane);
                        if (priEl) {
                            priEl.textContent = rank === 1 ? '★ PRIORITY' :
                                                rank === 2 ? '▲ HIGH' :
                                                rank === 3 ? '▼ LOW' : '';
                            priEl.className = `m2-priority-badge rank-${rank}`;
                        }

                        // Tile glow for active lane
                        const tileEl = document.getElementById('m2-tile-' + lane);
                        if (tileEl) {
                            tileEl.style.boxShadow = state === 'green'
                                ? '0 0 0 2px rgba(0,232,122,0.6)'
                                : state === 'yellow'
                                ? '0 0 0 2px rgba(245,196,0,0.5)'
                                : '';
                        }
                    });
                });
        });
}

setInterval(pollStats, 1000);
setInterval(pollMode2Stats, 1000);
pollStats();
pollMode2Stats();

// ─── SYSTEM START / STOP ─────────────────────────────────────
function systemAction(action) {
    fetch('/api/system', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action })
    })
    .then(r => r.json())
    .then(data => {
        toast(data.message || data.status, data.status === 'success' ? 'green' : 'red');
        pollStats();
    })
    .catch(err => toast('Network Error: ' + err.message, 'red'));
}

const toggleSystemBtn = document.getElementById('btn-toggle-system');
if (toggleSystemBtn) {
    toggleSystemBtn.addEventListener('click', () => {
        const isRunning = toggleSystemBtn.classList.contains('btn-red');
        systemAction(isRunning ? 'stop' : 'start');
    });
}

// ─── MODE 2 LOGIC ───────────────────────────────────────────
window.switchM2Tab = function(tab) {
    document.querySelectorAll('.m2-tab').forEach(el => el.classList.remove('active'));
    document.getElementById('m2-tab-' + tab).classList.add('active');
    document.querySelectorAll('.m2-section').forEach(el => el.classList.remove('active'));
    document.getElementById('m2-section-' + tab).classList.add('active');
};

window.switchM2ConfigDir = function(dir) {
    document.querySelectorAll('.m2-subtab').forEach(el => el.classList.remove('active'));
    document.getElementById('m2-subtab-' + dir).classList.add('active');
    document.querySelectorAll('.m2-dir-card').forEach(el => el.classList.remove('active'));
    document.getElementById('m2-card-' + dir).classList.add('active');
};

window.applyM2Source = function(dir) {
    const src = document.getElementById('m2-src-' + dir).value.trim();
    if (!src) return;
    fetch('/api/mode2_config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'set_source', dir, src })
    }).then(r => r.json()).then(data => {
        toast(data.message, data.status === 'success' ? 'green' : 'red');
        const img = document.getElementById('m2-prev-' + dir);
        if (img) img.src = `/video_feed/mode2/${dir}?t=${Date.now()}`;
    });
};

window.toggleControlMode = function() {
    fetch('/api/mode2_config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'toggle_mode' })
    }).then(r => r.json()).then(data => {
        const btn = document.getElementById('m2-mode-toggle');
        if (btn && data.mode_name) {
            btn.textContent = data.mode_name.toUpperCase();
            btn.classList.toggle('rush', data.mode === 2);
            toast(`Mode switched to: ${data.mode_name}`, 'cyan');
        } else if (data.message) {
            toast(data.message, 'yellow');
        }
    }).catch(err => {
        console.error("Toggle Mode Error:", err);
        toast("Failed to toggle control mode", "red");
    });
};

window.toggleSystem = function() {
    const btn = document.getElementById('m2-btn-toggle');
    const isRunning = btn.classList.contains('stop');
    systemAction(isRunning ? 'stop' : 'start');
};

// ─── MODE 2 ROI MANAGEMENT ─────────────────────────────────
let m2ROIDrawingDir = null;
let m2ROIPoints = {}; // { North: [], ... }

window.toggleM2ROIDraw = function(dir) {
    if (m2ROIDrawingDir === dir) {
        m2ROIDrawingDir = null;
        document.getElementById('m2-roi-draw-' + dir).classList.remove('active');
        document.getElementById('m2-roi-canvas-' + dir).classList.remove('drawing');
    } else {
        if (m2ROIDrawingDir) toggleM2ROIDraw(m2ROIDrawingDir);
        m2ROIDrawingDir = dir;
        document.getElementById('m2-roi-draw-' + dir).classList.add('active');
        document.getElementById('m2-roi-canvas-' + dir).classList.add('drawing');
        if (!m2ROIPoints[dir]) m2ROIPoints[dir] = [];
        drawM2ROIScene(dir);
    }
};

window.clearM2ROI = function(dir) {
    m2ROIPoints[dir] = [];
    drawM2ROIScene(dir);
    document.getElementById('m2-roi-status-' + dir).textContent = 'ROI CLEARED';
};

window.saveM2ROI = function(dir) {
    const pts = m2ROIPoints[dir];
    if (!pts || pts.length !== 4) { toast('Exactly 4 points required', 'red'); return; }
    
    const img = document.getElementById('m2-prev-' + dir);
    const rect = getContainedImageBounds(img);
    if (!rect) return;
    
    const pts_nat = pts.map(p => {
        const x_rel = p.x - rect.x;
        const y_rel = p.y - rect.y;
        return [ 
            Math.round(x_rel * (img.naturalWidth / rect.w)), 
            Math.round(y_rel * (img.naturalHeight / rect.h)) 
        ];
    });

    fetch('/api/mode2_rois', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dir, points: pts_nat })
    }).then(r => r.json()).then(data => {
        toast(data.message, data.status === 'success' ? 'green' : 'red');
        if (data.status === 'success') {
            toggleM2ROIDraw(dir);
            document.getElementById('m2-roi-status-' + dir).textContent = 'ROI SAVED';
        }
    });
};

function drawM2ROIScene(dir) {
    const canvas = document.getElementById('m2-roi-canvas-' + dir);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const img = document.getElementById('m2-prev-' + dir);
    
    // Auto-resize canvas to match displayed image
    if (img.clientWidth > 0 && canvas.width !== img.clientWidth) {
        canvas.width = img.clientWidth;
        canvas.height = img.clientHeight;
    }

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const pts = m2ROIPoints[dir] || [];

    // Draw lines
    if (pts.length > 0) {
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].y);
        pts.forEach(p => ctx.lineTo(p.x, p.y));
        if (pts.length === 4) ctx.closePath();
        ctx.strokeStyle = '#f5c400';
        ctx.lineWidth = 2;
        ctx.stroke();
        if (pts.length === 4) {
            ctx.fillStyle = 'rgba(245,196,0,0.2)';
            ctx.fill();
        }
    }

    // Draw points
    pts.forEach((p, idx) => {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 4, 0, Math.PI * 2);
        ctx.fillStyle = '#f5c400';
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1;
        ctx.stroke();
    });
}

function initM2ROICanvases() {
    DIRECTIONS.forEach(dir => {
        const canv = document.getElementById('m2-roi-canvas-' + dir);
        if (!canv) return;
        canv.addEventListener('mousedown', (e) => {
            if (m2ROIDrawingDir !== dir) return;
            if ((m2ROIPoints[dir] || []).length >= 4) return;
            const rect = canv.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            if (!m2ROIPoints[dir]) m2ROIPoints[dir] = [];
            m2ROIPoints[dir].push({ x, y });
            drawM2ROIScene(dir);
            
            const remaining = 4 - m2ROIPoints[dir].length;
            document.getElementById('m2-roi-status-' + dir).textContent = 
                remaining > 0 ? `${remaining} POINTS REMAINING` : 'ROI COMPLETE - CLICK SAVE';
        });
    });
}

initM2ROICanvases();
function loadExternalTLs() {
    fetch('/tl_panel', { headers: { 'Accept': 'application/json' } })
        .then(r => r.json())
        .then(data => {
            if (data.tl_ids) {
                appState.tlIds = data.tl_ids;
                saveState();
                ['North', 'South', 'East', 'West'].forEach(l => {
                    const apiInp = document.getElementById(`api-${l.toLowerCase()}`);
                    const entry = data.tl_ids[l] || {};
                    if (apiInp) apiInp.value = entry.api || '';
                });
            }
        });
}
loadExternalTLs();

