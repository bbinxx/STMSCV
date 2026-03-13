'use strict';

/* ============================================================
   TRAFFIC HMI CONTROL PANEL — script.js
   All state persisted to localStorage under key "hmi_state"
   ============================================================ */

// ─── STATE ──────────────────────────────────────────────────
const DEFAULT_STATE = {
    fsBase: 13,
    windows: {
        'win-dashboard': { x: 30, y: 30, w: 420, h: 400, open: true, pinned: false },
        'win-connection': { x: 480, y: 30, w: 360, h: 500, open: false, pinned: false },
        'win-roi': { x: 100, y: 80, w: 560, h: 440, open: false, pinned: false },
        'win-tl': { x: 480, y: 80, w: 360, h: 500, open: false, pinned: false },
        'win-live': { x: 200, y: 200, w: 500, h: 360, open: false, pinned: false },
        'win-log': { x: 80, y: 300, w: 440, h: 320, open: false, pinned: false },
        'win-api': { x: 600, y: 60, w: 380, h: 420, open: false, pinned: false },
    },
    config: {
        carlaHost: '',
        carlaPort: '',
        timeout: '',
        yolo: '',
        cycleTimer: 30,
        flaskHost: '0.0.0.0',
        flaskPort: 5050
    },
    tlIds: { North: '', South: '', East: '', West: '' },
    rois: {},
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
if (!appState.rois) appState.rois = {};
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

document.getElementById('log-clear-btn').addEventListener('click', () => {
    appState.logEntries = [];
    renderLog();
    saveState();
    toast('Event log cleared', 'yellow');
});

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
    // pin button
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

// Constrain position
function constrainPos(x, y, w, h) {
    const b = getCanvasBounds();
    return {
        x: Math.max(0, Math.min(b.w - 40, x)),
        y: Math.max(0, Math.min(b.h - 30, y))
    };
}

// Dragging
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

// Resizing
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

// Close and pin buttons
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

// Initialize all windows
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
    'sb-live': 'win-live',
    'sb-log': 'win-log',
    'sb-api': 'win-api'
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

// ─── CONFIG FORM ─────────────────────────────────────────────
function loadConfigToForm() {
    const c = appState.config;
    document.getElementById('cfg-carla-host').value = c.carlaHost || '';
    document.getElementById('cfg-carla-port').value = c.carlaPort || '';
    document.getElementById('cfg-timeout').value = c.timeout || '';
    document.getElementById('cfg-yolo').value = c.yolo || '';
    document.getElementById('cfg-cycle-timer').value = c.cycleTimer || 30;
    document.getElementById('cfg-live-url').value = c.liveFeedUrl || '';
    document.getElementById('cfg-flask-host').value = c.flaskHost || '0.0.0.0';
    document.getElementById('cfg-flask-port').value = c.flaskPort || 5050;
    updateTopbarFromConfig();
    updateApiEndpoints();
}

function saveConfigFromForm() {
    appState.config.carlaHost = document.getElementById('cfg-carla-host').value.trim();
    appState.config.carlaPort = document.getElementById('cfg-carla-port').value;
    appState.config.timeout = document.getElementById('cfg-timeout').value;
    appState.config.yolo = document.getElementById('cfg-yolo').value.trim();
    appState.config.cycleTimer = document.getElementById('cfg-cycle-timer').value || 30;
    appState.config.liveFeedUrl = document.getElementById('cfg-live-url').value.trim();
    appState.config.flaskHost = document.getElementById('cfg-flask-host').value.trim() || '0.0.0.0';
    appState.config.flaskPort = document.getElementById('cfg-flask-port').value || 5050;
    saveState();
    updateTopbarFromConfig();
    updateApiEndpoints();
}

function updateTopbarFromConfig() {
    const c = appState.config;
    document.getElementById('tb-host').textContent = (c.carlaHost || '--') + ':' + (c.carlaPort || '--');
}

function updateApiEndpoints() {
    const base = window.location.origin;
    document.getElementById('api-ep-feed').textContent = base + '/api/live_feed';
    document.getElementById('api-ep-counts').textContent = base + '/api/lane_counts';
    document.getElementById('api-ep-cam').textContent = base + '/api/camera/status';
}

function updateLiveFeedSrc() {
    console.log("[DEBUG] Initializing Live Feed UI sources");
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
            carla_host: appState.config.carlaHost,
            carla_port: appState.config.carlaPort,
            carla_timeout: appState.config.timeout,
            yolo_model: appState.config.yolo,
            cycle_timer: appState.config.cycleTimer,
            live_feed_url: appState.config.liveFeedUrl,
            flask_host: appState.config.flaskHost,
            flask_port: appState.config.flaskPort
        })
    }).then(r => r.json()).then(data => {
        toast(data.message || 'Success', data.status === 'success' ? 'green' : 'red');
        if (data.status === 'success') {
            if (actionName === 'toggle_connect') {
                // Backend handles the flip, we just wait for poll to update visuals
            }
        }
    }).catch(() => {
        toast('Network Error', 'red');
    });
}

