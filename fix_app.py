import re

with open('app.py', 'r') as f:
    code = f.read()

# 1. Imports
code = re.sub(
    r'try:\s+import carla.*?import cv2',
    'import cv2\nimport requests',
    code,
    flags=re.DOTALL
)

# 2. globals
code = code.replace('global_world = None\nglobal_camera = None\nconnection_status = "Disconnected"', 'connection_status = "Disconnected"')

# 3. carla_sensor_callback to disconnect_carla blocks
# Find from def carla_sensor_callback to def bg_connect_carla
start = code.find('def carla_sensor_callback')
end = code.find('def bg_connect_carla():')
if start != -1 and end != -1:
    end_end = code.find('    thread.start()\n', end) + len('    thread.start()\n')
    
    replacement = """def check_carla_control_connection():
    global connection_status
    connection_status = "Connecting..."
    host = config.get("carla_host", "localhost")
    port = config.get("carla_port", 5000)
    if not host:
        connection_status = "Waiting for configuration in Control Panel"
        return
    url = f"http://{host}:{port}/traffic_lights/all"
    try:
        req = requests.get(url, timeout=3.0)
        req.raise_for_status()
        connection_status = "Connected"
        print(f"[HMI] Connected to CARLA_CONTROL at {host}:{port}", flush=True)
    except Exception as e:
        connection_status = "Failed to connect (Check CARLA_CONTROL is running)"
        print(f"[ERR] Failed to connect to CARLA_CONTROL: {e}", flush=True)

def disconnect_carla():
    global connection_status
    connection_status = "Disconnected"
    print("[HMI] Disconnected from CARLA_CONTROL")

def bg_connect_carla():
    threading.Thread(target=check_carla_control_connection, daemon=True).start()
"""
    code = code[:start] + replacement + code[end_end:]

# 4. get_traffic_lights
code = re.sub(
    r'def get_traffic_lights\(.*?filter\(\'\w*traffic_light\w*\'\)',
    '',
    code,
    flags=re.DOTALL
)

# 5. vision_processing_loop
old_vision = """                # Pass scores directly — no stale dict lookup
                if global_world is not None:
                    cycle_time = float(config.get('cycle_timer', 30.0))
                    control_traffic_lights_logic(
                        global_world, lane_counts, TL_IDS,
                        cycle_timer=cycle_time,
                        lane_scores=current_lane_scores
                    )"""

new_vision = """                # Pass scores directly — no stale dict lookup
                if connection_status == "Connected":
                    cycle_time = float(config.get('cycle_timer', 30.0))
                    host = config.get("carla_host", "localhost")
                    port = config.get("carla_port", 5000)
                    control_url = f"http://{host}:{port}"
                    control_traffic_lights_logic(
                        control_url, lane_counts, TL_IDS,
                        cycle_timer=cycle_time,
                        lane_scores=current_lane_scores
                    )"""
code = code.replace(old_vision, new_vision)

# 6. mode2_traffic_control_loop
old_m2 = """            # We must process mode2 controls if we have active mode2 threads.
            # To prevent conflict with main vision loop, let's proxy the current counts to the logic.
            if global_world is not None:
                counts = {d: mode2_counts.get(d, 0) for d in DIRECTIONS}
                scores = {d: mode2_scores.get(d, 0) for d in DIRECTIONS}
                
                # Get cycle timer from main config
                current_config = load_config()
                cycle_time = float(current_config.get('cycle_timer', 30.0))
                
                control_traffic_lights_logic(
                    global_world, counts, TL_IDS,
                    cycle_timer=cycle_time,
                    lane_scores=scores
                )"""

new_m2 = """            # We must process mode2 controls if we have active mode2 threads.
            # To prevent conflict with main vision loop, let's proxy the current counts to the logic.
            if connection_status == "Connected":
                counts = {d: mode2_counts.get(d, 0) for d in DIRECTIONS}
                scores = {d: mode2_scores.get(d, 0) for d in DIRECTIONS}
                
                # Get cycle timer from main config
                current_config = load_config()
                cycle_time = float(current_config.get('cycle_timer', 30.0))
                host = current_config.get("carla_host", "localhost")
                port = current_config.get("carla_port", 5000)
                control_url = f"http://{host}:{port}"
                
                control_traffic_lights_logic(
                    control_url, counts, TL_IDS,
                    cycle_timer=cycle_time,
                    lane_scores=scores
                )"""
code = code.replace(old_m2, new_m2)

# 7. /api/lane_counts
old_api_lc = """    # Real-time traffic light states from world
    tl_states = {}
    if global_world and carla is not None:
        for lane, tid in TL_IDS.items():
            if tid and str(tid).strip():
                try:
                    tl_actor = global_world.get_actor(int(tid))
                    if tl_actor is not None:
                        st = tl_actor.get_state()
                        if st == carla.TrafficLightState.Red: tl_states[lane] = "red"
                        elif st == carla.TrafficLightState.Yellow: tl_states[lane] = "yellow"
                        elif st == carla.TrafficLightState.Green: tl_states[lane] = "green"
                        else: tl_states[lane] = "red"
                    else:
                        tl_states[lane] = "red"
                except:
                    tl_states[lane] = "red"
            else:
                tl_states[lane] = "red"
    else:
        # Fallback if no connection or CARLA not installed
        tl_states = {l: "red" for l in ["North", "South", "East", "West"]}"""

new_api_lc = """    # Real-time traffic light states from automation_state
    tl_states = {l: "red" for l in ["North", "South", "East", "West"]}
    if auto_data.get("is_yellow_phase"):
        green_lane = auto_data.get("current_green_lane")
        if green_lane in tl_states:
            tl_states[green_lane] = "yellow"
    else:
        green_lane = auto_data.get("current_green_lane")
        if green_lane in tl_states:
            tl_states[green_lane] = "green" """
