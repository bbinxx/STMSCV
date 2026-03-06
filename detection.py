import cv2
import numpy as np
def process_frame(frame, model, rois):
    # Initialize counts based on rois keys
    lane_counts = {k: 0 for k in rois.keys()}
    
    # Overlay for semi-transparent ROI shading
    overlay = frame.copy()
    
    # Draw ROI Polygons
    for name, points in rois.items():
        if points is None or len(points) < 3: continue
        
        pts = points.reshape((-1, 1, 2))
        # 1. Draw Semi-transparent shaded area
        cv2.fillPoly(overlay, [pts], (0, 255, 255)) # Yellow shade
        
        # 2. Draw Thick Border
        cv2.polylines(frame, [pts], isClosed=True, color=(0, 255, 255), thickness=3)
        
        # 3. Add Lane Name
        cv2.putText(frame, name.upper(), (int(points[0][0]), int(points[0][1] - 10)), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                    
    # Blending (alpha 0.15)
    cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)
    
    # Run YOLO inference
    if model is not None:
        # Detect: bicycle(1), car(2), motorcycle(3), bus(5), truck(7)
        # conf=0.25 reduces false positives; iou=0.45 suppresses duplicate boxes
        results = model.predict(frame, classes=[1, 2, 3, 5, 7], conf=0.25, iou=0.45, imgsz=1280, verbose=False)
        
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                cls = int(box.cls[0])
                cls_name = model.names[cls].lower()
                
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2

                # Multi-point anchor check: center, bottom-center, bottom-left, bottom-right
                # Vehicle counted if ANY of these points falls inside the ROI polygon —
                # catches large/angled vehicles missed by single ground-point test
                check_pts = [
                    (float(cx),  float(cy)),           # centroid
                    (float(cx),  float(y2)),            # bottom-center (ground contact)
                    (float(x1 + (x2 - x1) * 0.25), float(y2)),  # bottom-left quarter
                    (float(x1 + (x2 - x1) * 0.75), float(y2)),  # bottom-right quarter
                ]
                
                # Identify Emergency Vehicles
                is_emergency = any(kw in cls_name for kw in ['ambulance', 'fire', 'emergency', 'police'])
                
                # ROI Match Check — count in first matching ROI only (no double-count)
                for lane_name, points in rois.items():
                    roi_poly = points.astype(np.float32)
                    if any(cv2.pointPolygonTest(roi_poly, pt, False) >= 0 for pt in check_pts):
                        lane_counts[lane_name] += 1
                        
                        color = (0, 0, 255) if is_emergency else (0, 255, 0)
                        thickness = 3 if is_emergency else 2
                        
                        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
                        # Draw all anchor points for debug visibility
                        for pt in check_pts:
                            cv2.circle(frame, (int(pt[0]), int(pt[1])), 3, (0, 80, 255), -1)
                        label = f"!!! {cls_name.upper()} !!!" if is_emergency else f"{cls_name.upper()}"
                        cv2.putText(frame, label, (x1, y1 - 10), 
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                        break  # count once per vehicle
                        
    return frame, lane_counts

# --- Consolidated Traffic Control State ---
automation_state = {
    "current_green_lane": "North",
    "last_switch_time": 0.0,
    "is_yellow_phase": False,
    "yellow_trigger_lane": None,
    "control_mode": 1 # 1: Fixed Cycle, 2: Intensity Based
}

def control_traffic_lights_logic(world, counts, tl_ids, cycle_timer=30.0):
    """Consolidated controller logic with Mode (Fixed vs Intensity) support"""
    global automation_state
    
    import time
    import carla
    
    LANE_ORDER = ["North", "East", "South", "West"]
    MIN_GREEN_TIME = 10.0 # Prevent rapid switching in Intensity Mode
    
    current_time = time.time()
    if automation_state["last_switch_time"] == 0:
        automation_state["last_switch_time"] = current_time
        if automation_state["current_green_lane"] not in LANE_ORDER:
            automation_state["current_green_lane"] = "North"

    # 1. Handle Yellow Phase transition (Common for both modes)
    if automation_state["is_yellow_phase"]:
        if current_time - automation_state["last_switch_time"] >= 3.0: # 3s Yellow duration
            automation_state["is_yellow_phase"] = False
            automation_state["current_green_lane"] = automation_state["yellow_trigger_lane"]
            automation_state["last_switch_time"] = current_time
            print(f"[AUTO] Transition Complete: {automation_state['current_green_lane']} is now GREEN", flush=True)
    
    # 2. Lane Switch Decision
    else:
        next_lane = None
        
        # --- MODE 1: Fixed Cycle ---
        if automation_state.get("control_mode", 1) == 1:
            if current_time - automation_state["last_switch_time"] >= cycle_timer:
                try:
                    current_idx = LANE_ORDER.index(automation_state["current_green_lane"])
                except ValueError:
                    current_idx = 0
                next_idx = (current_idx + 1) % len(LANE_ORDER)
                next_lane = LANE_ORDER[next_idx]
                print(f"[AUTO] Mode 1: Cycle Switch -> {next_lane}", flush=True)

        # --- MODE 2: Intensity Based (Rush Priority) ---
        else:
            time_since_last = current_time - automation_state["last_switch_time"]
            
            # Check if current lane is empty OR minimum green time has passed
            current_count = counts.get(automation_state["current_green_lane"], 0)
            
            # Find lane with highest count
            max_lane = automation_state["current_green_lane"]
            max_val = current_count
            for l, c in counts.items():
                if c > max_val:
                    max_val = c
                    max_lane = l
            
            # Decision: Switch if another lane has more vehicles AND (current is empty OR min time passed)
            if max_lane != automation_state["current_green_lane"]:
                if current_count == 0 or time_since_last >= MIN_GREEN_TIME:
                    next_lane = max_lane
                    print(f"[AUTO] Mode 2: Intensity Switch -> {next_lane} (Count: {max_val})", flush=True)
            
            # Safety: Max cycle timer to prevent starving a lane if detect fails
            elif time_since_last >= cycle_timer * 2:
                try:
                    current_idx = LANE_ORDER.index(automation_state["current_green_lane"])
                except ValueError:
                    current_idx = 0
                next_lane = LANE_ORDER[(current_idx + 1) % len(LANE_ORDER)]
                print(f"[AUTO] Mode 2: Max-Time Safety Switch -> {next_lane}", flush=True)

        if next_lane:
            automation_state["is_yellow_phase"] = True
            automation_state["yellow_trigger_lane"] = next_lane
            automation_state["last_switch_time"] = current_time

    # 3. Apply to CARLA Actors
    if not any(tl_ids.values()): return
    
    tls_actors = world.get_actors().filter('*traffic_light*')
    for tl in tls_actors:
        lane_name = None
        for l, tid in tl_ids.items():
            if tid and tl.id == tid:
                lane_name = l
                break
        
        if lane_name:
            if automation_state["is_yellow_phase"] and lane_name == automation_state["current_green_lane"]:
                tl.set_state(carla.TrafficLightState.Yellow)
            elif lane_name == automation_state["current_green_lane"]:
                tl.set_state(carla.TrafficLightState.Green)
            else:
                tl.set_state(carla.TrafficLightState.Red)

def get_automation_data():
    return automation_state