document.getElementById('btn-save-only').addEventListener('click', () => handleConnectionCommand('save_only'));
document.getElementById('btn-toggle-connect').addEventListener('click', () => handleConnectionCommand('toggle_connect'));

loadConfigToForm();

// Load external Config from DB on Init
function loadExternalConfig() {
    fetch('/api/config')
        .then(r => r.json())
        .then(data => {
            appState.config.carlaHost = data.carla_host || '';
            appState.config.carlaPort = data.carla_port || '';
            appState.config.timeout = data.carla_timeout || '';
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
        const el = document.getElementById('tl-' + lane.toLowerCase());
        if (el) el.value = appState.tlIds[lane] || '';
    });
}

document.getElementById('btn-update-tl').addEventListener('click', () => {
    ['North', 'South', 'East', 'West'].forEach(lane => {
        const el = document.getElementById('tl-' + lane.toLowerCase());
        appState.tlIds[lane] = el ? el.value.trim() : '';
        const idEl = document.getElementById('tl-id-' + lane);
        if (idEl) idEl.textContent = appState.tlIds[lane] ? '#' + appState.tlIds[lane] : '--';
    });
    saveState();

    // POST to backend for validation and storage
    fetch('/tl_panel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            tl_north: appState.tlIds.North || null,
            tl_south: appState.tlIds.South || null,
            tl_east: appState.tlIds.East || null,
            tl_west: appState.tlIds.West || null
        })
    }).then(r => r.json()).then(data => {
        toast(data.message || 'Bindings updated', data.status === 'success' ? 'green' : 'red');
    }).catch(() => toast('Network Error', 'red'));
});

// Manual TL Test Helper for R/Y/G buttons
window.testTL = function (lane, state) {
    const actorId = document.getElementById('tl-' + lane.toLowerCase()).value;
    if (!actorId) {
        toast("Enter an Actor ID first", "red");
        return;
    }
    toast(`Testing ${lane}:${actorId} -> ${state.toUpperCase()}...`, 'cyan');

    fetch('/api/tl_test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ actor_id: actorId, state: state })
    }).then(r => r.json()).then(data => {
        toast(data.message, data.status === 'success' ? 'green' : 'red');
    }).catch(() => toast("Test Request Failed", "red"));
};

// ─── UTILS ───────────────────────────────────────────────────
function getContainedImageBounds(img) {
    if (!img || !img.naturalWidth || !img.clientWidth) return null;
    const ratio = img.naturalWidth / img.naturalHeight;
    let w = img.clientWidth;
    let h = img.clientHeight;
    if (w / h > ratio) {
        w = h * ratio;
    } else {
        h = w / ratio;
    }
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

    // Use clientWidth/Height of the container or the image's displayed size
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
    // Also poll slightly because offsetWidth can be 0 if window is display:none when it loads
    setInterval(() => {
        if (roiCanvas && roiCanvas.width !== roiImg.clientWidth && roiImg.clientWidth > 0) {
            resizeROICanvas();
        }
    }, 1000);
}
window.addEventListener('resize', resizeROICanvas);
setTimeout(resizeROICanvas, 500);

function drawROIScene() {
    if (!roiCtx) return;
    roiCtx.clearRect(0, 0, roiCanvas.width, roiCanvas.height);

    const img = document.getElementById('roi-img');
    const rect = getContainedImageBounds(img);
    if (!rect) return;

    // Draw saved ROIs
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
        roiCtx.shadowBlur = 4;
        roiCtx.shadowColor = 'rgba(0,0,0,0.8)';
        roiCtx.fillText(lane.toUpperCase(), rect.x + pts_nat[0][0] * sx + 5, rect.y + pts_nat[0][1] * sy - 8);
        roiCtx.shadowBlur = 0;
    });

    // Draw current points
    roiPoints.forEach((pt, idx) => {
        roiCtx.beginPath();
        roiCtx.arc(pt.x, pt.y, 5, 0, Math.PI * 2);
        roiCtx.fillStyle = '#f5c400';
        roiCtx.fill();
        roiCtx.strokeStyle = '#fff';
        roiCtx.lineWidth = 1.5;
        roiCtx.stroke();
        roiCtx.fillStyle = '#fff';
        roiCtx.font = '10px "Share Tech Mono"';
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
        if (statusEl) {
            statusEl.textContent = remaining > 0
                ? remaining + ' POINT(S) REMAINING...'
                : 'ROI SHAPE COMPLETE -- CLICK SAVE ROI';
        }
    });
}

