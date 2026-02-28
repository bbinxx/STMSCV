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

from detection import process_frame

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
        "carla_host": "localhost",
        "carla_port": 2000,
        "carla_timeout": 10.0,
        "yolo_model": "yolov8n.pt",
        "flask_host": "0.0.0.0",
        "flask_port": 5050,
        "live_feed_url": "/video_feed"
    }
    for k, v in rows:
        if k in ["carla_port", "flask_port"]:
            config_data[k] = int(v)
        elif k == "carla_timeout":
            config_data[k] = float(v)
        else:
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

# YOLOv8 model initialization
MODEL_PATH = config.get("yolo_model", "yolov8n.pt")
model = YOLO(MODEL_PATH)

# Thread-safe queue for CARLA camera images
image_queue = queue.Queue(maxsize=1)

# Global states for Flask streaming and tracking
latest_frame = None
lane_counts = {"North": 0, "South": 0, "East": 0, "West": 0}

global_world = None
global_camera = None
connection_status = "Disconnected"

# Traffic Light Timer State
current_green_lane = "North" # Default starting lane
last_switch_time = 0.0

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
    return {
        "North": np.array([[300, 100], [500, 100], [450, 250], [350, 250]], np.int32),
        "South": np.array([[300, 350], [500, 350], [450, 500], [350, 500]], np.int32),
        "East":  np.array([[550, 250], [700, 250], [600, 350], [500, 350]], np.int32),
        "West":  np.array([[100, 250], [250, 250], [300, 350], [200, 350]], np.int32)
    }

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

# --- 1. NATIVE CARLA VIDEO INPUT ---
def carla_sensor_callback(image):
    """Callback triggered every time the CARLA camera generates a new image"""
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
                
        host = config.get("carla_host", "localhost")
        port = config.get("carla_port", 2000)
        timeout = config.get("carla_timeout", 5.0)
        client = carla.Client(host, port)
        client.set_timeout(timeout)
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
        print(f"Failed to connect to CARLA at {host}:{port}. Error: {e}")

def bg_connect_carla():
    thread = threading.Thread(target=setup_carla_task, daemon=True)
    thread.start()

# --- 4. TRAFFIC LIGHT CONTROL ---
def get_traffic_lights(world):
    """
    HOW TO FIND CORRECT TRAFFIC LIGHT IDs:
    1. Spawn your camera and observe the intersection.
    2. Run a loop over world.get_actors().filter('*traffic_light*')
    3. Calculate the distance from each traffic light to your intersection center (or camera location).
    4. Group the closest 4-6 traffic lights and manually identify which ID controls which lane.
    5. Map these IDs to specific directions (e.g., North, South, East, West).
    """
    return world.get_actors().filter('*traffic_light*')

def control_traffic_lights_logic(world, counts):
    """Timed logic: Switch to highest density lane every 30 seconds"""
    global current_green_lane, last_switch_time
    
    current_time = time.time()
    
    # Check if it's time to re-evaluate (30 second intervals)
    if current_time - last_switch_time >= 30.0:
        # Find the lane with the highest density
        # Ties are handled by max() returning the first key found
        max_lane = max(counts, key=counts.get)
        
        # Update the green lane and reset timer
        if max_lane != current_green_lane:
            print(f"SWITCHING GREEN: {max_lane} chosen (Density: {counts[max_lane]})")
        
        current_green_lane = max_lane
        last_switch_time = current_time

    # Apply the states to CARLA actors
    if not any(TL_IDS.values()):
        return 
        
    tls_actors = get_traffic_lights(world)
    for tl in tls_actors:
        matched_lane = None
        for lane, t_id in TL_IDS.items():
            if t_id and tl.id == t_id:
                matched_lane = lane
                break
                
        if matched_lane:
            if matched_lane == current_green_lane:
                tl.set_state(carla.TrafficLightState.Green)
            else:
                tl.set_state(carla.TrafficLightState.Red)

