import carla
import cv2
import numpy as np
import threading
import queue
import time
import json
import os
import sqlite3
from ultralytics import YOLO
from flask import Flask, Response, render_template, request, redirect, url_for, jsonify

from detection import process_frame, control_traffic_lights_logic, get_automation_data

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
        "live_feed_url": ""
    }
    for k, v in rows:
        if k in ["carla_port", "flask_port"] and str(v).strip():
            val = int(v)
            # FORCE 5050 if 5000 is detected due to user project conflict
            if k == "flask_port" and val == 5000:
                val = 5050
            config_data[k] = val
        elif k == "carla_timeout" and str(v).strip():
            config_data[k] = float(v)
        else:
            config_data[k] = v
    # Final safety override
    if config_data["flask_port"] == 5000:
        config_data["flask_port"] = 5050
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
    
    # Resolve path relative to script directory
    base_dir = os.path.dirname(os.path.abspath(__file__))
    res_path = os.path.abspath(os.path.join(base_dir, path))
    
    if not os.path.exists(res_path):
        print(f"[ERR] YOLO model file not found at: {res_path}", flush=True)
        model = None
        return False
        
    try:
        model = YOLO(res_path)
        print(f"[INIT] YOLO model loaded successfully: {res_path}", flush=True)
        return True
    except Exception as e:
        print(f"[ERR] Failed to load YOLO ({res_path}): {e}", flush=True)
        model = None
        return False

# Initial load
load_yolo_model(config.get("yolo_model", ""))

# Thread-safe queue for CARLA camera images
image_queue = queue.Queue(maxsize=1)

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
    """Callback triggered every time the CARLA camera generates a new image"""
    try:
        if not image or not image.raw_data:
            return
            
        array = np.frombuffer(image.raw_data, dtype=np.dtype("uint8"))
        array = np.reshape(array, (image.height, image.width, 4))
        array = array[:, :, :3] # Keep BGR channels (CARLA outputs BGRA, OpenCV uses BGR)
        
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

# --- 4. TRAFFIC LIGHT CONTROL HELPERS ---
def get_traffic_lights(world):
    return world.get_actors().filter('*traffic_light*')

# --- 3. YOLOv8 CV ENGINE & PROCESSING LOOP ---
def vision_processing_loop():
    """Separate thread to process images async from CARLA ticks"""
    global latest_frame, lane_counts, global_world, last_process_time
    print("[INIT] Vision Processing Thread Started", flush=True)
    
    while True:
        try:
            if not image_queue.empty():
                img_data = image_queue.get()
                if img_data is None: continue
                
                frame = img_data.copy()
                
                # Execute Consolidated Detection & Control Logic
                rois_to_use = ROIS.copy() if ROI_ENABLED else {}
                frame, current_lane_counts = process_frame(frame, model, rois_to_use)
                
                if ROI_ENABLED:
                    lane_counts = current_lane_counts
                else:
                    lane_counts = {"North": 0, "South": 0, "East": 0, "West": 0}
                
                last_process_time = time.time()
                
                # Execute Logic centered in detection.py
                if global_world is not None:
                    control_traffic_lights_logic(global_world, lane_counts, TL_IDS)
                
                # Encode for Flask Video Stream
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
    remaining = max(0, int(30.0 - (time.time() - auto_data["last_switch_time"])))
    
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
    if global_world:
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
        # Fallback if no connection
        tl_states = {l: "red" for l in ["North", "South", "East", "West"]}
        
    return json.dumps({
        "counts": lane_counts,
        "green_lane": auto_data["current_green_lane"],
        "tl_states": tl_states,
        "timer": remaining,
        "connection": connection_status,
        "detect_status": is_detecting,
        "feed_status": "ONLINE" if global_camera is not None else "NO SIGNAL"
    })

@app.route('/api/live_feed')
def api_live_feed():
    """Alias for 3rd party apps"""
    response = Response(generate_video_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response

@app.route('/api/camera/status')
def api_camera_status():
    """Checks if the camera actor is active"""
    global global_camera
    status = "active" if global_camera is not None else "inactive"
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
    if not global_world:
        return jsonify({"status": "error", "message": "CARLA not connected"})
        
    data = request.json
    actor_id = data.get('actor_id')
    state_str = data.get('state') # "red", "yellow", "green"
    
    if not actor_id:
        return jsonify({"status": "error", "message": "No Actor ID provided"})
        
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
            config['carla_host'] = data.get('carla_host', config.get('carla_host', ''))
            config['carla_port'] = int(data.get('carla_port', 2000)) if data.get('carla_port') else config.get('carla_port')
            config['carla_timeout'] = float(data.get('carla_timeout', 10.0)) if data.get('carla_timeout') else config.get('carla_timeout')
            config['yolo_model'] = data.get('yolo_model', config.get('yolo_model', ''))
            save_config(config)
            
            # Reload model if path changed
            if config['yolo_model'] != old_path:
                load_yolo_model(config['yolo_model'])
            
        if action == 'toggle_connect':
            if global_camera:
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
                   headers={'Access-Control-Allow-Origin': '*'})

if __name__ == '__main__':
    try:
        # Avoid reloader double-spawn threading issues on Windows
        # Use debug=True for errors, but use_reloader=False for stable background threads
        
        # 2. Start YOLO CV processing in background thread
        processor_thread = threading.Thread(target=vision_processing_loop, daemon=True)
        processor_thread.start()
        
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