document.getElementById('roi-clear-btn').addEventListener('click', () => {
    roiPoints = [];
    drawROIScene();
    const s = document.getElementById('roi-status');
    if (s) s.textContent = 'POINTS CLEARED -- CLICK 4 POINTS';
});

document.getElementById('roi-save-btn').addEventListener('click', () => {
    if (roiPoints.length !== 4) {
        toast('Exactly 4 points required', 'red');
        return;
    }
    const lane = document.getElementById('roi-lane').value;
    const img = document.getElementById('roi-img');
    const rect = getContainedImageBounds(img);
    if (!rect) return;

    // Scale from display point inside image bounds to natural image pixels
    const pts_nat = roiPoints.map(p => {
        const x_rel = p.x - rect.x;
        const y_rel = p.y - rect.y;
        return [
            Math.round(x_rel * (img.naturalWidth / rect.w)),
            Math.round(y_rel * (img.naturalHeight / rect.h))
        ];
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

    const serializable = {};
    for (let l in appState.rois) {
        serializable[l] = appState.rois[l];
    }

    fetch('/api/roi_sets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, config: serializable })
    }).then(r => r.json()).then(data => {
        toast(data.message, data.status === 'success' ? 'green' : 'red');
        refreshRoiSetsList();
    });
});

function refreshRoiSetsList() {
    fetch('/api/roi_sets')
        .then(r => r.json())
        .then(data => {
            const list = document.getElementById('roi-sets-list');
            list.innerHTML = '';
            if (!data.sets || data.sets.length === 0) {
                list.innerHTML = '<span class="form-hint">No saved sets...</span>';
                return;
            }
            data.sets.forEach(s => {
                const item = document.createElement('div');
                item.className = 'api-row';
                item.style.cursor = 'pointer';
                item.style.marginBottom = '4px';
                item.textContent = s;
                item.onclick = () => {
                    document.getElementById('roi-set-name').value = s;
                };
                list.appendChild(item);
            });
        });
}

document.getElementById('roi-load-btn').addEventListener('click', () => {
    const name = document.getElementById('roi-set-title') || document.getElementById('roi-set-name').value;
    if (!name) return;
    fetch(`/api/roi_sets/${name}`)
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                appState.rois = {};
                for (let l in data.rois) appState.rois[l] = data.rois[l];
                drawROIScene();
                toast(data.message, 'green');
            } else {
                toast(data.message, 'red');
            }
        });
});

// ─── TRAFFIC LIGHTS ──────────────────────────────────────────
document.getElementById('btn-update-tl').addEventListener('click', () => {
    const data = {
        tl_north: document.getElementById('tl-north').value,
        tl_south: document.getElementById('tl-south').value,
        tl_east: document.getElementById('tl-east').value,
        tl_west: document.getElementById('tl-west').value
    };

    fetch('/tl_panel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    }).then(r => r.json()).then(res => {
        // Show actual server message in toast (e.g. invalid actor ID warning)
        const toastMsg = res.message || (res.status === 'success' ? 'TL Bindings Updated' : 'Update Failed');
        toast(toastMsg, res.status === 'success' ? 'green' : 'red');

        if (res.status === 'success') {
            appState.tlIds.North = data.tl_north;
            appState.tlIds.South = data.tl_south;
            appState.tlIds.East = data.tl_east;
            appState.tlIds.West = data.tl_west;
            saveState();
            // Update UI IDs
            ['North', 'South', 'East', 'West'].forEach(l => {
                const el = document.getElementById(`tl-id-${l}`);
                if (el) el.textContent = data[`tl_${l.toLowerCase()}`] || '--';
            });
        }
    });
});

function loadExternalTLs() {
    fetch('/tl_panel', { headers: { 'Accept': 'application/json' } })
        .then(r => r.json())
        .then(data => {
            if (data.tl_ids) {
                appState.tlIds = data.tl_ids;
                saveState();
                ['North', 'South', 'East', 'West'].forEach(l => {
                    const inp = document.getElementById(`tl-${l.toLowerCase()}`);
                    if (inp) inp.value = data.tl_ids[l] || '';
                    const el = document.getElementById(`tl-id-${l}`);
                    if (el) el.textContent = data.tl_ids[l] || '--';
                });
            }
        });
}