# --- 3. YOLOv8 CV ENGINE & PROCESSING LOOP ---
def vision_processing_loop():
    """Separate thread to process images async from CARLA ticks"""
    global latest_frame, lane_counts, global_world
    
    while True:
        if not image_queue.empty():
            frame = image_queue.get().copy()
            
            # Run detection logic from external module
            frame, lane_counts = process_frame(frame, model, ROIS)
            
            # Execute Traffic Light Control Logic
            if global_world is not None:
                control_traffic_lights_logic(global_world, lane_counts)
            
            # Encode for Flask Video Stream
            ret, buffer = cv2.imencode('.jpg', frame)
            if ret:
                latest_frame = buffer.tobytes()
        else:
            time.sleep(0.01)

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
    global lane_counts, current_green_lane, last_switch_time
    remaining = max(0, int(30.0 - (time.time() - last_switch_time)))
    return json.dumps({
        "counts": lane_counts,
        "green_lane": current_green_lane,
        "timer": remaining
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

@app.route('/tl_panel', methods=['GET', 'POST'])
def tl_panel_route():
    global TL_IDS
    if request.method == 'POST':
        if request.is_json:
            data = request.json
            TL_IDS['North'] = int(data.get('tl_north')) if data.get('tl_north') else None
            TL_IDS['South'] = int(data.get('tl_south')) if data.get('tl_south') else None
            TL_IDS['East'] = int(data.get('tl_east')) if data.get('tl_east') else None
            TL_IDS['West'] = int(data.get('tl_west')) if data.get('tl_west') else None
            save_tls(TL_IDS)
            return jsonify({"status": "success"})
        else:
            # Accept IDs or default to None if empty
            TL_IDS['North'] = int(request.form.get('tl_north')) if request.form.get('tl_north') else None
            TL_IDS['South'] = int(request.form.get('tl_south')) if request.form.get('tl_south') else None
            TL_IDS['East'] = int(request.form.get('tl_east')) if request.form.get('tl_east') else None
            TL_IDS['West'] = int(request.form.get('tl_west')) if request.form.get('tl_west') else None
            save_tls(TL_IDS)
            return redirect(url_for('tl_panel_route'))
    
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
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "Invalid point data"})
        
    # Serialize ROIs for frontend
    serializable_rois = {k: v.tolist() for k, v in ROIS.items()}
    
    # We return the rois as json here directly instead of rendering a template
    # Since the frontend script handles fetching config with endpoints now
    return jsonify({"current_rois": serializable_rois})

@app.route('/panel', methods=['GET', 'POST'])
def control_panel():
    global config, connection_status
    if request.method == 'POST':
        if request.is_json:
            data = request.json
            action = data.get('action')
            if action == 'save_connect':
                config['carla_host'] = data.get('carla_host', config.get('carla_host'))
                config['carla_port'] = int(data.get('carla_port', config.get('carla_port')))
                config['carla_timeout'] = float(data.get('carla_timeout', config.get('carla_timeout')))
                config['yolo_model'] = data.get('yolo_model', config.get('yolo_model'))
                config['flask_host'] = data.get('flask_host', config.get('flask_host'))
                config['flask_port'] = int(data.get('flask_port', config.get('flask_port')))
                config['live_feed_url'] = data.get('live_feed_url', config.get('live_feed_url'))
                save_config(config)
                bg_connect_carla()
                return jsonify({"status": "success"})
        else:
            action = request.form.get('action')
            if action == 'save_connect':
                config['carla_host'] = request.form.get('carla_host', config.get('carla_host'))
                config['carla_port'] = int(request.form.get('carla_port', config.get('carla_port')))
                config['carla_timeout'] = float(request.form.get('carla_timeout', config.get('carla_timeout')))
                config['yolo_model'] = request.form.get('yolo_model', config.get('yolo_model'))
                config['flask_host'] = request.form.get('flask_host', config.get('flask_host'))
                config['flask_port'] = int(request.form.get('flask_port', config.get('flask_port')))
                config['live_feed_url'] = request.form.get('live_feed_url', config.get('live_feed_url'))
                save_config(config)
                bg_connect_carla()
                return redirect(url_for('control_panel'))
    
    return render_template('panel.html', config=config, status=connection_status)

def generate_video_stream():
    global latest_frame
    while True:
        if latest_frame is not None:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + latest_frame + b'\r\n')
        time.sleep(0.03)

@app.route('/video_feed')
def video_feed():
    response = Response(generate_video_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response

if __name__ == '__main__':
    try:
        # Prevent threads from starting twice when using Flask reloader
        if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
            # 1. Start CARLA components non-blocking
            bg_connect_carla()
            
            # 2. Start YOLO CV processing in background thread
            processor_thread = threading.Thread(target=vision_processing_loop, daemon=True)
            processor_thread.start()
        
        # 3. Start Flask Web Server
        flask_host = config.get('flask_host', '0.0.0.0')
        flask_port = config.get('flask_port', 5050)
        print(f"Starting Smart Traffic Management App on {flask_host}:{flask_port}...")
        app.run(host=flask_host, port=flask_port, debug=True, use_reloader=True)
        
    except KeyboardInterrupt:
        print("Shutting down... destroying camera.")
        if global_camera:
            try:
                global_camera.destroy()
            except:
                pass
