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
        carlaHost: 'localhost',
        carlaPort: 2000,
        timeout: 10,
        yolo: 'yolov8n.pt',
        flaskHost: '0.0.0.0',
        flaskPort: 5000,
        feedUrl: ''
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
    document.getElementById('cfg-carla-host').value = c.carlaHost || 'localhost';
    document.getElementById('cfg-carla-port').value = c.carlaPort || 2000;
    document.getElementById('cfg-timeout').value = c.timeout || 10;
    document.getElementById('cfg-yolo').value = c.yolo || 'yolov8n.pt';
    document.getElementById('cfg-flask-host').value = c.flaskHost || '0.0.0.0';
    document.getElementById('cfg-flask-port').value = c.flaskPort || 5000;
    document.getElementById('cfg-feed-url').value = c.feedUrl || '';
    updateTopbarFromConfig();
    updateApiEndpoints();
}

function saveConfigFromForm() {
    appState.config.carlaHost = document.getElementById('cfg-carla-host').value.trim();
    appState.config.carlaPort = parseInt(document.getElementById('cfg-carla-port').value, 10);
    appState.config.timeout = parseFloat(document.getElementById('cfg-timeout').value);
    appState.config.yolo = document.getElementById('cfg-yolo').value.trim();
    appState.config.flaskHost = document.getElementById('cfg-flask-host').value.trim();
    appState.config.flaskPort = parseInt(document.getElementById('cfg-flask-port').value, 10);
    appState.config.feedUrl = document.getElementById('cfg-feed-url').value.trim();
    saveState();
    updateTopbarFromConfig();
    updateApiEndpoints();
    updateLiveFeedSrc();
}

function updateTopbarFromConfig() {
    const c = appState.config;
    document.getElementById('tb-host').textContent = (c.carlaHost || 'localhost') + ':' + (c.carlaPort || 2000);
}

function updateApiEndpoints() {
    const c = appState.config;
    const base = 'http://' + (c.flaskHost === '0.0.0.0' ? 'localhost' : c.flaskHost) + ':' + c.flaskPort;
    document.getElementById('api-ep-feed').textContent = base + '/api/live_feed';
    document.getElementById('api-ep-counts').textContent = base + '/api/lane_counts';
    document.getElementById('api-ep-cam').textContent = base + '/api/camera/status';
}

function updateLiveFeedSrc() {
    const c = appState.config;
    let feed = c.feedUrl || '/video_feed';
    if (feed && !feed.startsWith('/') && !feed.includes('://')) {
        feed = 'http://' + feed + '/api/live_feed';
    }
    const liveImg = document.getElementById('live-img');
    const roiImg = document.getElementById('roi-img');
    if (liveImg) liveImg.src = feed;
    if (roiImg) roiImg.src = feed;
}

document.getElementById('btn-save-connect').addEventListener('click', () => {
    saveConfigFromForm();
    toast('Config saved — attempting connection...', 'cyan');
    addLog('INFO', 'Config saved: ' + appState.config.carlaHost + ':' + appState.config.carlaPort);

    // POST properly to server instead of simulating
    fetch('/panel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: 'save_connect',
            carla_host: appState.config.carlaHost,
            carla_port: appState.config.carlaPort,
            carla_timeout: appState.config.timeout,
            yolo_model: appState.config.yolo,
            flask_host: appState.config.flaskHost,
            flask_port: appState.config.flaskPort,
            live_feed_url: appState.config.feedUrl
        })
    }).then(r => r.json()).then(data => {
        if (data.status === 'success') {
            toast('CARLA connection request sent', 'green');
            addLog('OK', 'Connect request dispatched');
            document.getElementById('sb-conn').classList.add('connected');
        } else {
            toast('Failed to save config', 'red');
            addLog('ERR', 'Could not save configurations.');
        }
    }).catch(() => {
        toast('Network Error', 'red');
    });
});

loadConfigToForm();

// ─── TRAFFIC LIGHT MAPPING ───────────────────────────────────
function loadTLForm() {
    ['North', 'South', 'East', 'West'].forEach(lane => {
        const el = document.getElementById('tl-' + lane.toLowerCase());
        if (el) el.value = appState.tlIds[lane] || '';
        const idEl = document.getElementById('tl-id-' + lane);
        if (idEl) idEl.textContent = appState.tlIds[lane] ? '#' + appState.tlIds[lane] : '--';
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

    // POST to backend
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
        if (data.status === 'success') {
            toast('Traffic light bindings updated', 'green');
            addLog('OK', 'TL actor IDs updated: N=' + appState.tlIds.North +
                ' S=' + appState.tlIds.South + ' E=' + appState.tlIds.East + ' W=' + appState.tlIds.West);
        } else {
            toast('Failed to update bindngs', 'red');
        }
    }).catch(() => toast('Network Error', 'red'));
});

loadTLForm();

// ─── ROI DRAWING ─────────────────────────────────────────────
const roiCanvas = document.getElementById('roi-canvas');
const roiCtx = roiCanvas ? roiCanvas.getContext('2d') : null;
let roiPoints = [];

function resizeROICanvas() {
    const img = document.getElementById('roi-img');
    if (!roiCanvas || !img) return;
    roiCanvas.width = img.offsetWidth;
    roiCanvas.height = img.offsetHeight;
    drawROIScene();
}

const roiImg = document.getElementById('roi-img');
if (roiImg) roiImg.addEventListener('load', () => setTimeout(resizeROICanvas, 100));
window.addEventListener('resize', resizeROICanvas);
setTimeout(resizeROICanvas, 600);