function loadExternalROIs() {
    fetch('/roi_panel')
        .then(r => r.json())
        .then(data => {
            if (data.current_rois) {
                appState.rois = data.current_rois;
                drawROIScene();
                saveState();
            }
            if (data.roi_enabled !== undefined) {
                const cb = document.getElementById('roi-enable-cb');
                if (cb) cb.checked = data.roi_enabled;
            }
        });
    refreshRoiSetsList();
}

const roiEnableCb = document.getElementById('roi-enable-cb');
if (roiEnableCb) {
    roiEnableCb.addEventListener('change', (e) => {
        fetch('/api/roi_enable', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: e.target.checked })
        }).then(r => r.json()).then(data => {
            toast('ROI Processing ' + (data.enabled ? 'ENABLED' : 'DISABLED'), 'cyan');
        });
    });
}

loadExternalROIs();
loadExternalTLs();
// ─── LIVE STATS POLLING ──────────────────────────────────────
let phaseMax = 30;

function updateDashboardStats(data) {
    const isConn = data.connection === "Connected";
    let counts = { North: 0, South: 0, East: 0, West: 0 };
    if (isConn) {
        counts = (typeof currentMode !== 'undefined' && currentMode === 2) ? (data.mode2_counts || {}) : (data.counts || {});
    }
    const greenLane = isConn ? data.green_lane : null;
    const timer = isConn ? data.timer : 0;

    let total = 0;
    ['North', 'South', 'East', 'West'].forEach(lane => {
        const val = counts[lane] || 0;
        total += val;
        const el = document.getElementById('count-' + lane);
        if (el) el.textContent = val;

        // Active indicator on card
        const card = document.getElementById('lane-card-' + lane);
        if (card) {
            if (isConn && lane === greenLane) card.classList.add('active');
            else card.classList.remove('active');
        }
    });

    document.getElementById('tb-total-veh').textContent = total;
    updatePhaseVisuals(data);
}

function updatePhaseVisuals(data) {
    const isConn = data.connection === "Connected";
    const greenLane = data.green_lane;
    const phTimer = data.timer;
    const tlStates = data.tl_states || {};
    const counts = data.counts || {};

    if (isConn && greenLane) {
        let cycleDuration = data.cycle_duration || 30;
        let pct = (phTimer / cycleDuration) * 100;
        document.getElementById('ph-bar').style.width = pct + '%';
        document.getElementById('ph-timer').textContent = phTimer + 's';
        document.getElementById('ph-lane').textContent = greenLane.toUpperCase();
    } else {
        document.getElementById('ph-bar').style.width = '0%';
        document.getElementById('ph-timer').textContent = '--s';
        document.getElementById('ph-lane').textContent = '--';
    }

    // Update Topbar
    document.getElementById('tb-phase-lane').textContent = (isConn && greenLane) ? greenLane.toUpperCase() : '--';
    document.getElementById('tb-cycle-timer').textContent = (isConn && phTimer !== undefined) ? phTimer + 's' : '--s';

    // Update TL Visual States and Counts
    ['North', 'South', 'East', 'West'].forEach(lane => {
        const state = tlStates[lane] || 'red';
        setTLState(lane, state);

        // Show current count in the Mapping window's Visual State box (matching user's screenshot)
        const idEl = document.getElementById('tl-id-' + lane);
        if (idEl) idEl.textContent = counts[lane] !== undefined ? counts[lane] : '--';
    });
}

function setTLState(lane, state) {
    const states = ['red', 'yellow', 'green'];
    states.forEach(s => {
        const el = document.getElementById('tl-' + s[0] + '-' + lane);
        if (!el) return;
        el.className = 'tl-light' + (s === state ? ' lit-' + s : '');
    });
}

let lastBackendStatus = true;
let lastFeedStatus = "NO SIGNAL";

function pollStats() {
    fetch('/api/lane_counts')
        .then(r => r.json())
        .then(data => {
            if (!lastBackendStatus || (lastFeedStatus === "NO SIGNAL" && data.feed_status === "ONLINE")) {
                // Backend recovered, OR feed came online. Reload the stream to fix frozen MJPEG
                console.log("[DEBUG] Connection recovered or Feed came Online, reloading Live Feed...");
                updateLiveFeedSrc();
                lastBackendStatus = true;
            }
            lastFeedStatus = data.feed_status;

            updateDashboardStats(data);
            setCarlaStatus(data.connection);
            setDetectionStatus(data.detect_status);
            setFeedStatus(data.feed_status);
        })
        .catch(() => {
            lastBackendStatus = false;
            setCarlaStatus("Disconnected");
            // Clear stats on network error
            updateDashboardStats({ connection: "Disconnected" });
        });
}

