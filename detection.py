import cv2
import numpy as np

# --- detection constants (to avoid re-allocating each frame) ---
ALL_VEHICLE_CLASSES = None  # None => include every class reported by the model

CLASS_WEIGHT = {1: 1, 2: 2, 3: 1, 5: 3, 6: 3, 7: 3, 8: 2}  # heavier vehicles raise the score more

VEHICLE_KEYWORDS = ['bicycle', 'car', 'motorcycle', 'bus', 'train', 'truck', 'boat', 'van', 'taxi', 'ambulance', 'scooter', 'auto-rickshaw']

DETECTION_IMG_SIZE = 640  # increased for higher accuracy (better for bikes/small objects)

_YOLO_LOCK_ATTR = '_yolo_lock'


def process_frame(frame, model, rois, conf_thres=0.15, iou_thres=0.45, img_size=640):
    """
    Detect vehicles only inside each ROI region.
    """
    import threading

    lane_counts = {k: 0 for k in rois.keys()}
    heavy_counts = {k: 0 for k in rois.keys()}
    emergency_counts = {k: 0 for k in rois.keys()}

    # ── Draw ROI overlays on the full frame ───────────────────────────────────
    if rois:
        overlay = frame.copy()
        for name, points in rois.items():
            if points is None or len(points) < 3:
                continue
            pts = points.reshape((-1, 1, 2))
            cv2.fillPoly(overlay, [pts], (0, 255, 255))
            cv2.polylines(frame, [pts], isClosed=True, color=(0, 255, 255), thickness=3)
            cv2.putText(frame, name.upper(),
                        (int(points[0][0]), int(points[0][1] - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)

    if model is None or not rois:
        return frame, lane_counts, heavy_counts, emergency_counts

    # ── Thread-safe YOLO lock ─────────────────────────────────────────────────
    if not hasattr(model, _YOLO_LOCK_ATTR):
        setattr(model, _YOLO_LOCK_ATTR, threading.Lock())
    lock = getattr(model, _YOLO_LOCK_ATTR)

    half_precision = False
    try:
        if 'cuda' in str(model.device).lower():
            half_precision = True
    except Exception:
        pass

    # ── Single-pass Full-Frame Inference ──────────────────────────────────────
    predict_kwargs = dict(
        conf=conf_thres,
        iou=iou_thres,
        imgsz=img_size,
        half=half_precision,
        verbose=False,
    )
    if ALL_VEHICLE_CLASSES is not None:
        predict_kwargs['classes'] = ALL_VEHICLE_CLASSES

    with lock:
        results = model.predict(frame, **predict_kwargs)
        
    # --- Process detections against all ROIs ---------------------------------
    for r in results:
        for box in r.boxes:
            cx1, cy1, cx2, cy2 = map(int, box.xyxy[0])
            cls = int(box.cls[0])
            cls_name = model.names[cls].lower()

            if ALL_VEHICLE_CLASSES is None:
                if not any(kw in cls_name for kw in VEHICLE_KEYWORDS):
                    continue

            # Heavy vehicles: bus, truck, train, boat (classes 5,6,7,8)
            is_heavy = cls in [5, 6, 7, 8]
            is_emergency = any(kw in cls_name for kw in ['ambulance', 'fire', 'emergency', 'police'])

            # Anchor check
            fcx = (cx1 + cx2) / 2.0
            fcy = (cy1 + cy2) / 2.0
            anchor_pt = (float(fcx), float(fcy))

            matched_lane = None
            
            for lane_name, roi_pts in rois.items():
                if roi_pts is None or len(roi_pts) < 3:
                    continue
                roi_poly = roi_pts.astype(np.float32)
                
                inside = cv2.pointPolygonTest(roi_poly, anchor_pt, False) >= 0
                
                if inside:
                    matched_lane = lane_name
                    lane_counts[lane_name] += 1
                    if is_heavy:
                        heavy_counts[lane_name] += 1
                    if is_emergency:
                        emergency_counts[lane_name] += 1
                    break 
            
            if matched_lane:
                if is_emergency:
                    color = (0, 0, 255)
                elif cls in [1, 3]:
                    color = (255, 200, 0)
                else:
                    color = (0, 255, 0)
                thickness = 3 if is_emergency else 2

                cv2.rectangle(frame, (cx1, cy1), (cx2, cy2), color, thickness)
                cv2.circle(frame, (int(anchor_pt[0]), int(anchor_pt[1])), 3, (0, 80, 255), -1)

                label = cls_name.upper()
                if is_emergency:
                    label = f"!!! {label} !!!"
                cv2.putText(frame, label, (cx1, cy1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    return frame, lane_counts, heavy_counts, emergency_counts

# --- Consolidated Traffic Control State ---
automation_state = {
    "current_green_lane": "North",
    "last_switch_time": 0.0,
    "is_yellow_phase": False,
    "yellow_trigger_lane": None,
    "control_mode": 1, # 1: Fixed Cycle, 2: Intensity Based
    "wait_times": {"North": 0.0, "South": 0.0, "East": 0.0, "West": 0.0},
    "last_tick_time": 0.0,
    "latest_scores": {"North": 0.0, "South": 0.0, "East": 0.0, "West": 0.0},
    "latest_green_timers": {"North": 30.0, "South": 30.0, "East": 30.0, "West": 30.0},
    "latest_densities": {"North": 0.0, "South": 0.0, "East": 0.0, "West": 0.0},
    "current_max_green": 30.0
}

def control_traffic_lights_logic(control_url, counts, tl_ids, cycle_timer=30.0, lane_scores=None, lane_heavies=None):
    """
    Advanced Adaptive AI Traffic Control Algorithm
    """
    global automation_state
    
    import time
    import requests
    import threading

    if lane_scores is None: lane_scores = {}
    if lane_heavies is None: lane_heavies = {}
    
    LANE_ORDER = ["North", "East", "South", "West"]
    current_time = time.time()
    
    # Initialize timing
    if automation_state["last_tick_time"] == 0:
        automation_state["last_tick_time"] = current_time
    if automation_state["last_switch_time"] == 0:
        automation_state["last_switch_time"] = current_time

    dt = current_time - automation_state["last_tick_time"]
    automation_state["last_tick_time"] = current_time

    # 1. Update Wait Times for Red Lanes
    for lane in LANE_ORDER:
        if lane == automation_state["current_green_lane"]:
            automation_state["wait_times"][lane] = 0.0 # Reset wait time
        elif counts.get(lane, 0) > 0:
            automation_state["wait_times"][lane] += dt # Accumulate
        else:
            automation_state["wait_times"][lane] = 0.0 # No vehicles = no wait

    # Handle Yellow Phase transition
    if automation_state["is_yellow_phase"]:
        if current_time - automation_state["last_switch_time"] >= 3.0: 
            automation_state["is_yellow_phase"] = False
            automation_state["current_green_lane"] = automation_state["yellow_trigger_lane"]
            automation_state["last_switch_time"] = current_time
            print(f"[AUTO] Phase Shift: {automation_state['current_green_lane']} is now GREEN", flush=True)
        return

    # Force ALL-RED when no vehicles are detected anywhere
    if sum(counts.values()) == 0:
        if automation_state["current_green_lane"] is not None:
            print("[AUTO] Idle Mode — No vehicles detected. Intersection clear.", flush=True)
            automation_state["current_green_lane"] = None
            automation_state["last_switch_time"] = current_time
        return

    # 2. Advanced Decision Logic
    time_since_last = current_time - automation_state["last_switch_time"]
    current_lane = automation_state["current_green_lane"]
    
    # Calculate Intensity Scores and Adaptive Timers
    intensity_scores = {}
    green_timers = {}
    
    for lane in LANE_ORDER:
        V = counts.get(lane, 0)
        H = lane_heavies.get(lane, 0)
        W = automation_state["wait_times"].get(lane, 0.0)
        E = lane_scores.get(lane, 0)  # Emergency vehicles
        
        D = min(1.0, (V + H * 2.0) / 10.0)  # Density
        Q = V  # Queue length
        F = V / 10.0  # Flow rate proxy
        C = 10 if (lane == current_lane and time_since_last < 5.0) else 0 # Cooldown
        
        score = (2 * V) + (4 * H) + (0.7 * W) + (5 * D) + (2 * Q) + (1 * F) - C
        score += (E * 50)  # Massive override for emergencies
        
        intensity_scores[lane] = score
        dynamic_green = 10 + (1.2 * V) + (10 * D) + (0.3 * W)
        green_timers[lane] = min(90.0, max(10.0, dynamic_green))
        automation_state["latest_densities"][lane] = round(D, 2)

    automation_state["latest_scores"] = {k: round(v, 1) for k, v in intensity_scores.items()}
    automation_state["latest_green_timers"] = {k: round(v, 1) for k, v in green_timers.items()}

    sorted_lanes = sorted(intensity_scores.items(), key=lambda x: x[1], reverse=True)
    best_lane, best_score = sorted_lanes[0]
    
    next_lane = None
    
    if current_lane not in LANE_ORDER:
        next_lane = best_lane
    else:
        current_score = intensity_scores.get(current_lane, 0)
        current_max_green = green_timers.get(current_lane, 10.0)
        automation_state["current_max_green"] = current_max_green  # Expose for UI
        
        if counts.get(current_lane, 0) == 0 and best_score > 0:
            next_lane = best_lane
        elif time_since_last >= current_max_green:
            if best_lane != current_lane and best_score > 0:
                next_lane = best_lane
        elif time_since_last >= 5.0: # Minimum green time
            if best_lane != current_lane:
                threshold = 15.0 # SwitchOnlyIf = NewScore > CurrentScore + Threshold
                if best_score > (current_score + threshold):
                    next_lane = best_lane

    if next_lane and next_lane != current_lane:
        automation_state["is_yellow_phase"] = True
        automation_state["yellow_trigger_lane"] = next_lane
        automation_state["last_switch_time"] = current_time

    # 3. Apply via HTTP API (skip if no APIs or IDs set)
    has_target = False
    for entry in tl_ids.values():
        if isinstance(entry, dict):
            if (entry.get('id') and str(entry.get('id')).strip()) or (entry.get('api') and str(entry.get('api')).strip()):
                has_target = True
                break
    
    if not has_target:
        return

    def send_updates():
        for lane_name, entry in tl_ids.items():
            # Support both old (plain value) and new (dict) format
            if isinstance(entry, dict):
                tid = entry.get('id')
                api_base = entry.get('api', '').strip()
            else:
                tid = entry
                api_base = ''

            # Determine state for this lane
            state = "red"
            if automation_state["is_yellow_phase"] and lane_name == automation_state.get("yellow_trigger_lane"):
                state = "yellow"
            elif lane_name == automation_state["current_green_lane"] and not automation_state["is_yellow_phase"]:
                state = "green"

            # Resolve URL
            url = None
            if api_base:
                if not api_base.lower().startswith(('http://', 'https://')):
                    api_base = 'http://' + api_base
                api_base = api_base.rstrip('/')
                if tid and str(tid).strip():
                    # Legacy: base + /traffic_light/<id>/set/<state>
                    url = f"{api_base}/traffic_light/{tid}/set/{state}"
                else:
                    # New: base is already the full light URL, just append /set/<state>
                    url = f"{api_base}/set/{state}"
            elif tid and str(tid).strip() and control_url:
                # Fallback: global controller
                base = control_url.rstrip('/')
                url = f"{base}/traffic_light/{tid}/set/{state}"

            if url:
                try:
                    requests.get(url, timeout=1.5)
                except Exception:
                    pass

    # Run in thread so vision loop doesn't block
    threading.Thread(target=send_updates, daemon=True).start()

def get_automation_data():
    return automation_state