code = code.replace(old_api_lc, new_api_lc)

code = code.replace('global_camera is None and external_feed_src', 'external_feed_src')

# fix global vars definition
code = code.replace('global lane_counts, connection_status, global_world, last_process_time', 'global lane_counts, connection_status, last_process_time')
code = code.replace('global global_camera, external_feed_thread, external_feed_src', 'global external_feed_thread, external_feed_src')
code = code.replace('if global_camera is not None:', 'if False:')

# 8. tl_panel_route
old_tl_auth = """        for lane, tid in new_ids.items():
            if tid and str(tid).strip():
                try:
                    actor_id = int(tid)
                    # Check actor existence in CARLA
                    if global_world:
                        carla_actor = global_world.get_actor(actor_id)
                        if carla_actor is None:
                            invalid_ids.append(f"{lane}:{tid}")
                        else:
                            validated_ids[lane] = actor_id
                    else:
                        # If CARLA not connected, can't validate, but user asked to check if exists
                        # So we might want to warn or prevent save.
                        # For now, if not connected, we warn.
                        invalid_ids.append(f"CARLA_OFFLINE({lane})")
                except ValueError:
                    invalid_ids.append(f"INVALID_NUM({lane}:{tid})")
            else:
                validated_ids[lane] = None"""

new_tl_auth = """        valid_actors = set()
        if connection_status == "Connected":
            host = config.get("carla_host", "localhost")
            port = config.get("carla_port", 5000)
            url = f"http://{host}:{port}/traffic_lights/all"
            try:
                resp = requests.get(url, timeout=2.0)
                if resp.status_code == 200:
                    for tl in resp.json():
                        valid_actors.add(tl.get("id"))
            except:
                pass

        for lane, tid in new_ids.items():
            if tid and str(tid).strip():
                try:
                    actor_id = int(tid)
                    # Check actor existence
                    if connection_status == "Connected":
                        if actor_id not in valid_actors and valid_actors:
                            invalid_ids.append(f"{lane}:{tid}")
                        else:
                            validated_ids[lane] = actor_id
                    else:
                        invalid_ids.append(f"CONTROL_API_OFFLINE({lane})")
                except ValueError:
                    invalid_ids.append(f"INVALID_NUM({lane}:{tid})")
            else:
                validated_ids[lane] = None"""
code = code.replace(old_tl_auth, new_tl_auth)
code = code.replace('global TL_IDS, global_world', 'global TL_IDS')

# 9. /api/tl_test
old_tl_test = """@app.route('/api/tl_test', methods=['POST'])
def tl_test_mode():
    \"\"\"Manually set a traffic light state for testing\"\"\"
    global global_world
    if carla is None:
        return jsonify({"status": "error", "message": "CARLA module missing"})
    if not global_world:
        return jsonify({"status": "error", "message": "CARLA not connected"})
        
    data = request.json or {}
    actor_id = data.get('actor_id')
    state_str = data.get('state', '').lower() # "red", "yellow", "green"
    
    if not actor_id or not state_str:
        return jsonify({"status": "error", "message": "Missing Actor ID or state"})
        
    try:
        tl_actor = global_world.get_actor(int(actor_id))
        if not tl_actor:
            return jsonify({"status": "error", "message": f"Actor {actor_id} not found"})
            
        mapping = {
            "red": carla.TrafficLightState.Red,
            "yellow": carla.TrafficLightState.Yellow,
            "green": carla.TrafficLightState.Green
        }
        
        target_state = mapping.get(state_str.lower())
        if target_state is not None:
            tl_actor.set_state(target_state)
            return jsonify({"status": "success", "message": f"Actor {actor_id} set to {state_str.upper()}"})
        return jsonify({"status": "error", "message": "Invalid state"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})"""

new_tl_test = """@app.route('/api/tl_test', methods=['POST'])
def tl_test_mode():
    \"\"\"Manually set a traffic light state for testing\"\"\"
    if connection_status != "Connected":
        return jsonify({"status": "error", "message": "Not connected to CARLA_CONTROL"})
        
    data = request.json or {}
    actor_id = data.get('actor_id')
    state_str = data.get('state', '').capitalize() # "Red", "Yellow", "Green"
    
    if not actor_id or not state_str:
        return jsonify({"status": "error", "message": "Missing Actor ID or state"})
        
    try:
        host = config.get("carla_host", "localhost")
        port = config.get("carla_port", 5000)
        url = f"http://{host}:{port}/traffic_light/set_multiple"
        
        updates = [{"id": int(actor_id), "state": state_str, "freeze": True}]
        resp = requests.post(url, json={"updates": updates}, timeout=2.0)
        resp.raise_for_status()
        
        return jsonify({"status": "success", "message": f"Actor {actor_id} set to {state_str}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})"""
code = code.replace(old_tl_test, new_tl_test)

# 10. control_panel global vars
code = code.replace('global config, connection_status, global_camera', 'global config, connection_status')

code = code.replace('global latest_frame, lane_counts, global_world, last_process_time', 'global latest_frame, lane_counts, last_process_time')

code = code.replace('if carla is None:', 'if False:')

code = code.replace('elif global_camera:', 'elif connection_status == "Connected":')

code = code.replace('''    except KeyboardInterrupt:
        print("Shutting down... destroying camera.")
        if global_camera:
            try:
                global_camera.destroy()
            except:
                pass''', '    except KeyboardInterrupt:\n        print("Shutting down...")')

with open('app.py', 'w') as f:
    f.write(code)