function setCarlaStatus(statusString) {
    const dot = document.getElementById('dot-carla');
    const txt = document.getElementById('stat-carla');
    const tbEl = document.getElementById('tb-carla-status');
    const connBtn = document.getElementById('sb-conn');
    const toggleBtn = document.getElementById('btn-toggle-connect');

    const isConnected = statusString === "Connected";
    const isConnecting = statusString === "Connecting...";

    if (txt) txt.textContent = statusString.toUpperCase();
    if (tbEl) tbEl.textContent = statusString.toUpperCase();

    if (toggleBtn) {
        toggleBtn.textContent = isConnected ? "DISCONNECT" : (isConnecting ? "CONNECTING..." : "CONNECT");
        toggleBtn.className = isConnected ? "btn btn-red" : "btn btn-green";
    }

    if (isConnected) {
        if (dot) dot.className = 'dot green';
        if (txt) txt.style.color = 'var(--green)';
        if (tbEl) tbEl.className = 'tb-stat-value green';
        if (connBtn) connBtn.classList.add('connected');
    } else if (isConnecting) {
        if (dot) dot.className = 'dot yellow';
        if (txt) txt.style.color = 'var(--yellow)';
        if (tbEl) tbEl.className = 'tb-stat-value yellow';
        if (connBtn) connBtn.classList.remove('connected');
    } else {
        if (dot) dot.className = 'dot red';
        if (txt) txt.style.color = 'var(--red)';
        if (tbEl) tbEl.className = 'tb-stat-value red';
        if (connBtn) connBtn.classList.remove('connected');
    }
}

function setDetectionStatus(status) {
    const dot = document.getElementById('dot-detect');
    const txt = document.getElementById('stat-detect');
    if (!dot || !txt) return;
    txt.textContent = status.toUpperCase();
    if (status === "ACTIVE") {
        dot.className = 'dot green';
        txt.style.color = 'var(--green)';
    } else {
        dot.className = 'dot';
        txt.style.color = 'var(--text-dim)';
    }
}

function setFeedStatus(status) {
    const dot = document.getElementById('dot-feed');
    const txt = document.getElementById('stat-feed');
    if (!dot || !txt) return;
    txt.textContent = status.toUpperCase();
    if (status === "ONLINE") {
        dot.className = 'dot green';
        txt.style.color = 'var(--green)';
    } else if (status === "STREAMING") {
        dot.className = 'dot cyan';
        txt.style.color = 'var(--cyan)';
    } else {
        dot.className = 'dot';
        txt.style.color = 'var(--text-dim)';
    }
}

setInterval(pollStats, 1000);
pollStats();

// ─── API TEST BUTTON ─────────────────────────────────────────
document.getElementById('btn-test-api').addEventListener('click', () => {
    const c = appState.config;
    const url = 'http://' + (c.flaskHost === '0.0.0.0' ? 'localhost' : c.flaskHost) + ':' + c.flaskPort + '/api/lane_counts';
    toast('Testing: ' + url, 'cyan');
    fetch(url)
        .then(r => r.json())
        .then(data => {
            toast('API OK -- ' + JSON.stringify(data).slice(0, 60), 'green');
            addLog('OK', 'API test passed: ' + url);
        })
        .catch(err => {
            toast('API UNREACHABLE', 'red');
            addLog('ERR', 'API test failed: ' + url);
        });
});

// ─── INIT FEED ───────────────────────────────────────────────
updateLiveFeedSrc();

// ─── MODE SWITCHING ──────────────────────────────────────────
let currentMode = 1;

function switchMode(mode) {
    currentMode = mode;
    const canvas = document.getElementById('canvas');
    const mode2Panel = document.getElementById('mode2-panel');
    const tab1 = document.getElementById('mode-tab-1');
    const tab2 = document.getElementById('mode-tab-2');

    if (mode === 2) {
        canvas.style.display = 'none';
        mode2Panel.classList.add('active');
        tab1.classList.remove('active');
        tab2.classList.add('active');
        document.getElementById('sb-mode2').classList.add('active');
        loadM2ConfigFromBackend();
    } else {
        canvas.style.display = '';
        mode2Panel.classList.remove('active');
        tab1.classList.add('active');
        tab2.classList.remove('active');
        document.getElementById('sb-mode2').classList.remove('active');
    }
}

// Sidebar M2 button
document.getElementById('sb-mode2').addEventListener('click', () => {
    switchMode(currentMode === 2 ? 1 : 2);
});

// ─── MODE 2 INNER TABS ───────────────────────────────────────
function switchM2Tab(tab) {
    ['config', 'main'].forEach(t => {
        document.getElementById('m2-tab-' + t).classList.toggle('active', t === tab);
        document.getElementById('m2-section-' + t).classList.toggle('active', t === tab);
    });
}

