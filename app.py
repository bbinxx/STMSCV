try:
    import carla
except ImportError:
    carla = None
    print("[WARN] carla module not available; CARLA integration disabled", flush=True)
import cv2
import numpy as np
import threading
import queue
import time
import json
import os
import sqlite3
import urllib.request
from ultralytics import YOLO
from flask import Flask, Response, render_template, request, redirect, url_for, jsonify

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
        "carla_host": "",
        "carla_port": "",
        "carla_timeout": "",
        "yolo_model": "",
        "flask_host": "0.0.0.0",
        "flask_port": 5050,
        "live_feed_url": "",
        "cycle_timer": 30
    }
    for k, v in rows:
        try:
            if k in ["carla_port", "flask_port", "cycle_timer"] and v is not None and str(v).strip():
                config_data[k] = int(v)
            elif k == "carla_timeout" and v is not None and str(v).strip():
                config_data[k] = float(v)
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

# Thread-safe queue for CARLA camera images (and optionally external feed)
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

global_world = None
global_camera = None
connection_status = "Disconnected"

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
    for lane, src in rows:
        data[lane] = src or ''
    return data

def save_mode2_cameras(cam_cfg):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for lane, src in cam_cfg.items():
        c.execute("INSERT OR REPLACE INTO mode2_cameras (lane, source) VALUES (?, ?)", (lane, src))
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
        stream = urllib.request.urlopen(src, timeout=10)
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
    global model, mode2_rois, mode2_counts, mode2_scores
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

# --- 1. NATIVE CARLA VIDEO INPUT ---
def carla_sensor_callback(image):
    """Callback triggered every time the CARLA camera generates a new image.
    If an external `live_feed_url` is configured, we ignore the CARLA stream so
    that the queue is not polluted with unwanted frames.
    """
    try:
        # if a user has configured an API/URL feed, prefer that source
        if config.get("live_feed_url", "").strip():
            return
        if not image or not image.raw_data:
            return
        array = np.frombuffer(image.raw_data, dtype=np.dtype("uint8"))
        array = np.reshape(array, (image.height, image.width, 4))
        array = array[:, :, :3]  # Keep BGR channels (CARLA outputs BGRA, OpenCV uses BGR)
        # Store in queue, dropping oldest if full to maintain real-time
        if image_queue.full():
            try:
                image_queue.get_nowait()
            except queue.Empty:
                pass
        image_queue.put(array)
    except Exception as e:
        print(f"[ERR] Camera callback error: {e}")

def setup_carla_task():
    """Connects to CARLA gracefully in a separate thread"""
    global global_world, global_camera, connection_status
    connection_status = "Connecting..."
    
    try:
        if carla is None:
            connection_status = "CARLA MODULE MISSING"
            return
        if global_camera:
            try:
                global_camera.destroy()
            except:
                pass
        
        host = config.get("carla_host")
        port = config.get("carla_port")
        if not host or not port:
            connection_status = "Waiting for configuration in Control Panel"
            return
        
        timeout = config.get("carla_timeout") or 5.0
        client = carla.Client(str(host), int(port))
        client.set_timeout(float(timeout))
        world = client.get_world()
        
        # Locate RGB camera blueprint
        blueprint_library = world.get_blueprint_library()
        camera_bp = blueprint_library.find('sensor.camera.rgb')
        camera_bp.set_attribute('image_size_x', '800')
        camera_bp.set_attribute('image_size_y', '600')
        camera_bp.set_attribute('fov', '90')
        camera_bp.set_attribute('sensor_tick', '0.05') # Limit frame rate if needed
        
        # Get Spectator transform to match view
        spectator = world.get_spectator()
        transform = spectator.get_transform()
        # Offset slightly to ensure we aren't inside the spectator's head or floor
        transform.location.z += 2.0
        
        camera = world.spawn_actor(camera_bp, transform)
        
        # Start listening to sensor
        camera.listen(lambda image: carla_sensor_callback(image))
        global_world = world
        global_camera = camera
        connection_status = "Connected"
        
        # Start a thread to keep camera synced with spectator movement
        def sync_view():
            while global_camera:
                try:
                    spec_trans = world.get_spectator().get_transform()
                    global_camera.set_transform(spec_trans)
                    time.sleep(0.05)
                except:
                    break
        
        sync_thread = threading.Thread(target=sync_view, daemon=True)
        sync_thread.start()
        
        print(f"CARLA connected at {host}:{port} (Synced to Spectator)")
    except Exception as e:
        connection_status = f"Failed to connect (Check CARLA is running)"
        global_world = None
        global_camera = None
        print(f"Failed to connect to CARLA at {host}:{port}. Error: {e}", flush=True)

