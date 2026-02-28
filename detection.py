import cv2

def process_frame(frame, model, rois):
    """
    Process a single frame for vehicle detection.
    Draws ROIs, runs inference, draws bounding boxes, and counts vehicles in ROIs.
    Returns:
        frame: the modified frame with annotations
        lane_counts: dictionary of vehicle counts per ROI
    """
    # Initialize counts based on rois keys
    lane_counts = {k: 0 for k in rois.keys()}
    
    # Draw ROI Polygons
    for name, points in rois.items():
        pts = points.reshape((-1, 1, 2))
        cv2.polylines(frame, [pts], isClosed=True, color=(0, 255, 255), thickness=2)
        # Correctly position text near first point
        cv2.putText(frame, name, (int(points[0][0]), int(points[0][1] - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    
    # Run YOLO inference
    if model is not None:
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
                for lane_name, points in rois.items():
                    if cv2.pointPolygonTest(points, (cx, cy), False) >= 0:
                        lane_counts[lane_name] += 1
                        # Draw prominent Green box for COUNTED vehicles
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        cv2.circle(frame, (cx, cy), 5, (0, 0, 255), -1)
                        break 
    
    # Overlay Statistics
    y_pos = 30
    for name, count in lane_counts.items():
        cv2.putText(frame, f"{name} count: {count}", (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        y_pos += 30
        
    return frame, lane_counts