// ─── MODE 2 SOURCE MANAGEMENT ────────────────────────────────
const M2_DIRS = ['North', 'South', 'East', 'West'];

function applyM2Source(direction) {
    const src = document.getElementById('m2-src-' + direction).value.trim();
    const payload = {};
    payload['src_' + direction.toLowerCase()] = src;

    fetch('/api/mode2_config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).then(r => r.json()).then(data => {
        if (data.status === 'success') {
            toast(direction + ' source applied', 'green');
            // Refresh stream src to trigger reload
            refreshM2Stream(direction);
            updateM2CardState(direction, !!src);
        } else {
            toast('Failed to apply ' + direction, 'red');
        }
    }).catch(() => toast('Network Error', 'red'));
}

function refreshM2Stream(direction) {
    const ts = Date.now();
    const prevImg = document.getElementById('m2-prev-' + direction);
    const mainImg = document.getElementById('m2-main-' + direction);
    const newSrc = `/video_feed/mode2/${direction}?t=${ts}`;
    if (prevImg) prevImg.src = newSrc;
    if (mainImg) mainImg.src = newSrc;

    // Badge update
    const badge = document.getElementById('m2-prev-badge-' + direction);
    const src = document.getElementById('m2-src-' + direction)?.value?.trim();
    if (badge) {
        badge.textContent = src ? 'LIVE' : 'NO SOURCE';
        badge.className = 'm2-prev-badge' + (src ? ' live' : '');
    }
}

function updateM2CardState(direction, isConfigured) {
    const card = document.getElementById('m2-card-' + direction);
    if (card) card.classList.toggle('configured', isConfigured);
}

document.getElementById('m2-save-all').addEventListener('click', () => {
    const payload = {};
    let anyFilled = false;
    M2_DIRS.forEach(d => {
        const val = document.getElementById('m2-src-' + d)?.value?.trim() || '';
        payload['src_' + d.toLowerCase()] = val;
        if (val) anyFilled = true;
    });

    fetch('/api/mode2_config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).then(r => r.json()).then(data => {
        toast('All sources saved', data.status === 'success' ? 'green' : 'red');
        if (data.status === 'success') {
            M2_DIRS.forEach(d => {
                const src = payload['src_' + d.toLowerCase()];
                refreshM2Stream(d);
                updateM2CardState(d, !!src);
            });
        }
    }).catch(() => toast('Network Error', 'red'));
});

function loadM2ConfigFromBackend() {
    fetch('/api/mode2_config')
        .then(r => r.json())
        .then(data => {
            if (!data.config) return;
            M2_DIRS.forEach(d => {
                const val = data.config[d] || '';
                const inp = document.getElementById('m2-src-' + d);
                if (inp) inp.value = val;
                updateM2CardState(d, !!val);
                const badge = document.getElementById('m2-prev-badge-' + d);
                if (badge) {
                    badge.textContent = val ? 'LIVE' : 'NO SOURCE';
                    badge.className = 'm2-prev-badge' + (val ? ' live' : '');
                }
            });
        })
        .catch(() => { });
}

// ─── MODE 2 SIGNAL SYNC (reuses existing poll data) ──────────
function updateMode2Visuals(data) {
    if (currentMode !== 2) return;
    const tlStates = data.tl_states || {};
    const m2counts = data.mode2_counts || {};
    const greenLane = data.green_lane;

    M2_DIRS.forEach(lane => {
        const state = tlStates[lane] || 'red';
        const count = m2counts[lane] !== undefined ? m2counts[lane] : (data.counts || {})[lane] || 0;

        // Traffic lights in mode 2
        ['red', 'yellow', 'green'].forEach(s => {
            const el = document.getElementById('m2-tl-' + s[0] + '-' + lane);
            if (el) el.className = 'm2-tl-light' + (s === state ? ' lit-' + s : '');
        });

        // Vehicle count
        const cntEl = document.getElementById('m2-cnt-' + lane);
        if (cntEl) cntEl.textContent = count;

        // Tile green-active highlight
        const tile = document.getElementById('m2-tile-' + lane);
        if (tile) tile.classList.toggle('green-active', lane === greenLane && state === 'green');
    });

    // Update Control Mode Button
    const mode = data.control_mode || 1;
    const btn = document.getElementById('m2-mode-toggle');
    if (btn) {
        if (mode === 2) {
            btn.textContent = 'RUSH PRIORITY';
            btn.classList.add('rush');
        } else {
            btn.textContent = 'FIXED CYCLE';
            btn.classList.remove('rush');
        }
    }
}

