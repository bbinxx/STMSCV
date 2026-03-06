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
        # All vehicle types including 2-wheelers:
        # 1=bicycle, 2=car, 3=motorcycle, 5=bus, 6=train, 7=truck, 8=boat
        # 0=person excluded (pedestrians only, not riders)
        ALL_VEHICLE_CLASSES = [1, 2, 3, 5, 6, 7, 8]

        # Weighted score per class — heavier vehicles add more pressure
        # 2-wheelers = 1, car = 2, bus/truck = 3 (used for rush priority scoring)
        CLASS_WEIGHT = {1: 1, 2: 2, 3: 1, 5: 3, 6: 3, 7: 3, 8: 2}

        results = model.predict(frame, classes=ALL_VEHICLE_CLASSES, conf=0.25, iou=0.45, imgsz=1280, verbose=False)

        # lane_scores used by rush priority (weighted), lane_counts is raw vehicle count
        lane_scores = {k: 0 for k in rois.keys()}

        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                cls = int(box.cls[0])
                cls_name = model.names[cls].lower()
                weight = CLASS_WEIGHT.get(cls, 1)

                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2

                # 4-point anchor check — catches large/angled vehicles
                check_pts = [
                    (float(cx),                        float(cy)),   # centroid
                    (float(cx),                        float(y2)),   # bottom-center
                    (float(x1 + (x2 - x1) * 0.25),   float(y2)),   # bottom-left quarter
                    (float(x1 + (x2 - x1) * 0.75),   float(y2)),   # bottom-right quarter
                ]

                # Emergency flag
                is_emergency = any(kw in cls_name for kw in ['ambulance', 'fire', 'emergency', 'police'])

                # ROI match — count once in first matching ROI
                for lane_name, pts in rois.items():
                    if pts is None or len(pts) < 3:
                        continue
                    roi_poly = pts.astype(np.float32)
                    if any(cv2.pointPolygonTest(roi_poly, pt, False) >= 0 for pt in check_pts):
                        lane_counts[lane_name] += 1
                        lane_scores[lane_name] += weight * (5 if is_emergency else 1)

                        # Color: red=emergency, cyan=2-wheeler, green=other
                        if is_emergency:
                            color = (0, 0, 255)
                        elif cls in [1, 3]:  # bicycle / motorcycle
                            color = (255, 200, 0)
                        else:
                            color = (0, 255, 0)

                        thickness = 3 if is_emergency else 2
                        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
                        for pt in check_pts:
                            cv2.circle(frame, (int(pt[0]), int(pt[1])), 3, (0, 80, 255), -1)
                        label = f"!!! {cls_name.upper()} !!!" if is_emergency else cls_name.upper()
                        cv2.putText(frame, label, (x1, y1 - 10),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                        break  # count once per vehicle

    # Always define lane_scores (empty if no model)
    if model is None:
        lane_scores = {k: 0 for k in rois}

    return frame, lane_counts, lane_scores

# --- Consolidated Traffic Control State ---
automation_state = {
    "current_green_lane": "North",
    "last_switch_time": 0.0,
    "is_yellow_phase": False,
    "yellow_trigger_lane": None,
    "control_mode": 1 # 1: Fixed Cycle, 2: Intensity Based
}

def control_traffic_lights_logic(world, counts, tl_ids, cycle_timer=30.0, lane_scores=None):
    """Consolidated controller logic with Mode (Fixed vs Intensity) support"""
    global automation_state
    
    import time
    import carla

    if lane_scores is None:
        lane_scores = {}
    
    LANE_ORDER = ["North", "East", "South", "West"]
    MIN_GREEN_TIME = 10.0
    
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

        # --- MODE 2: Rush Priority (Weighted Intensity) ---
        else:
            time_since_last = current_time - automation_state["last_switch_time"]
            current_lane = automation_state["current_green_lane"]
            current_count = counts.get(current_lane, 0)
            current_score = automation_state.get("lane_scores", {}).get(current_lane, 0)

            # Build sorted list of non-empty lanes by weighted score (desc)
            # Use passed-in lane_scores, fall back to raw count if missing
            scored = sorted(
                [(lane, lane_scores.get(lane, cnt))
                 for lane, cnt in counts.items() if cnt > 0],
                key=lambda x: x[1], reverse=True
            )

            # Skip current lane if empty — jump immediately to busiest non-empty lane
            if current_count == 0 and scored:
                next_lane = scored[0][0]
                print(f"[AUTO] Rush: '{current_lane}' empty → skip to '{next_lane}' (score:{scored[0][1]})", flush=True)

            # Switch if another lane has higher score AND min green time passed
            elif scored and scored[0][0] != current_lane and time_since_last >= MIN_GREEN_TIME:
                next_lane = scored[0][0]
                current_score = lane_scores.get(current_lane, current_count)
                print(f"[AUTO] Rush: Switch → '{next_lane}' (score:{scored[0][1]} > '{current_lane}':{current_score})", flush=True)

            # Safety fallback: prevent lane starvation if detection fails
            elif time_since_last >= cycle_timer * 2:
                try:
                    current_idx = LANE_ORDER.index(current_lane)
                except ValueError:
                    current_idx = 0
                # Skip to next lane that has vehicles, otherwise rotate
                for i in range(1, len(LANE_ORDER)):
                    candidate = LANE_ORDER[(current_idx + i) % len(LANE_ORDER)]
                    if counts.get(candidate, 0) > 0:
                        next_lane = candidate
                        break
                else:
                    next_lane = LANE_ORDER[(current_idx + 1) % len(LANE_ORDER)]
                print(f"[AUTO] Rush: Safety fallback → '{next_lane}'", flush=True)

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
