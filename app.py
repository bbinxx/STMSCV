import cv2
import requests
import numpy as np
import threading
import queue
import time
import json
import os
import sqlite3
import urllib.request
import logging
from ultralytics import YOLO
from flask import Flask, Response, render_template, request, redirect, url_for, jsonify

# Suppress Flask access logs
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

from detection import process_frame, control_traffic_lights_logic, get_automation_data, DETECTION_IMG_SIZE

app = Flask(__name__)

# --- Configuration & Shared State ---
# Setup SQLite Database
DB_FILE = "traffic_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS rois
                 (lane TEXT PRIMARY KEY, points TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS config
                 (key TEXT PRIMARY KEY, value TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tls
                 (lane TEXT PRIMARY KEY, actor_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS roi_sets
                 (name TEXT PRIMARY KEY, config TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS mode2_cameras
                 (lane TEXT PRIMARY KEY, source TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS mode2_rois
                 (lane TEXT PRIMARY KEY, points TEXT)''')
    conn.commit()
    conn.close()

init_db()

def load_config():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT key, value FROM config")
    rows = c.fetchall()
    conn.close()
    
    config_data = {
        "controller_host": "",
        "controller_port": "",
        "controller_timeout": "",
        "yolo_model": "",
        "flask_host": "0.0.0.0",
        "flask_port": 5050,
        "live_feed_url": "",
        "cycle_timer": 30
    }
    for k, v in rows:
        # Keep backward compatibility with old names while normalizing to controller_* keys.
        try:
            if k in ["controller_port", "thorulf_port", "control_port", "flask_port", "cycle_timer"] and v is not None and str(v).strip():
                config_data[k] = int(v)
            elif k in ["controller_timeout", "thorulf_timeout", "control_timeout"] and v is not None and str(v).strip():
                config_data["controller_timeout"] = float(v)
            elif k in ["controller_host", "thorulf_host", "control_host"]:
                config_data["controller_host"] = v
            elif k == "controller_port" or k == "thorulf_port" or k == "control_port":
                if v is not None and str(v).strip():
                    try:
                        config_data["controller_port"] = int(v)
                    except ValueError:
                        pass
            elif k == "controller_timeout" or k == "thorulf_timeout" or k == "control_timeout":
                if v is not None and str(v).strip():
                    try:
                        config_data["controller_timeout"] = float(v)
                    except ValueError:
                        pass
            else:
                config_data[k] = v
        except (ValueError, TypeError):
            config_data[k] = v
    return config_data

def save_config(config_data):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for k, v in config_data.items():
        c.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (k, str(v)))
    conn.commit()
    conn.close()

config = load_config()

# YOLOv8 model loading helper
model = None
def load_yolo_model(path):
    global model
    if not path or not str(path).strip():
        model = None
        print("[INIT] YOLO model path is empty, detection DISABLED", flush=True)
        return False
    
    # resolve path relative to script directory
    base_dir = os.path.dirname(os.path.abspath(__file__))
    res_path = os.path.abspath(os.path.join(base_dir, path))

    print(f"[INIT] YOLO detection image size set to {DETECTION_IMG_SIZE}", flush=True)
    
    try:
        # If model doesn't exist at specific path, try loading by name (YOLO auto-downloads)
        if not os.path.exists(res_path):
            print(f"[WARN] YOLO model file not found at: {res_path}", flush=True)
            print("[INFO] Attempting to auto-download model by name...", flush=True)
            model_name = os.path.basename(path)
            model = YOLO(model_name)
        else:
            try:
                model = YOLO(res_path)
            except Exception as load_err:
                print(f"[ERR] Model file corrupted or invalid: {res_path}. Deleting and retrying download.", flush=True)
                if os.path.exists(res_path):
                    os.remove(res_path)
                model_name = os.path.basename(path)
                model = YOLO(model_name)

        # WARM UP: Run dummy inference to trigger model fusion in main thread.
        # This prevents 'AttributeError: bn' when multiple threads predict() simultaneously.
        if model is not None:
            try:
                # if a GPU is available, move model to it for faster inference
                import torch
                if torch.cuda.is_available():
                    model.to('cuda:0')
                    print("[INIT] YOLO model moved to CUDA:0", flush=True)
                # fuse conv+bns to speed up realtime inference
                try:
                    model.fuse()
                    print("[INIT] YOLO model fused for speed", flush=True)
                except Exception:
                    pass
            except ImportError:
                pass

            # WARM UP: small dummy prediction to finish initialization
            dummy = np.zeros((64, 64, 3), dtype=np.uint8)
            model.predict(dummy, verbose=False)
            print(f"[INIT] YOLO model loaded and warmed up: {path}", flush=True)
            
        return True
    except Exception as e:
        print(f"[ERR] Failed to load or download YOLO ({path}): {e}", flush=True)
        model = None
        return False

# Initial load
load_yolo_model(config.get("yolo_model", ""))
# live-feed thread will be started lazily inside the vision_processing_loop when
# the loop first runs.  doing it here before _start_external_feed is defined would
# raise a NameError if the URL had been saved earlier.

# Thread-safe queue for CONTROL camera images (and optionally external feed)
image_queue = queue.Queue(maxsize=1)

# External feed support (config.live_feed_url)
external_feed_thread = None        # current worker thread
external_feed_src = ""           # URL being read by worker

# Global states for Flask streaming and tracking
latest_frame = None
lane_counts = {"North": 0, "South": 0, "East": 0, "West": 0}
last_process_time = 0.0

# Global blank frame for standby
_blank_img = np.zeros((360, 640, 3), np.uint8)
cv2.putText(_blank_img, "SIGNAL STANDBY", (210, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 70, 255), 2)
_, _blank_jpeg = cv2.imencode('.jpg', _blank_img)
BLANK_FRAME_BYTES = _blank_jpeg.tobytes()

connection_status = "Disconnected"
system_started = False

# Checks for required runtime configuration before starting

def check_start_prereqs():
    """Only requires YOLO model; controller connectivity is optional."""
    missing = []
    if not config.get('yolo_model'):
        missing.append('yolo_model (set in CON panel)')
    if config.get('yolo_model') and model is None:
        missing.append('yolo_model not loaded')
    return missing

# Global automation state now handled by detection module

# --- MODE 2: Per-direction camera sources, ROIs & frames ---
DIRECTIONS = ['North', 'South', 'East', 'West']

# ── camera config ─────────────────────────────────────────────
def load_mode2_cameras():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT lane, source FROM mode2_cameras")
    rows = c.fetchall()
    conn.close()
    data = {d: '' for d in DIRECTIONS}
    for lane, source in rows:
        data[lane] = source
    return data

def save_mode2_cameras(cam_cfg):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM mode2_cameras")
    for lane, source in cam_cfg.items():
        if source:
            c.execute("INSERT INTO mode2_cameras (lane, source) VALUES (?, ?)", (lane, source))
    conn.commit()
    conn.close()

# ── ROI config ────────────────────────────────────────────────
def load_mode2_rois():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT lane, points FROM mode2_rois")
    rows = c.fetchall()
    conn.close()
    data = {}
    for lane, pts_str in rows:
        try:
            data[lane] = np.array(json.loads(pts_str), np.int32)
        except Exception:
            pass
    return data

def save_mode2_rois(rois_dict):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Clear and re-save to handle deletions
    c.execute("DELETE FROM mode2_rois")
    for lane, pts in rois_dict.items():
        pts_list = pts.tolist() if isinstance(pts, np.ndarray) else pts
        c.execute("INSERT INTO mode2_rois (lane, points) VALUES (?, ?)",
                  (lane, json.dumps(pts_list)))
    conn.commit()
    conn.close()

mode2_cam_config = load_mode2_cameras()   # {lane: source_str}
mode2_rois       = load_mode2_rois()      # {lane: np.array (4,2)}
mode2_frames     = {d: None for d in DIRECTIONS}  # {lane: jpeg_bytes (with detection overlay)}
mode2_raw_frames = {}                     # {lane: raw np frame (for detection)}
mode2_counts     = {d: 0 for d in DIRECTIONS}  # {lane: int}
mode2_scores     = {d: 0 for d in DIRECTIONS}  # {lane: int} weighted scores
mode2_captures   = {}  # {lane: cv2.VideoCapture or urllib stream}
mode2_threads    = {}  # {lane: Thread}

def _make_blank_dir_frame(direction):
    img = np.zeros((360, 640, 3), np.uint8)
    cv2.putText(img, f'{direction.upper()} -- NO SOURCE', (160, 180),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (40, 80, 120), 2)
    _, buf = cv2.imencode('.jpg', img)
    return buf.tobytes()

for _d in DIRECTIONS:
    mode2_frames[_d] = _make_blank_dir_frame(_d)

def _is_mjpeg_url(src):
    """Returns True if source looks like an HTTP/HTTPS MJPEG stream."""
    s = str(src).lower()
    return s.startswith('http://') or s.startswith('https://') or '127.0.0.1' in s or 'localhost' in s

def _read_mjpeg_url(direction, src):
    """
    Generator: yields decoded BGR frames from an MJPEG-over-HTTP stream.
    Handles multipart/x-mixed-replace boundaries (e.g. /video_feed?id=27).
    Falls back to cv2.VideoCapture for generic HTTP streams.
    """
    print(f"[MODE2] Connecting MJPEG URL for {direction}: {src}", flush=True)
    buf = b''
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (STMCV Dashboard)',
            'Accept': 'multipart/x-mixed-replace,image/jpeg,image/*,*/*;q=0.8',
            'Connection': 'keep-alive'
        }
        req = urllib.request.Request(src, headers=headers)
        stream = urllib.request.urlopen(req, timeout=10)
        while True:
            # Stop if source changed
            src_now = mode2_cam_config.get(direction, '').strip()
            if src_now != src:
                return
            chunk = stream.read(4096)
            if not chunk:
                return
            buf += chunk
            # JPEG start/end markers
            a = buf.find(b'\xff\xd8')
            b_ = buf.find(b'\xff\xd9')
            if a != -1 and b_ != -1 and b_ > a:
                jpg = buf[a:b_+2]
                buf = buf[b_+2:]
                frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    yield frame
    except Exception as e:
        print(f"[MODE2] MJPEG URL error for {direction}: {e}", flush=True)
        return

def _apply_detection_overlay(direction, frame):
    """
    Runs YOLO detection on `frame` within the direction's ROI (if set).
    Updates mode2_counts[direction]. Returns annotated frame bytes.
    """
    global model, mode2_rois, mode2_counts, mode2_scores, last_process_time

    last_process_time = time.time()

    roi_map = {}
    if direction in mode2_rois:
        roi_map[direction] = mode2_rois[direction]

    annotated, counts, scores = process_frame(frame.copy(), model, roi_map)
    if roi_map:
        mode2_counts[direction] = counts.get(direction, 0)
        mode2_scores[direction] = scores.get(direction, 0)
    else:
        mode2_counts[direction] = 0
        mode2_scores[direction] = 0

    _, buf = cv2.imencode('.jpg', annotated)
    return buf.tobytes()

def mode2_capture_loop(direction):
    """Background thread: reads frames from any source for one direction with retries."""
    global mode2_frames, mode2_captures, mode2_raw_frames

    print(f"[MODE2] Thread started for {direction}", flush=True)

    while True:
        src = mode2_cam_config.get(direction, '').strip()
        if not src:
            mode2_frames[direction] = _make_blank_dir_frame(direction)
            time.sleep(1.0)
            continue

        # Try to open source
        try:
            try:
                src_val = int(src)
            except ValueError:
                src_val = src

            cap = cv2.VideoCapture(src_val)
            mode2_captures[direction] = cap

            if not cap.isOpened():
                # Fallback for manual MJPEG if OpenCV fails on a URL
                if _is_mjpeg_url(src):
                    print(f"[MODE2] OpenCV failed, trying manual MJPEG for {direction}: {src}", flush=True)
                    for frame in _read_mjpeg_url(direction, src):
                        mode2_raw_frames[direction] = frame
                        mode2_frames[direction] = _apply_detection_overlay(direction, frame)
                        # Check if source changed while streaming
                        if mode2_cam_config.get(direction, '').strip() != src: break
                    
                print(f"[MODE2] Connection lost/failed for {direction}: {src}. Retrying in 2s...", flush=True)
                mode2_frames[direction] = _make_blank_dir_frame(direction)
                cap.release()
                mode2_captures.pop(direction, None)
                time.sleep(2.0)
                continue

            print(f"[MODE2] Capture active for {direction}: {src}", flush=True)
            while True:
                # Check if source changed in config
                src_now = mode2_cam_config.get(direction, '').strip()
                if src_now != src:
                    break

                ret, frame = cap.read()
                if not ret:
                    # Video file loop or temporary glitch
                    if isinstance(src_val, str) and not _is_mjpeg_url(src_val):
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        time.sleep(0.05)
                        continue
                    else:
                        break # Break to outer loop for retry

                mode2_raw_frames[direction] = frame
                mode2_frames[direction] = _apply_detection_overlay(direction, frame)
                time.sleep(0.03)

            cap.release()
            mode2_captures.pop(direction, None)
            
        except Exception as e:
            print(f"[MODE2] Loop error for {direction}: {e}", flush=True)
            time.sleep(2.0)

    print(f"[MODE2] Thread exiting for {direction}", flush=True)

def start_mode2_direction(direction):
    """Start (or restart) the capture thread for a direction."""
    # Check if thread is already running
    if direction in mode2_threads and mode2_threads[direction].is_alive():
        return

    t = threading.Thread(target=mode2_capture_loop, args=(direction,), daemon=True, name=f"Mode2_{direction}")
    mode2_threads[direction] = t
    t.start()

def generate_mode2_stream(direction):
    while True:
        frame = mode2_frames.get(direction)
        if frame:
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        time.sleep(0.04)

# --- Dynamic Settings Configurations ---
def load_rois():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT lane, points FROM rois")
    rows = c.fetchall()
    conn.close()
    
    if rows:
        rois = {}
        for lane, points_str in rows:
            rois[lane] = np.array(json.loads(points_str), np.int32)
        return rois
        
    # Default fallback if DB is empty
    return {}

def save_rois():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for lane, points in ROIS.items():
        c.execute("INSERT OR REPLACE INTO rois (lane, points) VALUES (?, ?)",
                  (lane, json.dumps(points.tolist())))
    conn.commit()
    conn.close()

def load_tls():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT lane, actor_id FROM tls")
    rows = c.fetchall()
    conn.close()
    
    tls_data = {"North": None, "South": None, "East": None, "West": None}
    if rows:
        for lane, actor_id in rows:
            tls_data[lane] = int(actor_id) if actor_id is not None else None
    return tls_data

def save_tls(tls_data):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for lane, actor_id in tls_data.items():
        c.execute("INSERT OR REPLACE INTO tls (lane, actor_id) VALUES (?, ?)",
                  (lane, actor_id))
    conn.commit()
    conn.close()

ROIS = load_rois()
TL_IDS = load_tls()
ROI_ENABLED = True

# --- 1. NATIVE CONTROL VIDEO INPUT ---
def get_controller_url():
    host = config.get("controller_host", "localhost")
    port = config.get("controller_port", 5000)
    return f"http://{host}:{port}" if host else None


def check_controller_connection():
    global connection_status
    connection_status = "Connecting..."
    host = config.get("controller_host", "localhost")
    port = config.get("controller_port", 5000)
    if not host:
        connection_status = "Waiting for configuration in Controller API Panel"
        return
    url = f"http://{host}:{port}/traffic_lights/all"
    try:
        req = requests.get(url, timeout=3.0)
        req.raise_for_status()
        if not is_live_feed_ready():
            connection_status = "Requires LIVE FEED"
            print("[HMI] Controller connection OK but live feed not ready", flush=True)
        else:
            connection_status = "Connected"
            print(f"[HMI] Connected to controller API at {host}:{port}", flush=True)
    except Exception as e:
        connection_status = "Failed to connect (Check controller API is running)"
        print(f"[ERR] Failed to connect to controller API: {e}", flush=True)

def is_live_feed_ready():
    if config.get('live_feed_url', '').strip():
        return external_feed_thread is not None and external_feed_thread.is_alive()
    return latest_frame is not None


def disconnect_controller():
    global connection_status
    connection_status = "Disconnected"
    print("[HMI] Disconnected from controller API")

def bg_connect_controller():
    threading.Thread(target=check_controller_connection, daemon=True).start()


# --- EXTERNAL / API BASED LIVE FEED SUPPORT ---

def _read_external_mjpeg(src):
    """Generator that yields frames from a multipart MJPEG HTTP stream."""
    buf = b''
    try:
        req = urllib.request.Request(src, headers={'User-Agent': 'Mozilla/5.0 (STMCV Dashboard)'})
        stream = urllib.request.urlopen(req, timeout=10)
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            buf += chunk
            a = buf.find(b'\xff\xd8')
            b_ = buf.find(b'\xff\xd9')
            if a != -1 and b_ != -1 and b_ > a:
                jpg = buf[a:b_+2]
                buf = buf[b_+2:]
                frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    yield frame
    except Exception as e:
        print(f"[EXT] MJPEG read error: {e}", flush=True)
        return


def _start_external_feed(src):
    """Launch or restart the thread reading from `src` and pushing into image_queue."""
    global external_feed_thread, external_feed_src
    # signal any existing thread to stop by changing source
    external_feed_src = src or ""
    if external_feed_thread and external_feed_thread.is_alive():
        # worker will exit when it sees external_feed_src changed
        pass

    def worker():
        global external_feed_src
        current_src = src
        cap = None
        print(f"[EXT] thread starting for {current_src}", flush=True)
        while external_feed_src == current_src and current_src.strip():
            try:
                # attempt to open capture (numeric index or URL)
                try:
                    idx = int(current_src)
                except Exception:
                    idx = current_src

                cap = cv2.VideoCapture(idx)
                if not cap.isOpened():
                    # fallback to manual MJPEG if URL looks like one
                    if _is_mjpeg_url(current_src):
                        for frame in _read_external_mjpeg(current_src):
                            if external_feed_src != current_src:
                                break
                            if image_queue.full():
                                try: image_queue.get_nowait()
                                except queue.Empty: pass
                            image_queue.put(frame)
                            time.sleep(0.03)
                        continue
                    # otherwise wait and retry
                    print(f"[EXT] failed to open {current_src}, retrying", flush=True)
                    time.sleep(2)
                    continue

                # read loop
                while external_feed_src == current_src:
                    ret, frame = cap.read()
                    if not ret:
                        # if it's a file, loop it
                        if isinstance(idx, str) and not _is_mjpeg_url(idx):
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            time.sleep(0.05)
                            continue
                        break
                    if image_queue.full():
                        try: image_queue.get_nowait()
                        except queue.Empty: pass
                    image_queue.put(frame)
                    time.sleep(0.03)
                cap.release()
                cap = None
                time.sleep(1)
            except Exception as e:
                print(f"[EXT] error reading {current_src}: {e}", flush=True)
                time.sleep(2)
        if cap:
            try: cap.release()
            except: pass
        print(f"[EXT] thread stopping for {current_src}", flush=True)

    external_feed_thread = threading.Thread(target=worker, daemon=True)
    external_feed_thread.start()


def stop_external_feed():
    global external_feed_src
    external_feed_src = ""  # signal worker to exit

# --- 4. TRAFFIC LIGHT CONTROL HELPERS ---
def get_traffic_lights(world):
    # Legacy helper placeholder. We rely on Thorulf API endpoints for TL IDs and states.
    return []

# --- 3. YOLOv8 CV ENGINE & PROCESSING LOOP ---
def vision_processing_loop():
    """CONTROL camera mode: process frames and control lights"""
    global latest_frame, lane_counts, last_process_time, system_started
    print("[INIT] Vision Processing Thread Started", flush=True)
    
    while True:
        try:
            if not system_started:
                time.sleep(0.5)
                continue
            # make sure external feed thread is alive when a URL is configured
            if config.get('live_feed_url', '').strip():
                if external_feed_thread is None or not external_feed_thread.is_alive():
                    _start_external_feed(config['live_feed_url'])

            if not image_queue.empty():
                img_data = image_queue.get()
                if img_data is None:
                    continue
                frame = img_data.copy()
                
                rois_to_use = ROIS.copy() if ROI_ENABLED else {}
                frame, current_lane_counts, current_lane_scores = process_frame(frame, model, rois_to_use)
                
                if ROI_ENABLED:
                    lane_counts = current_lane_counts
                else:
                    lane_counts = {"North": 0, "South": 0, "East": 0, "West": 0}
                    current_lane_scores = {"North": 0, "South": 0, "East": 0, "West": 0}
                
                last_process_time = time.time()
                
                ret, buffer = cv2.imencode('.jpg', frame)
                if ret:
                    latest_frame = buffer.tobytes()
                else:
                    print("[ERR] Failed to encode frame", flush=True)
            else:
                time.sleep(0.01)
        except Exception as e:
            print(f"[ERR] Vision Loop Error: {e}", flush=True)
            time.sleep(0.5)

def mode2_traffic_control_loop():
    """
    Mode 2 (multi-camera): runs control_traffic_lights_logic every second
    using per-direction counts & weighted scores from detection overlay threads.
    The local automation state (green lane selection) always runs so the UI
    reflects switching even when the external controller is disconnected.
    """
    global connection_status, mode2_counts, mode2_scores, system_started
    print("[INIT] Mode2 Traffic Control Thread Started", flush=True)

    while True:
        try:
            # Only start once user has pressed START
            if not system_started:
                time.sleep(1.0)
                continue

            counts = {d: mode2_counts.get(d, 0) for d in DIRECTIONS}
            scores = {d: mode2_scores.get(d, 0) for d in DIRECTIONS}

            # Always load latest config
            current_config = load_config()
            cycle_time = float(current_config.get('cycle_timer', 30.0))

            # Build control URL — only passed to the HTTP dispatch when truly connected
            if connection_status == "Connected" and is_live_feed_ready():
                host = current_config.get("controller_host", "localhost")
                port = current_config.get("controller_port", 5000)
                control_url = f"http://{host}:{port}"
            elif connection_status == "Connected" and not is_live_feed_ready():
                connection_status = "Requires LIVE FEED"
                control_url = None
            else:
                control_url = None  # Disconnected — run state machine only, skip HTTP

            # State machine always runs; HTTP dispatch skipped when control_url is None
            control_traffic_lights_logic(
                control_url, counts, TL_IDS,
                cycle_timer=cycle_time,
                lane_scores=scores
            )

            time.sleep(1.0)
        except Exception as e:
            print(f"[ERR] Mode2 Control Loop Error: {e}", flush=True)
            time.sleep(2.0)

# --- Flask Web Output ---
@app.route('/')
def index():
    global connection_status
    return render_template('index.html', status=connection_status)

@app.route('/traffic_control')
def traffic_control():
    global lane_counts, config, connection_status
    return render_template('traffic_control.html', lane_counts=lane_counts, config=config, status=connection_status)

@app.route('/api/lane_counts')
def api_lane_counts():
    global lane_counts, connection_status, last_process_time
    auto_data = get_automation_data()
    cycle_time = float(config.get('cycle_timer', 30.0))
    if auto_data.get("is_yellow_phase"):
        remaining = max(0, int(3.0 - (time.time() - auto_data["last_switch_time"])))
        current_cycle = 3.0
    else:
        remaining = max(0, int(cycle_time - (time.time() - auto_data["last_switch_time"])))
        current_cycle = cycle_time
    
    # Current detected state
    is_detecting = "INACTIVE"
    if model is not None:
        if last_process_time == 0:
            is_detecting = "READY"
        elif time.time() - last_process_time < 3.0:
            is_detecting = "ACTIVE"
        else:
            is_detecting = "STALE"
            
    # Use the active count source for the current control mode.
    control_mode = auto_data.get("control_mode", 1)
    counts = {d: mode2_counts.get(d, 0) for d in DIRECTIONS} if control_mode == 2 else lane_counts

    # Real-time traffic light states from automation_state
    tl_states = {l: "red" for l in ["North", "South", "East", "West"]}
    green_lane = None
    if any(v > 0 for v in counts.values()):
        if auto_data.get("is_yellow_phase"):
            green_lane = auto_data.get("current_green_lane")
            if green_lane in tl_states:
                tl_states[green_lane] = "yellow"
        else:
            green_lane = auto_data.get("current_green_lane")
            if green_lane in tl_states:
                tl_states[green_lane] = "green"
    else:
        green_lane = None
        # If there are no vehicles, enforce all-red regardless of prior automation state.
        tl_states = {l: "red" for l in ["North", "South", "East", "West"]}

    return json.dumps({
        "counts": counts,
        "green_lane": green_lane,
        "tl_states": tl_states,
        "timer": remaining,
        "cycle_duration": current_cycle,
        "connection": connection_status,
        "system_started": system_started,
        "detect_status": is_detecting,
        "feed_status": "ONLINE" if is_live_feed_ready() else "NO SIGNAL",
        "mode2_counts": {d: mode2_counts.get(d, 0) for d in DIRECTIONS},
        "mode2_thread_health": {d: (d in mode2_threads and mode2_threads[d].is_alive()) for d in DIRECTIONS},
        "control_mode": control_mode
    })

@app.route('/api/intensity_scores')
def api_intensity_scores():
    """Returns per-lane intensity scores and wait times from the detection module."""
    from detection import automation_state
    scores = {}
    wait_times = {}
    COUNT_WEIGHT = 5.0
    WAIT_WEIGHT = 1.0
    for lane in DIRECTIONS:
        cnt  = mode2_counts.get(lane, 0)
        wait = automation_state.get("wait_times", {}).get(lane, 0.0)
        score = (cnt * COUNT_WEIGHT) + (wait * WAIT_WEIGHT) + mode2_scores.get(lane, 0) * 10
        scores[lane]    = round(score, 1)
        wait_times[lane] = round(wait, 1)
    return jsonify({
        "scores":     scores,
        "wait_times": wait_times,
        "green_lane": automation_state.get("current_green_lane"),
        "is_yellow":  automation_state.get("is_yellow_phase", False),
        "system_started": system_started,
    })

@app.route('/api/live_feed')
def api_live_feed():
    """Alias for 3rd party apps"""
    response = Response(generate_video_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

@app.route('/api/camera/status')
def api_camera_status():
    """Checks if *any* active video source is present (CONTROL or external URL)."""
    status = "inactive"
    if is_live_feed_ready():
        status = "online"
    return json.dumps({"status": status, "timestamp": time.time()})

@app.route('/api/config')
def api_get_config():
    global config
    return jsonify(config)

@app.route('/api/system', methods=['POST'])
def api_system():
    """Simple start/stop endpoint callable directly from the frontend."""
    global system_started
    data = request.get_json(force=True) or {}
    action = data.get('action', '')
    if action == 'start':
        missing = check_start_prereqs()
        if missing:
            return jsonify({'status': 'error', 'message': 'Missing: ' + ', '.join(missing)})
        system_started = True
        print('[HMI] System STARTED via API', flush=True)
        return jsonify({'status': 'success', 'message': 'System started', 'system_started': True})
    elif action == 'stop':
        system_started = False
        print('[HMI] System STOPPED via API', flush=True)
        return jsonify({'status': 'success', 'message': 'System stopped', 'system_started': False})
    return jsonify({'status': 'error', 'message': 'Unknown action. Use start or stop.'})

@app.route('/tl_panel', methods=['GET', 'POST'])
def tl_panel_route():
    global TL_IDS
    if request.method == 'POST':
        data = request.json if request.is_json else request.form
        
        new_ids = {
            'North': data.get('tl_north'),
            'South': data.get('tl_south'),
            'East': data.get('tl_east'),
            'West': data.get('tl_west')
        }
        
        invalid_ids = []
        validated_ids = {}
        
        valid_actors = set()
        if connection_status == "Connected":
            host = config.get("controller_host", "localhost")
            port = config.get("controller_port", 5000)
            url = f"http://{host}:{port}/traffic_lights/all"
            try:
                resp = requests.get(url, timeout=2.0)
                if resp.status_code == 200:
                    for tl in resp.json():
                        valid_actors.add(tl.get("id"))
            except:
                pass

        for lane, tid in new_ids.items():
            if tid and str(tid).strip():
                try:
                    actor_id = int(tid)
                    # Check actor existence
                    if connection_status == "Connected":
                        if actor_id not in valid_actors and valid_actors:
                            invalid_ids.append(f"{lane}:{tid}")
                        else:
                            validated_ids[lane] = actor_id
                    else:
                        invalid_ids.append(f"CONTROL_API_OFFLINE({lane})")
                except ValueError:
                    invalid_ids.append(f"INVALID_NUM({lane}:{tid})")
            else:
                validated_ids[lane] = None

        if invalid_ids:
            msg = f"Validation Failed: {', '.join(invalid_ids)}. IDs must exist in active CONTROL session."
            if request.is_json:
                return jsonify({"status": "error", "message": msg})
            return render_template('tl_panel.html', tl_ids=TL_IDS, error=msg)

        # Update and Save only if everything is valid
        for lane, val in validated_ids.items():
            TL_IDS[lane] = val
            
        save_tls(TL_IDS)
        
        if request.is_json:
            return jsonify({"status": "success", "message": "Traffic Light IDs validated and saved"})
        return redirect(url_for('tl_panel_route'))
    
    # Return JSON if requested, else template
    if request.headers.get('Accept') == 'application/json':
        return jsonify({"tl_ids": TL_IDS})
    return render_template('tl_panel.html', tl_ids=TL_IDS)

@app.route('/roi_panel', methods=['GET', 'POST'])
def roi_panel_route():
    global ROIS
    if request.method == 'POST':
        data = request.json
        lane = data.get('lane')
        points = data.get('points')
        if lane and points and len(points) == 4:
            ROIS[lane] = np.array(points, np.int32)
            save_rois()
            return jsonify({"status": "success", "message": f"ROI saved and activated for {lane}"})
        return jsonify({"status": "error", "message": "Invalid point data"})
        
    serializable_rois = {k: v.tolist() for k, v in ROIS.items()}
    return jsonify({"current_rois": serializable_rois, "roi_enabled": ROI_ENABLED})

@app.route('/api/roi_enable', methods=['POST'])
def api_roi_enable():
    global ROI_ENABLED
    data = request.json
    ROI_ENABLED = data.get('enabled', True)
    return jsonify({"status": "success", "enabled": ROI_ENABLED})

@app.route('/api/controller/status', methods=['GET'])
def api_controller_status():
    url = get_controller_url()
    if not url:
        return jsonify({"status": "offline", "message": "Controller host not configured"}), 400
    try:
        resp = requests.get(f"{url}/traffic_lights/all", timeout=2.0)
        if resp.ok:
            return jsonify({"status": "connected", "lights": resp.json()})
        return jsonify({"status": "error", "message": f"Upstream responded {resp.status_code}"}), 502
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 502


@app.route('/api/tl_test', methods=['POST'])
def tl_test_mode():
    """Manually set a traffic light state for testing"""
    if connection_status != "Connected":
        return jsonify({"status": "error", "message": "Not connected to controller API"})

    data = request.json or {}
    actor_id = data.get('actor_id')
    state_str = data.get('state', '').capitalize() # "Red", "Yellow", "Green"

    if not actor_id or not state_str:
        return jsonify({"status": "error", "message": "Missing Actor ID or state"})

    try:
        host = config.get("controller_host", "localhost")
        port = config.get("controller_port", 5000)
        url = f"http://{host}:{port}/traffic_light/set_multiple"

        updates = [{"id": int(actor_id), "state": state_str, "freeze": True}]
        resp = requests.post(url, json={"updates": updates}, timeout=2.0)
        resp.raise_for_status()

        return jsonify({"status": "success", "message": f"Actor {actor_id} set to {state_str}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/traffic_lights/all', methods=['GET'])
def api_traffic_lights_all():
    url = get_controller_url()
    if not url:
        return jsonify({"status": "error", "message": "Controller API not configured"}), 400
    try:
        resp = requests.get(f"{url}/traffic_lights/all", timeout=config.get('controller_timeout', 5.0))
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/json'))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 502


@app.route('/api/traffic_light/set_multiple', methods=['POST'])
def api_traffic_light_set_multiple():
    url = get_controller_url()
    if not url:
        return jsonify({"status": "error", "message": "Controller API not configured"}), 400
    body = request.get_json(force=True, silent=True) or {}
    try:
        resp = requests.post(f"{url}/traffic_light/set_multiple", json=body, timeout=config.get('controller_timeout', 5.0))
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/json'))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 502

@app.route('/api/roi_sets', methods=['GET', 'POST'])
def roi_sets_api():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    if request.method == 'POST':
        data = request.json
        name = data.get('name')
        config_json = data.get('config') # Expecting full ROIS dict as json
        if name and config_json:
            # Update global ROIS in memory IMMEDIATELY so they show up on stream
            new_rois = {}
            for lane, points in config_json.items():
                new_rois[lane] = np.array(points, np.int32)
            global ROIS
            ROIS = new_rois
            save_rois() # Save these as the 'current' active ROIs in the main rois table
            
            c.execute("INSERT OR REPLACE INTO roi_sets (name, config) VALUES (?, ?)", (name, json.dumps(config_json)))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": f"ROI Set '{name}' saved and activated"})
        conn.close()
        return jsonify({"status": "error", "message": "Missing name or config"})
        
    c.execute("SELECT name FROM roi_sets")
    sets = [row[0] for row in c.fetchall()]
    conn.close()
    return jsonify({"sets": sets})

@app.route('/api/roi_sets/<name>', methods=['GET'])
def load_roi_set(name):
    global ROIS
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT config FROM roi_sets WHERE name = ?", (name,))
    row = c.fetchone()
    conn.close()
    
    if row:
        config_data = json.loads(row[0])
        new_rois = {}
        for lane, points in config_data.items():
            new_rois[lane] = np.array(points, np.int32)
        ROIS = new_rois
        save_rois() # Set as current
        return jsonify({"status": "success", "message": f"Set '{name}' loaded", "rois": config_data})
    return jsonify({"status": "error", "message": "Set not found"})

@app.route('/panel', methods=['GET', 'POST'])
def control_panel():
    global config, connection_status, system_started
    if request.method == 'POST':
        data = request.json if request.is_json else request.form
        action = data.get('action')
        # Keep config updated from incoming form even if start action runs directly
        if data.get('controller_host') is not None:
            config['controller_host'] = data.get('controller_host')
        if data.get('controller_port') is not None:
            try:
                config['controller_port'] = int(data.get('controller_port'))
            except Exception:
                pass
        if data.get('controller_timeout') is not None:
            try:
                config['controller_timeout'] = float(data.get('controller_timeout'))
            except Exception:
                pass
        if data.get('yolo_model') is not None:
            config['yolo_model'] = data.get('yolo_model')
        if data.get('cycle_timer') is not None:
            try:
                config['cycle_timer'] = int(data.get('cycle_timer'))
            except Exception:
                pass
        if data.get('live_feed_url') is not None:
            config['live_feed_url'] = data.get('live_feed_url')
        save_config(config)

        # Simple Save
        if action in ['save_only', 'toggle_connect', 'start_system', 'stop_system']:
            old_path = config.get('yolo_model', '')
            old_url = config.get('live_feed_url', '')

            config['controller_host'] = data.get('controller_host', data.get('thorulf_host', config.get('controller_host', '')))
            config['controller_port'] = int(data.get('controller_port', data.get('thorulf_port', config.get('controller_port', 2000)))) if data.get('controller_port', data.get('thorulf_port', None)) else config.get('controller_port', 5000)
            config['controller_timeout'] = float(data.get('controller_timeout', data.get('thorulf_timeout', config.get('controller_timeout', 10.0)))) if data.get('controller_timeout', data.get('thorulf_timeout', None)) else config.get('controller_timeout', 10.0)
            config['yolo_model'] = data.get('yolo_model', config.get('yolo_model', ''))
            config['cycle_timer'] = int(data.get('cycle_timer', 30)) if data.get('cycle_timer') else config.get('cycle_timer', 30)
            config['flask_host'] = data.get('flask_host', config.get('flask_host', '0.0.0.0'))
            config['flask_port'] = int(data.get('flask_port', 5050)) if data.get('flask_port') else config.get('flask_port', 5050)
            # new field:
            config['live_feed_url'] = data.get('live_feed_url', config.get('live_feed_url', ''))
            save_config(config)

            # reload model if path changed
            if config['yolo_model'] != old_path:
                load_yolo_model(config['yolo_model'])

            # start/stop external feed if URL changed
            if config['live_feed_url'] != old_url:
                if config['live_feed_url'].strip():
                    print(f"[HMI] Starting external feed: {config['live_feed_url']}", flush=True)
                    _start_external_feed(config['live_feed_url'])
                else:
                    print("[HMI] Stopping external feed", flush=True)
                    stop_external_feed()
            
        if action == 'toggle_connect':
            if not is_live_feed_ready():
                msg = "Cannot connect: Live feed not ready"
                connection_status = "Requires LIVE FEED"
            elif connection_status == "Connected":
                disconnect_controller()
                msg = "Disconnected from controller"
            else:
                bg_connect_controller()
                msg = "Connecting to controller..."
        elif action == 'start_system':
            missing = check_start_prereqs()
            if missing:
                msg = "Cannot start. Missing: " + ", ".join(missing)
            else:
                system_started = True
                msg = "System started"
        elif action == 'stop_system':
            system_started = False
            msg = "System stopped"
        else:
            msg = "Configuration saved to database"
        if request.is_json:
            return jsonify({"status": "success", "message": msg})
        return redirect(url_for('control_panel'))
    
    return render_template('panel.html', config=config, status=connection_status)

def generate_video_stream():
    global latest_frame, BLANK_FRAME_BYTES
    print("[DEBUG] Video Stream Generator Started for a client")
    try:
        while True:
            # Check current data
            frame_to_yield = latest_frame if latest_frame is not None else BLANK_FRAME_BYTES
            
            if frame_to_yield:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_to_yield + b'\r\n')
            time.sleep(0.05)
    except Exception as e:
        print(f"[DEBUG] Video client disconnected: {e}")

@app.route('/video_feed')
def video_feed():
    """MJPEG stream for live dashboard and ROI setup windows"""
    return Response(generate_video_stream(), 
                   mimetype='multipart/x-mixed-replace; boundary=frame',
                   headers={'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-cache, no-store, must-revalidate'})

# --- MODE 2 ROUTES ---
@app.route('/video_feed/mode2/<direction>')
def video_feed_mode2(direction):
    """Per-direction MJPEG stream for Mode 2"""
    if direction not in DIRECTIONS:
        return jsonify({'error': 'Invalid direction'}), 404
    return Response(generate_mode2_stream(direction),
                    mimetype='multipart/x-mixed-replace; boundary=frame',
                    headers={'Access-Control-Allow-Origin': '*'})

@app.route('/api/mode2_config', methods=['GET', 'POST'])
def mode2_config_api():
    global mode2_cam_config
    if request.method == 'POST':
        data = request.json or {}
        changed = []
        for lane in DIRECTIONS:
            key = f'src_{lane.lower()}'
            if key in data:
                new_src = str(data[key]).strip()
                if mode2_cam_config.get(lane) != new_src:
                    mode2_cam_config[lane] = new_src
                    changed.append(lane)
        if changed:
            save_mode2_cameras(mode2_cam_config)
            for lane in changed:
                start_mode2_direction(lane)
        return jsonify({'status': 'success', 'updated': changed, 'config': mode2_cam_config})
    return jsonify({'status': 'ok', 'config': mode2_cam_config})

@app.route('/api/mode2_rois', methods=['GET', 'POST'])
def mode2_rois_api():
    global mode2_rois
    if request.method == 'POST':
        data = request.json or {}
        lane = data.get('lane')
        points = data.get('points')  # list of [x,y]
        if lane not in DIRECTIONS:
            return jsonify({'status': 'error', 'message': 'Invalid lane'})
        if points and len(points) >= 3:
            mode2_rois[lane] = np.array(points, np.int32)
        elif points == [] or points is None:
            # Clear ROI for this lane
            mode2_rois.pop(lane, None)
        save_mode2_rois(mode2_rois)
        return jsonify({'status': 'success', 'message': f'Mode2 ROI saved for {lane}'})

    # GET: return all saved rois serialized
    serializable = {k: v.tolist() for k, v in mode2_rois.items()}
    return jsonify({'status': 'ok', 'rois': serializable})

@app.route('/api/control_mode', methods=['POST'])
def api_control_mode():
    from detection import automation_state
    data = request.json or {}
    mode = data.get('mode', 1) # 1 or 2
    automation_state['control_mode'] = mode
    return jsonify({'status': 'success', 'control_mode': mode})

# Auto-start Mode 2 captures for any saved sources
for _dir in DIRECTIONS:
    if mode2_cam_config.get(_dir, '').strip():
        start_mode2_direction(_dir)

if __name__ == '__main__':
    try:
        # Avoid reloader double-spawn threading issues on Windows
        # Use debug=True for errors, but use_reloader=False for stable background threads
        
        # 2. Start YOLO CV processing in background thread
        processor_thread = threading.Thread(target=vision_processing_loop, daemon=True)
        processor_thread.start()

        # 3. Start Mode 2 traffic control thread
        mode2_ctrl_thread = threading.Thread(target=mode2_traffic_control_loop, daemon=True)
        mode2_ctrl_thread.start()
        
        # 3. Start Flask Web Server
        flask_host = config.get('flask_host', '0.0.0.0')
        flask_port = config.get('flask_port', 5050)
        print(f"Starting Smart Traffic Management App on {flask_host}:{flask_port} (Reloader: OFF)...")
        app.run(host=flask_host, port=flask_port, debug=True, use_reloader=False)
        
    except KeyboardInterrupt:
        print("Shutting down...")