window.toggleControlMode = function () {
    const btn = document.getElementById('m2-mode-toggle');
    const currentIsRush = btn ? btn.classList.contains('rush') : false;
    const newMode = currentIsRush ? 1 : 2;

    fetch('/api/control_mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: newMode })
    }).then(r => r.json()).then(data => {
        toast('Control Mode: ' + (newMode === 2 ? 'RUSH PRIORITY' : 'FIXED CYCLE'), newMode === 2 ? 'amber' : 'cyan');
    }).catch(() => toast('Mode Update Failed', 'red'));
};

window.switchM2ConfigDir = function (dir) {
    const directions = ['North', 'West', 'South', 'East'];
    directions.forEach(d => {
        const tab = document.getElementById('m2-subtab-' + d);
        const card = document.getElementById('m2-card-' + d);
        if (tab) tab.classList.toggle('active', d === dir);
        if (card) card.classList.toggle('active', d === dir);
    });
    console.log(`[MODE2] Configuration switched to: ${dir}`);
};

// Hook into existing pollStats
const _origPollStats = pollStats;
// Patch: extend updateDashboardStats to also call mode2
const _origUpdateDash = updateDashboardStats;
updateDashboardStats = function (data) {
    _origUpdateDash(data);
    updateMode2Visuals(data);
};

// Load M2 config at startup (non-blocking)
loadM2ConfigFromBackend();

// ─── MODE 2 ROI DRAWING ──────────────────────────────────────
const m2RoiState = {};  // { direction: { points: [{x,y}], drawMode: bool, savedPts: [[x,y]] } }

M2_DIRS.forEach(d => {
    m2RoiState[d] = { points: [], drawMode: false, savedPts: null };
});

function _getM2Canvas(dir) {
    return document.getElementById('m2-roi-canvas-' + dir);
}

function _syncM2CanvasSize(dir) {
    const canvas = _getM2Canvas(dir);
    if (!canvas) return;
    const preview = canvas.closest('.m2-card-preview');
    if (!preview) return;
    canvas.width = preview.clientWidth;
    canvas.height = preview.clientHeight;
}

function _drawM2ROI(dir) {
    const canvas = _getM2Canvas(dir);
    if (!canvas) return;
    _syncM2CanvasSize(dir);
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const state = m2RoiState[dir];
    const img = document.getElementById('m2-prev-' + dir);
    const rect = getContainedImageBounds(img);

    // Draw saved ROI (always shown when in draw mode so user sees existing)
    if (state.savedPts && state.savedPts.length >= 3 && rect) {
        const sx = rect.w / (img.naturalWidth || 1);
        const sy = rect.h / (img.naturalHeight || 1);

        ctx.beginPath();
        ctx.moveTo(rect.x + state.savedPts[0][0] * sx, rect.y + state.savedPts[0][1] * sy);
        state.savedPts.forEach(p => {
            ctx.lineTo(rect.x + p[0] * sx, rect.y + p[1] * sy);
        });
        ctx.closePath();
        ctx.strokeStyle = 'rgba(0,232,122,0.75)';
        ctx.lineWidth = 2.5;
        ctx.stroke();
        ctx.fillStyle = 'rgba(0,232,122,0.15)';
        ctx.fill();
    }

    // Draw in-progress points
    const pts = state.points;
    pts.forEach((pt, i) => {
        ctx.beginPath();
        ctx.arc(pt.x, pt.y, 5, 0, Math.PI * 2);
        ctx.fillStyle = '#f5c400';
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.fillStyle = '#fff';
        ctx.font = '10px "Share Tech Mono"';
        ctx.fillText(i + 1, pt.x + 8, pt.y + 9);
    });

    if (pts.length > 1) {
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].y);
        pts.forEach(p => ctx.lineTo(p.x, p.y));
        if (pts.length === 4) ctx.closePath();
        ctx.strokeStyle = '#f5c400';
        ctx.lineWidth = 2;
        ctx.stroke();
    }

    // Status text
    const statusEl = document.getElementById('m2-roi-status-' + dir);
    if (statusEl) {
        if (pts.length > 0) {
            const rem = 4 - pts.length;
            statusEl.textContent = rem > 0 ? rem + ' MORE POINT(S)' : 'READY — SAVE ROI';
            statusEl.style.color = rem > 0 ? 'var(--yellow)' : 'var(--green)';
        } else if (state.savedPts) {
            statusEl.textContent = 'ROI ACTIVE';
            statusEl.style.color = 'var(--green)';
        } else {
            statusEl.textContent = 'NO ROI SET';
            statusEl.style.color = 'var(--text-dim)';
        }
    }
}

