# AI BASED SMART TRAFFIC CONTROL SYSTEM (STMCV)

A professional, high-performance Traffic Management HMI that uses Computer Vision and AI to optimize traffic flow in real-time.

---

## 🚦 Core Features

### 1. Intelligent AI Detection
- **YOLOv8 Engine**: Real-time detection of cars, buses, trucks, and motorcycles.
- **Emergency Priority**: Automatic identification of Ambulances, Police, and Fire trucks with signal overrides.
- **Region of Interest (ROI)**: Custom interactive drawing tool to define specific detection zones for each lane.

### 2. Advanced Signal Logic
- **Fixed Cycle Mode**: Traditional timed traffic signal switching.
- **Rush Priority (Intensity-Based)**: Dynamic switching where lanes with higher vehicle density get longer green lights, reducing urban congestion.

### 3. Comprehensive HMI (Human-Machine Interface)
- **Live Dashboard**: Real-time vehicle counts, signal states, and countdown timers.
- **External Live Feed**: Optionally point the main stream at any HTTP/RTSP/MJPEG/source URL or camera device; the app will ingest it, run detection, and expose it via the `/video_feed` and `/api/live_feed` endpoints.
- **Multi-Camera (Mode 2)**: Support for 4 simultaneous video sources (Webcams, RTSP, Video Files, or MJPEG URLs).
- **Controller API Integration**: Control traffic lights through generic REST API endpoints (no CARLA Python dependency required).

### 4. Configuration & API
- **Dynamic Port Control**: Fully configurable Flask and Thorulf API connection settings.
- **JSON API**: Built-in endpoints for external data consumption.

---

## 🛠 Technology Stack

- **Backend**: Python 3.x, Flask, OpenCV
- **AI/ML**: YOLOv8 (Ultralytics), NumPy
- **Database**: SQLite3 (Persistent configuration and ROI storage)
- **Frontend**: Vanilla HTML5, CSS3 (Modern Cyberpunk Aesthetics), JavaScript (ES6+)

---

## 🚀 Getting Started

### 1. Installation
Install the required dependencies using `pip` or your preferred package manager:
```bash
pip install flask opencv-python ultralytics numpy
```

### 2. Running the Application
Start the main server:
```bash
python app.py
```
By default, the HMI will be accessible at: `http://localhost:5050`

### 3. Controller API Connection (Required)
Ensure your controller API server is running and that live feed is active. Use the **Control Panel** in the HMI to input the Controller Host and Port, live feed source, and run the feed test before connecting.

### 🔌 External API (Headless Control)
Control the simulation through REST endpoints without CARLA Python in your app.

1. Fetch all traffic lights from the configured controller:
```bash
curl http://localhost:5050/api/traffic_lights/all
```
2. Set multiple lights to one state via the app proxy:
```bash
curl -X POST http://localhost:5050/api/traffic_light/set_multiple \
  -H "Content-Type: application/json" \
  -d '{"ids": [105,106], "state": "Green", "freeze": true}'
```
3. Individual updates in one call through app proxy:
```bash
curl -X POST http://localhost:5050/api/traffic_light/set_multiple \
  -H "Content-Type: application/json" \
  -d '{"updates": [{"id":105,"state":"Red","freeze":true}, {"id":106,"state":"Green","freeze":false}]}'
```

---

## 📂 Project Structure
- `app.py`: Main Flask server and MJPEG streaming logic.
- `detection.py`: YOLOv8 inference and traffic control algorithms.
- `static/`: Frontend assets (Styles, Scripts).
- `templates/`: HMI Dashboard UI.
- `traffic_data.db`: Persistent storage for ROIs and settings.

## 🧭 HMI Panel Workflow
### 1) Dashboard Panel (Main)
- Shows controller status, detection/live feed health, lane counts, current phase.
- Use this first to verify system status and that feed and controller are ready before automation.

### 2) Connection Panel
- Configure:
  - Flask host/port (app server)
  - Controller API host/port/timeout
  - Live feed source URL/device
  - YOLO model path
  - Cycle timer
- Use **TEST LIVE FEED** and **TEST CONTROLLER API** to validate before connecting.
- Press **SAVE CONFIG** to persist settings.
- Press **CONNECT** to establish controller API and enable automation (requires live feed ready).

### 3) ROI Panel
- Draw per-lane ROI boxes on live feed preview.
- Save each ROI and save ROI sets for quick reloading.
- Used by the detection pipeline to count vehicles per lane.

### 4) Traffic Light Panel
- Map lane names to controller TL ids.
- Test each mapped traffic light with red/yellow/green buttons for validation.
- This mapping is required for automated control to drive actual signal IDs.

### 5) Live Feed Panel
- Displays MJPEG video output from the active live feed source.
- Use if you need to verify the processed stream visually.

### 6) Multi-cam Mode (optional)
- For multi-camera mode (Mode 2), configure per-direction sources, ROI, and use advanced source control.
- Mode 2 is optional and can be toggled from the top nav.

### Control Enforcement Rule
- The system now only applies traffic control when:
  1. Controller API is connected
  2. Live feed is confirmed healthy
- If live feed fails, controller status will show `Requires LIVE FEED` and control actions are blocked.

### API Quick Checks
- `GET /api/camera/status` to verify feed status
- `GET /api/controller/status` to verify controller connectivity
- `GET /api/lane_counts` to validate detection outputs

This workflow ensures safe sequencing: configure → test feed → test API → connect → run automation.