function drawROIScene() {
    if (!roiCtx) return;
    roiCtx.clearRect(0, 0, roiCanvas.width, roiCanvas.height);

    // Draw saved ROIs
    Object.entries(appState.rois).forEach(([lane, pts]) => {
        if (!pts || pts.length < 4) return;
        // Scale from natural to display
        const img = document.getElementById('roi-img');
        const sx = roiCanvas.width / (img.naturalWidth || roiCanvas.width);
        const sy = roiCanvas.height / (img.naturalHeight || roiCanvas.height);

        roiCtx.beginPath();
        roiCtx.moveTo(pts[0][0] * sx, pts[0][1] * sy);
        for (let i = 1; i < pts.length; i++) roiCtx.lineTo(pts[i][0] * sx, pts[i][1] * sy);
        roiCtx.closePath();
        roiCtx.strokeStyle = 'rgba(0,212,245,0.5)';
        roiCtx.lineWidth = 2;
        roiCtx.stroke();
        roiCtx.fillStyle = 'rgba(0,212,245,0.06)';
        roiCtx.fill();

        roiCtx.fillStyle = '#00d4f5';
        roiCtx.font = 'bold 11px "Share Tech Mono"';
        roiCtx.fillText(lane.toUpperCase(), pts[0][0] * sx + 4, pts[0][1] * sy - 6);
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
    // Convert display coords to natural
    const sx = (img.naturalWidth || roiCanvas.width) / roiCanvas.width;
    const sy = (img.naturalHeight || roiCanvas.height) / roiCanvas.height;
    const pts = roiPoints.map(p => [Math.round(p.x * sx), Math.round(p.y * sy)]);

    // Update local state temporarily
    appState.rois[lane] = pts;
    roiPoints = [];
    drawROIScene();

    const s = document.getElementById('roi-status');
    if (s) s.textContent = 'SAVING ' + lane.toUpperCase() + ' ROI TO DB...';

    // Post to server
    fetch('/roi_panel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lane, points: pts })
    }).then(r => r.json()).then(data => {
        if (data.status === 'success') {
            saveState();
            toast('ROI updated in Database for ' + lane, 'green');
            addLog('OK', 'ROI DB config saved: ' + lane);
            if (s) s.textContent = 'ROI FOR ' + lane.toUpperCase() + ' SYNCED TO DB';
        } else {
            toast('Failed to save ROI to DB', 'red');
            addLog('ERR', 'ROI DB Sync failed');
        }
    }).catch(() => {
        toast('Network Error during ROI save', 'red');
    });
});

// Load external ROIs from db on Init
function loadExternalROIs() {
    fetch('/roi_panel')
        .then(r => r.json())
        .then(data => {
            if (data.current_rois) {
                appState.rois = data.current_rois;
                drawROIScene();
                saveState();
                addLog('INFO', 'Loaded ROIs from database automatically');
            }
        })
        .catch(err => console.error("Could not fetch initial ROIs", err));
}

loadExternalROIs();

// ─── LIVE STATS POLLING ──────────────────────────────────────
let phaseMax = 30;

function updateDashboardStats(data) {
    const counts = data.counts || {};
    const greenLane = data.green_lane || null;
    const timer = data.timer ?? '--';

    ['North', 'South', 'East', 'West'].forEach(lane => {
        const countEl = document.getElementById('count-' + lane);
        if (countEl) countEl.textContent = counts[lane] ?? 0;
        const card = document.getElementById('lane-card-' + lane);
        if (card) card.classList.toggle('active', lane === greenLane);
    });

    // Topbar
    const total = Object.values(counts).reduce((s, v) => s + (v || 0), 0);
    document.getElementById('tb-total-veh').textContent = total;
    document.getElementById('tb-phase-lane').textContent = greenLane || '--';
    document.getElementById('tb-cycle-timer').textContent = timer + 's';

    // Phase bar
    const phTimer = parseFloat(timer);
    if (!isNaN(phTimer)) {
        if (phTimer > phaseMax) phaseMax = phTimer;
        const pct = Math.max(0, Math.min(100, (phTimer / phaseMax) * 100));
        document.getElementById('ph-bar').style.width = pct + '%';
    }
    document.getElementById('ph-timer').textContent = timer + 's';
    document.getElementById('ph-lane').textContent = greenLane || '--';

    // Update TL visuals
    ['North', 'South', 'East', 'West'].forEach(lane => {
        const isGreen = lane === greenLane;
        setTLState(lane, isGreen ? 'green' : 'red');
    });

    // Timer color
    const timerEl = document.getElementById('tb-cycle-timer');
    timerEl.className = 'tb-stat-value ' + (phTimer <= 5 ? 'yellow' : '');
}

function setTLState(lane, state) {
    const states = ['red', 'yellow', 'green'];
    states.forEach(s => {
        const el = document.getElementById('tl-' + s[0] + '-' + lane);
        if (!el) return;
        el.className = 'tl-light' + (s === state ? ' lit-' + s : '');
    });
}

function pollStats() {
    fetch('/api/lane_counts')
        .then(r => r.json())
        .then(data => {
            updateDashboardStats(data);
            // update CARLA status display
            setCarlaStatus(true);
        })
        .catch(() => {
            setCarlaStatus(false);
        });
}

function setCarlaStatus(online) {
    const dot = document.getElementById('dot-carla');
    const txt = document.getElementById('stat-carla');
    const tbEl = document.getElementById('tb-carla-status');
    if (online) {
        dot.className = 'dot green';
        txt.textContent = 'ONLINE';
        txt.style.color = 'var(--green)';
        tbEl.className = 'tb-stat-value green';
        tbEl.textContent = 'ONLINE';
    } else {
        dot.className = 'dot red';
        txt.textContent = 'OFFLINE';
        txt.style.color = 'var(--red)';
        tbEl.className = 'tb-stat-value red';
        tbEl.textContent = 'OFFLINE';
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