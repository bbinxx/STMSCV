import cv2
import numpy as np
def process_frame(frame, model, rois):
    # Debug image info
    print(f"[DEBUG] Frame Process: {type(frame)} Shape: {frame.shape if hasattr(frame, 'shape') else 'None'} Model: {model is not None}")
    
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
        # Use user-specified confidence and filter only vehicles (car=2, motorcycle=3, bus=5, truck=7)
        results = model.predict(frame, classes=[2, 3, 5, 7], conf=0.5, verbose=False)
        
        det_count = 0
        for r in results:
            det_count += len(r.boxes)
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                conf = float(box.conf[0])
                cls = int(box.cls[0])
                name = model.names[cls]
                print(f"[DEBUG] Found: {name} ({conf:.2f})")
                
                # Calculate center point
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                
                # High Visibility Detection Overlay (ALL DETECTIONS)
                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 255), 2)
                cv2.putText(frame, f"{name.upper()} {conf:.2f}", (x1, y1 - 10), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 2)
                
                # ROI Match Check
                for lane_name, points in rois.items():
                    if cv2.pointPolygonTest(points.astype(np.float32), (float(cx), float(cy)), False) >= 0:
                        lane_counts[lane_name] += 1
                        # Highlight COUNTED vehicle with GREEN 3px box
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 3)
                        cv2.circle(frame, (cx, cy), 6, (0, 0, 255), -1)
                        break 
        if det_count > 0: 
            print(f"[DEBUG] Processing successful. Detections found: {det_count}", flush=True)
            
    return frame, lane_counts

# --- Consolidated Traffic Control State ---
automation_state = {
    "current_green_lane": "North",
    "last_switch_time": 0.0,
    "is_yellow_phase": False,
    "yellow_trigger_lane": None
}

def control_traffic_lights_logic(world, counts, tl_ids):
    """Consolidated controller logic previously in app.py"""
    global automation_state
    
    if not counts: return
    current_time = 0.0 # Will be set below
    import time
    import carla
    
    current_time = time.time()
    if automation_state["last_switch_time"] == 0:
        automation_state["last_switch_time"] = current_time

    # 1. Handle Yellow Phase transition
    if automation_state["is_yellow_phase"]:
        if current_time - automation_state["last_switch_time"] >= 3.0: # 3s Yellow duration
            automation_state["is_yellow_phase"] = False
            automation_state["current_green_lane"] = automation_state["yellow_trigger_lane"]
            automation_state["last_switch_time"] = current_time
            print(f"[AUTO] Transition Complete: {automation_state['current_green_lane']} is now GREEN", flush=True)
    
    # 2. Check for Lane Switch (Density based)
    elif current_time - automation_state["last_switch_time"] >= 30.0:
        max_lane = max(counts, key=counts.get)
        if max_lane != automation_state["current_green_lane"]:
            print(f"[AUTO] Density Switch: {automation_state['current_green_lane']} -> YELLOW -> {max_lane}", flush=True)
            automation_state["is_yellow_phase"] = True
            automation_state["yellow_trigger_lane"] = max_lane
            automation_state["last_switch_time"] = current_time
        else:
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