def disconnect_carla():
    global global_world, global_camera, connection_status
    if global_camera:
        try:
            global_camera.stop()
            global_camera.destroy()
        except:
            pass
    global_camera = None
    global_world = None
    connection_status = "Disconnected"
    print("[HMI] Disconnected from CARLA")

def bg_connect_carla():
    thread = threading.Thread(target=setup_carla_task, daemon=True)
    thread.start()


# --- EXTERNAL / API BASED LIVE FEED SUPPORT ---

def _read_external_mjpeg(src):
    """Generator that yields frames from a multipart MJPEG HTTP stream."""
    buf = b''
    try:
        stream = urllib.request.urlopen(src, timeout=10)
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
    if carla is None or world is None:
        return []
    return world.get_actors().filter('*traffic_light*')

# --- 3. YOLOv8 CV ENGINE & PROCESSING LOOP ---
def vision_processing_loop():
    """CARLA camera mode: process frames and control lights"""
    global latest_frame, lane_counts, global_world, last_process_time
    print("[INIT] Vision Processing Thread Started", flush=True)
    
    while True:
        try:
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
                
                # Pass scores directly — no stale dict lookup
                if global_world is not None:
                    cycle_time = float(config.get('cycle_timer', 30.0))
                    control_traffic_lights_logic(
                        global_world, lane_counts, TL_IDS,
                        cycle_timer=cycle_time,
                        lane_scores=current_lane_scores
                    )
                
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
    Only active when control_mode == 2.
    """
    from detection import automation_state
    print("[INIT] Mode2 Traffic Control Thread Started", flush=True)

    while True:
        try:
            if automation_state.get("control_mode", 1) == 2 and global_world is not None:
                # Build counts and scores from mode2 detection results
                counts = {d: mode2_counts.get(d, 0) for d in DIRECTIONS}
                scores = {d: mode2_scores.get(d, 0) for d in DIRECTIONS}
                
                # Get cycle timer from main config
                current_config = load_config()
                cycle_time = float(current_config.get('cycle_timer', 30.0))
                
                control_traffic_lights_logic(
                    global_world, counts, TL_IDS,
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
    global lane_counts, connection_status, global_world, last_process_time
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
            
    # Real-time traffic light states from world
    tl_states = {}
    if global_world and carla is not None:
        for lane, tid in TL_IDS.items():
            if tid and str(tid).strip():
                try:
                    tl_actor = global_world.get_actor(int(tid))
                    if tl_actor is not None:
                        st = tl_actor.get_state()
                        if st == carla.TrafficLightState.Red: tl_states[lane] = "red"
                        elif st == carla.TrafficLightState.Yellow: tl_states[lane] = "yellow"
                        elif st == carla.TrafficLightState.Green: tl_states[lane] = "green"
                        else: tl_states[lane] = "red"
                    else:
                        tl_states[lane] = "red"
                except:
                    tl_states[lane] = "red"
            else:
                tl_states[lane] = "red"
    else:
        # Fallback if no connection or CARLA not installed
        tl_states = {l: "red" for l in ["North", "South", "East", "West"]}
        
    return json.dumps({
        "counts": lane_counts,
        "green_lane": auto_data["current_green_lane"],
        "tl_states": tl_states,
        "timer": remaining,
        "cycle_duration": current_cycle,
        "connection": connection_status,
        "detect_status": is_detecting,
        "feed_status": "EXTERNAL" if (global_camera is None and external_feed_src and external_feed_thread and external_feed_thread.is_alive()) else ("ONLINE" if global_camera is not None else "NO SIGNAL"),
        "mode2_counts": {d: mode2_counts.get(d, 0) for d in DIRECTIONS},
        "control_mode": get_automation_data().get("control_mode", 1)
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
    """Checks if *any* active video source is present (CARLA or external URL)."""
    global global_camera, external_feed_thread, external_feed_src
    status = "inactive"
    if global_camera is not None:
        status = "carla"
    elif external_feed_src and external_feed_thread and external_feed_thread.is_alive():
        status = "external"
    return json.dumps({"status": status, "timestamp": time.time()})

@app.route('/api/config')
def api_get_config():
    global config
    return jsonify(config)

@app.route('/tl_panel', methods=['GET', 'POST'])
def tl_panel_route():
    global TL_IDS, global_world
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
        
        for lane, tid in new_ids.items():
            if tid and str(tid).strip():
                try:
                    actor_id = int(tid)
                    # Check actor existence in CARLA
                    if global_world:
                        carla_actor = global_world.get_actor(actor_id)
                        if carla_actor is None:
                            invalid_ids.append(f"{lane}:{tid}")
                        else:
                            validated_ids[lane] = actor_id
                    else:
                        # If CARLA not connected, can't validate, but user asked to check if exists
                        # So we might want to warn or prevent save.
                        # For now, if not connected, we warn.
                        invalid_ids.append(f"CARLA_OFFLINE({lane})")
                except ValueError:
                    invalid_ids.append(f"INVALID_NUM({lane}:{tid})")
            else:
                validated_ids[lane] = None

        if invalid_ids:
            msg = f"Validation Failed: {', '.join(invalid_ids)}. IDs must exist in active CARLA session."
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

@app.route('/api/tl_test', methods=['POST'])
def tl_test_mode():
    """Manually set a traffic light state for testing"""
    global global_world
    if carla is None:
        return jsonify({"status": "error", "message": "CARLA module missing"})
    if not global_world:
        return jsonify({"status": "error", "message": "CARLA not connected"})
        
    data = request.json or {}
    actor_id = data.get('actor_id')
    state_str = data.get('state', '').lower() # "red", "yellow", "green"
    
    if not actor_id or not state_str:
        return jsonify({"status": "error", "message": "Missing Actor ID or state"})
        
    try:
        tl_actor = global_world.get_actor(int(actor_id))
        if not tl_actor:
            return jsonify({"status": "error", "message": f"Actor {actor_id} not found"})
            
        mapping = {
            "red": carla.TrafficLightState.Red,
            "yellow": carla.TrafficLightState.Yellow,
            "green": carla.TrafficLightState.Green
        }
        
        target_state = mapping.get(state_str.lower())
        if target_state is not None:
            tl_actor.set_state(target_state)
            return jsonify({"status": "success", "message": f"Actor {actor_id} set to {state_str.upper()}"})
        return jsonify({"status": "error", "message": "Invalid state"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

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
    global config, connection_status, global_camera
    if request.method == 'POST':
        data = request.json if request.is_json else request.form
        action = data.get('action')
        
        # Simple Save
        if action in ['save_only', 'toggle_connect']:
            old_path = config.get('yolo_model', '')
            old_url = config.get('live_feed_url', '')

            config['carla_host'] = data.get('carla_host', config.get('carla_host', ''))
            config['carla_port'] = int(data.get('carla_port', 2000)) if data.get('carla_port') else config.get('carla_port')
            config['carla_timeout'] = float(data.get('carla_timeout', 10.0)) if data.get('carla_timeout') else config.get('carla_timeout')
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
            if carla is None:
                msg = "CARLA integration unavailable"
            elif global_camera:
                disconnect_carla()
                msg = "Disconnected from simulator"
            else:
                bg_connect_carla()
                msg = "Connecting to CARLA..."
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
        print("Shutting down... destroying camera.")
        if global_camera:
            try:
                global_camera.destroy()
            except:
                pass
