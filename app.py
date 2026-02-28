import carla
import cv2
import numpy as np
import threading
import queue
import time
import json
import os
from ultralytics import YOLO
from flask import Flask, Response, render_template, request, redirect, url_for

app = Flask(__name__)

# --- Configuration & Shared State ---
# Load connection configuration
CONFIG_FILE = "connection.json"
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {
        "carla_host": "localhost",
        "carla_port": 2000,
        "carla_timeout": 10.0,
        "yolo_model": "yolov8n.pt",
        "flask_host": "0.0.0.0",
        "flask_port": 5050,
        "live_feed_url": "/video_feed"
    }

def save_config(config_data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config_data, f, indent=4)

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
ROI_CONFIG_FILE = "rois.json"
TL_CONFIG_FILE = "tls.json"

def load_rois():
    if os.path.exists(ROI_CONFIG_FILE):
        with open(ROI_CONFIG_FILE, "r") as f:
            data = json.load(f)
            return {k: np.array(v, np.int32) for k, v in data.items()}
    return {
        "North": np.array([[300, 100], [500, 100], [450, 250], [350, 250]], np.int32),
        "South": np.array([[300, 350], [500, 350], [450, 500], [350, 500]], np.int32),
        "East":  np.array([[550, 250], [700, 250], [600, 350], [500, 350]], np.int32),
        "West":  np.array([[100, 250], [250, 250], [300, 350], [200, 350]], np.int32)
    }

def save_rois():
    with open(ROI_CONFIG_FILE, "w") as f:
        data = {k: v.tolist() for k, v in ROIS.items()}
        json.dump(data, f, indent=4)

def load_tls():
    if os.path.exists(TL_CONFIG_FILE):
        with open(TL_CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"North": None, "South": None, "East": None, "West": None}

def save_tls(tls_data):
    with open(TL_CONFIG_FILE, "w") as f:
        json.dump(tls_data, f, indent=4)

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
            
            # Reset counts
            lane_counts = {k: 0 for k in lane_counts}
            
            # Draw ROI Polygons
            for name, points in ROIS.items():
                pts = points.reshape((-1, 1, 2))
                cv2.polylines(frame, [pts], isClosed=True, color=(0, 255, 255), thickness=2)
                # Correctly position text near first point
                cv2.putText(frame, name, (int(points[0][0]), int(points[0][1] - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            
            # Run YOLO inference
            # broadened classes: car(2), motorcycle(3), bus(5), truck(7), train(6), ambulance?
            results = model.predict(frame, classes=[2, 3, 5, 7, 1, 4, 6, 8], conf=0.2, verbose=False)
            
            for r in results:
                for box in r.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    conf = float(box.conf[0])
                    cls = int(box.cls[0])
                    name = model.names[cls]
                    
                    # Calculate center point
                    cx = (x1 + x2) // 2
                    cy = (y1 + y2) // 2
                    
                    # Draw a bright debug box for ANY detection
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 255), 1) # Magenta for visibility
                    cv2.putText(frame, f"{name} {conf:.2f}", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 255), 1)
                    
                    # Point inside Polygon check for ROIs
                    for lane_name, points in ROIS.items():
                        if cv2.pointPolygonTest(points, (cx, cy), False) >= 0:
                            lane_counts[lane_name] += 1
                            # Draw prominent Green box for COUNTED vehicles
                            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                            cv2.circle(frame, (cx, cy), 5, (0, 0, 255), -1)
                            break 
            
            # Execute Traffic Light Control Logic
            if global_world is not None:
                control_traffic_lights_logic(global_world, lane_counts)
            
            # Overlay Statistics
            y_pos = 30
            for name, count in lane_counts.items():
                cv2.putText(frame, f"{name} count: {count}", (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                y_pos += 30
            
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
            return {"status": "success"}
        return {"status": "error", "message": "Invalid point data"}
        
    # Serialize ROIs for frontend
    serializable_rois = {k: v.tolist() for k, v in ROIS.items()}
    return render_template('roi_panel.html', current_rois=serializable_rois, config=config)

@app.route('/panel', methods=['GET', 'POST'])
def control_panel():
    global config, connection_status
    if request.method == 'POST':
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
