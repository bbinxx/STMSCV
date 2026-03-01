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
- **Multi-Camera (Mode 2)**: Support for 4 simultaneous video sources (Webcams, RTSP, Video Files, or MJPEG URLs).
- **CARLA Integration**: Direct connection to the CARLA Simulator to sync virtual traffic lights and camera views.

### 4. Configuration & API
- **Dynamic Port Control**: Fully configurable Flask and CARLA connection settings.
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
pip install flask opencv-python ultralytics numpy carla
```

### 2. Running the Application
Start the main server:
```bash
python app.py
```
By default, the HMI will be accessible at: `http://localhost:5050`

### 3. CARLA Simulator Connection (Optional)
Ensure CARLA is running, then use the **Control Panel** in the HMI to input your CARLA Host and Port to sync the virtual environment.

---

## 📂 Project Structure
- `app.py`: Main Flask server and MJPEG streaming logic.
- `detection.py`: YOLOv8 inference and traffic control algorithms.
- `static/`: Frontend assets (Styles, Scripts).
- `templates/`: HMI Dashboard UI.
- `traffic_data.db`: Persistent storage for ROIs and settings.
