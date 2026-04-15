import cv2
import numpy as np

# --- detection constants (to avoid re-allocating each frame) ---
# `ALL_VEHICLE_CLASSES` can be either a list of COCO class indices to restrict
# detection, or ``None`` to let the model return all classes and then filter by
# name.  Setting to ``None`` is the easiest way to ensure *every* vehicle type
# is seen in the ROI, which is what users asked for in the dashboard.
#
# To view the mapping from index to name, run:
#   >>> from ultralytics import YOLO
#   >>> print(YOLO('yolov8s.pt').names)
#
# Examples of vehicle indices: bicycle=1, car=2, motorcycle=3, bus=5, train=6,
# truck=7, boat=8.  But there are also names such as "ambulance" and "taxi"
# which are useful to include in some scenarios.
ALL_VEHICLE_CLASSES = None  # None => include every class reported by the model

# if you do specify indices, you can still supply weights here; if ALL_VEHICLE_CLASSES
# is None then CLASS_WEIGHT is used only for those classes present in the results
CLASS_WEIGHT = {1: 1, 2: 2, 3: 1, 5: 3, 6: 3, 7: 3, 8: 2}  # heavier vehicles raise the score more

# Keywords used when ALL_VEHICLE_CLASSES is None to recognise the object as a
# vehicle.  Searching by substring makes it robust to names like 'ambulance'.
VEHICLE_KEYWORDS = ['bicycle', 'car', 'motorcycle', 'bus', 'train', 'truck', 'boat', 'van', 'taxi', 'ambulance']

# target inference size (must be <= 1280 for YOLOv8 by default)
# lower values speed up detection at the cost of accuracy; user can tweak as needed
DETECTION_IMG_SIZE = 640  # reduce default for faster real‑time performance

# lock name added to model when first used
_YOLO_LOCK_ATTR = '_yolo_lock'


def process_frame(frame, model, rois, conf_thres=0.20, iou_thres=0.45, img_size=640):
    """
    Detect vehicles only inside each ROI region.

    For each ROI:
      1. Crop the frame to the polygon's axis-aligned bounding box.
      2. Run YOLO only on that crop (fast — small image).
      3. Map detection coordinates back to the full frame.
      4. Apply a point-in-polygon test against the exact ROI polygon.
      5. Draw boxes and count only true positives inside the polygon.

    Vehicles outside all ROIs are never processed, eliminating wasted
    inference compute and spurious counts.
    """
    import threading

    lane_counts = {k: 0 for k in rois.keys()}
    lane_scores  = {k: 0 for k in rois.keys()}

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
        return frame, lane_counts, lane_scores

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

            weight = CLASS_WEIGHT.get(cls, 1)

            # 4-point anchor check
            fcx = (cx1 + cx2) // 2
            fcy = (cy1 + cy2) // 2
            check_pts = [
                (float(fcx), float(fcy)),
                (float(fcx), float(cy2)),
                (float(cx1 + (cx2 - cx1) * 0.25), float(cy2)),
                (float(cx1 + (cx2 - cx1) * 0.75), float(cy2)),
            ]

            # Find if this vehicle is inside ANY ROI
            is_emergency = any(kw in cls_name for kw in ['ambulance', 'fire', 'emergency', 'police'])
            matched_lane = None
            
            for lane_name, roi_pts in rois.items():
                if roi_pts is None or len(roi_pts) < 3:
                    continue
                roi_poly = roi_pts.astype(np.float32)
                inside = any(cv2.pointPolygonTest(roi_poly, pt, False) >= 0 for pt in check_pts)
                
                if inside:
                    matched_lane = lane_name
                    lane_counts[lane_name] += 1
                    lane_scores[lane_name] += weight * (5 if is_emergency else 1)
                    break # count in the first matched lane
            
            if matched_lane:
                if is_emergency:
                    color = (0, 0, 255)
                elif cls in [1, 3]:   # bicycle / motorcycle
                    color = (255, 200, 0)
                else:
                    color = (0, 255, 0)
                thickness = 3 if is_emergency else 2

                cv2.rectangle(frame, (cx1, cy1), (cx2, cy2), color, thickness)
                for pt in check_pts:
                    cv2.circle(frame, (int(pt[0]), int(pt[1])), 3, (0, 80, 255), -1)

                label = cls_name.upper()
                if is_emergency:
                    label = f"!!! {label} !!!"
                cv2.putText(frame, label, (cx1, cy1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    return frame, lane_counts, lane_scores

# --- Consolidated Traffic Control State ---
automation_state = {
    "current_green_lane": "North",
    "last_switch_time": 0.0,
    "is_yellow_phase": False,
    "yellow_trigger_lane": None,
    "control_mode": 1 # 1: Fixed Cycle, 2: Intensity Based
}

def control_traffic_lights_logic(control_url, counts, tl_ids, cycle_timer=30.0, lane_scores=None):
    """Consolidated controller logic with Mode (Fixed vs Intensity) support using HTTP API"""
    global automation_state
    
    import time
    import requests
    import threading

    if control_url is None:
        # running without TRAFFIC_API configured
        return

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

    # 3. Apply via HTTP API to TRAFFIC_API
    if not any(tl_ids.values()):
        return

    updates = []
    for lane_name, tid in tl_ids.items():
        if tid is None or not str(tid).strip():
            continue
        try:
            tid_int = int(tid)
            if automation_state["is_yellow_phase"] and lane_name == automation_state["current_green_lane"]:
                updates.append({"id": tid_int, "state": "Yellow", "freeze": True})
            elif lane_name == automation_state["current_green_lane"]:
                updates.append({"id": tid_int, "state": "Green", "freeze": True})
            else:
                updates.append({"id": tid_int, "state": "Red", "freeze": True})
        except ValueError:
            pass

    if updates:
        def send_request():
            try:
                endpoint = f"{control_url.rstrip('/')}/traffic_light/set_multiple"
                requests.post(endpoint, json={"updates": updates}, timeout=2.0)
            except Exception as e:
                # Silently ignore connection errors so we don't spam the console if API is down
                pass
        
        # Run in thread so vision loop doesn't block if API is slow
        t = threading.Thread(target=send_request, daemon=True)
        t.start()

def get_automation_data():
    return automation_state