function toggleM2ROIDraw(dir) {
    const state = m2RoiState[dir];
    state.drawMode = !state.drawMode;
    state.points = [];  // reset in-progress on toggle

    const canvas = _getM2Canvas(dir);
    const btn = document.getElementById('m2-roi-draw-' + dir);
    if (!canvas) return;

    if (state.drawMode) {
        _syncM2CanvasSize(dir);
        canvas.classList.add('drawing');
        if (btn) btn.classList.add('active');
        _drawM2ROI(dir);
        toast(dir + ': Click 4 points on preview to draw ROI', 'yellow');
    } else {
        canvas.classList.remove('drawing');
        if (btn) btn.classList.remove('active');
    }
}

function clearM2ROI(dir) {
    const state = m2RoiState[dir];
    state.points = [];
    state.savedPts = null;

    const canvas = _getM2Canvas(dir);
    if (canvas) {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    const statusEl = document.getElementById('m2-roi-status-' + dir);
    if (statusEl) { statusEl.textContent = 'NO ROI SET'; statusEl.style.color = 'var(--text-dim)'; }

    // Clear on backend too
    fetch('/api/mode2_rois', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lane: dir, points: [] })
    }).then(r => r.json()).then(() => toast(dir + ' ROI cleared', 'yellow'))
        .catch(() => toast('Network Error', 'red'));
}

function saveM2ROI(dir) {
    const state = m2RoiState[dir];
    if (state.points.length !== 4) {
        toast('Need exactly 4 points for ' + dir + ' ROI', 'red');
        return;
    }

    const canvas = _getM2Canvas(dir);
    const preview = canvas ? canvas.closest('.m2-card-preview') : null;
    const img = document.getElementById('m2-prev-' + dir);

    // Scale from display point (relative to whole canvas) to natural image pixels
    const rect = getContainedImageBounds(img);
    if (!rect) {
        toast('Preview not ready', 'red');
        return;
    }

    const pts = state.points.map(p => [
        Math.round((p.x - rect.x) * (img.naturalWidth / rect.w)),
        Math.round((p.y - rect.y) * (img.naturalHeight / rect.h))
    ]);

    // Keep natural-space copy for rendering
    state.savedPts = pts;
    state.points = [];
    state.drawMode = false;

    const cvs = _getM2Canvas(dir);
    const btn = document.getElementById('m2-roi-draw-' + dir);
    if (cvs) cvs.classList.remove('drawing');
    if (btn) btn.classList.remove('active');
    _drawM2ROI(dir);

    fetch('/api/mode2_rois', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lane: dir, points: pts })
    }).then(r => r.json()).then(data => {
        toast(data.message || (dir + ' ROI saved'), data.status === 'success' ? 'green' : 'red');
    }).catch(() => toast('Network Error', 'red'));
}

// Wire canvas click events
M2_DIRS.forEach(dir => {
    const canvas = _getM2Canvas(dir);
    if (!canvas) return;
    canvas.addEventListener('mousedown', e => {
        const state = m2RoiState[dir];
        if (!state.drawMode || state.points.length >= 4) return;
        const rect = canvas.getBoundingClientRect();
        state.points.push({ x: Math.round(e.clientX - rect.left), y: Math.round(e.clientY - rect.top) });
        _drawM2ROI(dir);
    });
});

// Load saved ROIs from backend and render
function loadM2ROIsFromBackend() {
    fetch('/api/mode2_rois')
        .then(r => r.json())
        .then(data => {
            if (!data.rois) return;
            M2_DIRS.forEach(dir => {
                const pts = data.rois[dir];
                if (pts && pts.length >= 3) {
                    const state = m2RoiState[dir];
                    const canvas = _getM2Canvas(dir);
                    const preview = canvas ? canvas.closest('.m2-card-preview') : null;
                    const img = document.getElementById('m2-prev-' + dir);

                    // Convert natural coords back to display-space
                    state.savedPts = pts; // We store national coords now to handle resizing correctly

                    const statusEl = document.getElementById('m2-roi-status-' + dir);
                    if (statusEl) { statusEl.textContent = 'ROI ACTIVE'; statusEl.style.color = 'var(--green)'; }

                    // Show on canvas (needs to be visible)
                    if (canvas) {
                        _syncM2CanvasSize(dir);
                        canvas.classList.add('drawing');
                        _drawM2ROI(dir);
                        // Keep canvas visible to show roi overlay permanently
                    }
                }
            });
        })
        .catch(() => { });
}

// Load ROIs when entering Mode 2
const _origSwitchMode = switchMode;
switchMode = function (mode) {
    _origSwitchMode(mode);
    if (mode === 2) {
        setTimeout(loadM2ROIsFromBackend, 300);
    }
};

