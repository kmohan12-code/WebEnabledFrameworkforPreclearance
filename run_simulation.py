import os
import sys
import traci
import threading
import time
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
 
if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    sys.path.append(tools)
else:
    sys.exit("Please declare environment variable 'SUMO_HOME'")
 
SUMO_CONFIG = "multi_intersection.sumocfg"
GUI_MODE = True
sumoBinary = "sumo-gui" if GUI_MODE else "sumo"
sumoCmd = [sumoBinary, "-c", SUMO_CONFIG, "--start", "--quit-on-end"]
 
emergency_logs = []
 
app = Flask(__name__)
 
# FIX: Broaden CORS to cover every port Vite might use, and both
# localhost / 127.0.0.1 forms. The browser treats these as different
# origins even though they resolve to the same IP.
CORS(app, origins=[
    "http://localhost:8080", "http://127.0.0.1:8080",
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:3000", "http://127.0.0.1:3000",
])
 
# ─────────────────────────────────────────────────────────────────────────────
# FIX 1: Thread lock + shared cache
#
# Previously: every HTTP request from the frontend called traci.* directly.
# TraCI is NOT thread-safe — calling it from Flask threads while run_sumo()
# is mid-step causes race conditions, crashes, and slow/empty responses.
#
# Fix: run_sumo() writes results into these shared dicts after each step.
# Flask routes just READ from the cache — they never touch TraCI directly.
# The lock protects the cache during writes.
# ─────────────────────────────────────────────────────────────────────────────
_traci_lock = threading.Lock()
_signal_cache = []       # updated every sim step
_ambulance_cache = []    # updated every sim step
_sumo_ready = False      # False until traci.start() succeeds
 
 
# ─────────────────────────────────────────────────────────────────────────────
# FIX 2: Server-Sent Events endpoint replaces frontend polling
#
# Previously: frontend called fetch() every 1000ms → new HTTP connection each
# time → slow, and each request had to wait for the next response.
#
# Fix: SSE keeps ONE persistent HTTP connection open. The server pushes fresh
# data to the browser the moment it's ready — no repeated reconnects, no wait.
# No new Python library needed — SSE is plain HTTP with text/event-stream.
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/stream_signals')
def stream_signals():
    def event_stream():
        import json
        last_sent = None
        while True:
            with _traci_lock:
                current = list(_signal_cache)
                ready = _sumo_ready
            
            # Only push when data actually changed — saves bandwidth
            if ready and current != last_sent:
                payload = json.dumps({"signals": current})
                # SSE format: must start with "data: " and end with \n\n
                yield f"data: {payload}\n\n"
                last_sent = current
            
            time.sleep(0.3)   # check for changes 3x/second — fast enough, not spammy
 
    return Response(
        event_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',   # prevents nginx from buffering the stream
        }
    )
 
 
@app.route('/stream_ambulances')
def stream_ambulances():
    def event_stream():
        import json
        last_sent = None
        while True:
            with _traci_lock:
                current = list(_ambulance_cache)
                ready = _sumo_ready
 
            if ready and current != last_sent:
                payload = json.dumps({"ambulances": current})
                yield f"data: {payload}\n\n"
                last_sent = current
 
            time.sleep(0.3)
 
    return Response(
        event_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )
 
 
# Keep the old polling endpoints as fallback — they now read from cache safely
@app.route('/get_signal_data', methods=['GET'])
def get_signal_data():
    with _traci_lock:
        return jsonify({"signals": list(_signal_cache)}), 200
 
 
@app.route('/get_ambulance_data', methods=['GET'])
def get_ambulance_data():
    with _traci_lock:
        return jsonify({"ambulances": list(_ambulance_cache)}), 200
 
 
@app.route('/get_logs', methods=['GET'])
def get_logs():
    return jsonify({"logs": emergency_logs}), 200
 
 
@app.route('/spawn_ambulance', methods=['POST'])
def spawn_ambulance():
    data = request.json
    start_edge = data.get('edge_id')
    dest_edge = data.get('destination_id')
 
    if start_edge == dest_edge:
        return jsonify({"status": "error", "message": "Start and End IDs must be different"}), 400
 
    # Queue the spawn — run_sumo() picks it up and executes it safely on the
    # TraCI thread, avoiding the race condition from calling traci.* here
    _spawn_queue.append({"start": start_edge, "dest": dest_edge})
    return jsonify({"status": "success", "message": "Spawn queued"}), 200
 
 
# Queue for ambulance spawns requested via HTTP while SUMO loop is running
_spawn_queue = []
 
 
def _update_caches():
    """Called inside run_sumo() after each simulationStep — safe to use TraCI here."""
    global _signal_cache, _ambulance_cache
 
    # --- Signal cache ---
    signal_data = []
    try:
        tls_ids = traci.trafficlight.getIDList()
        current_time = traci.simulation.getTime()
        active_veh = traci.vehicle.getIDList()
 
        for tls_id in tls_ids:
            state = traci.trafficlight.getRedYellowGreenState(tls_id).lower()
            phase = "green" if 'g' in state else "red"
            if 'y' in state:
                phase = "yellow"
 
            next_switch = traci.trafficlight.getNextSwitch(tls_id)
            remaining = max(0, int(next_switch - current_time))
 
            is_priority = False
            for veh_id in active_veh:
                if veh_id.startswith("amb_"):
                    try:
                        next_tls = traci.vehicle.getNextTLS(veh_id)
                        if next_tls and next_tls[0][0] == tls_id and next_tls[0][2] < 60:
                            is_priority = True
                    except:
                        continue
 
            signal_data.append({
                "id": tls_id,
                "name": f"Junction {tls_id}",
                "phase": phase,
                "timeRemaining": remaining,
                "priority": is_priority,
                "status": "optimized" if is_priority else "normal"
            })
    except Exception:
        pass
 
    # --- Ambulance cache ---
    ambulance_data = []
    try:
        boundary = traci.simulation.getNetBoundary()
        min_x, min_y = boundary[0]
        max_x, max_y = boundary[1]
        range_x = max_x - min_x
        range_y = max_y - min_y
 
        for veh_id in traci.vehicle.getIDList():
            if veh_id.startswith("amb_"):
                try:
                    raw_x, raw_y = traci.vehicle.getPosition(veh_id)
                    ambulance_data.append({
                        "id": veh_id,
                        "x": ((raw_x - min_x) / range_x) * 100,
                        "y": ((raw_y - min_y) / range_y) * 100,
                        "speed": round(traci.vehicle.getSpeed(veh_id) * 3.6, 1),
                        "edge": traci.vehicle.getRoadID(veh_id),
                        "co2": round(traci.vehicle.getCO2Emission(veh_id), 1),
                        "fuel": round(traci.vehicle.getFuelConsumption(veh_id), 1),
                        "waiting": round(traci.vehicle.getWaitingTime(veh_id), 1)
                    })
                except traci.exceptions.TraCIException:
                    continue
    except Exception:
        pass
 
    with _traci_lock:
        _signal_cache = signal_data
        _ambulance_cache = ambulance_data
 
 
def run_sumo():
    global _sumo_ready
    active_ambulances = {}
 
    # FIX: Retry loop — "Retrying in 1 seconds" in your terminal meant
    # traci.start() was crashing and the thread was dying silently.
    # Now it retries up to 10 times with a 2s gap between attempts.
    for attempt in range(10):
        try:
            traci.start(sumoCmd)
            break
        except Exception as e:
            print(f"[SUMO] Start attempt {attempt+1}/10 failed: {e}. Retrying...")
            time.sleep(2)
    else:
        print("[SUMO] Could not start after 10 attempts. Check SUMO_HOME and .sumocfg path.")
        return
 
    _sumo_ready = True
    print("--- SUMO Simulation Started and Bridge Active ---")
 
    try:
        while True:
            # Process any queued ambulance spawns before stepping
            while _spawn_queue:
                job = _spawn_queue.pop(0)
                try:
                    veh_id = f"amb_{int(traci.simulation.getTime())}"
                    route_info = traci.simulation.findRoute(job["start"], job["dest"])
                    if route_info.edges:
                        traci.route.add(f"route_{veh_id}", route_info.edges)
                        traci.vehicle.add(vehID=veh_id, routeID=f"route_{veh_id}", typeID="ambulance")
                        traci.vehicle.setColor(veh_id, (255, 0, 0, 255))
                        traci.vehicle.setSpeedMode(veh_id, 0)
                        print(f"[SUMO] Spawned {veh_id}")
                except Exception as e:
                    print(f"[SUMO] Spawn failed: {e}")
 
            traci.simulationStep()
            current_time = traci.simulation.getTime()
            current_ids = traci.vehicle.getIDList()
 
            for veh_id in current_ids:
                if veh_id.startswith("amb_"):
                    try:
                        edge = traci.vehicle.getRoadID(veh_id)
                        if veh_id not in active_ambulances:
                            active_ambulances[veh_id] = {
                                "start_time": current_time,
                                "start_edge": edge,
                                "signals_cleared": 0
                            }
                        next_tls = traci.vehicle.getNextTLS(veh_id)
                        if next_tls:
                            tls_id, _, dist, _ = next_tls[0]
                            if dist < 60:
                                current_logic = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)
                                phase_len = len(current_logic[0].phases[0].state)
                                traci.trafficlight.setRedYellowGreenState(tls_id, "G" * phase_len)
                                active_ambulances[veh_id]["signals_cleared"] += 1
                    except:
                        continue
 
            finished = [v for v in active_ambulances if v not in current_ids]
            for veh_id in finished:
                info = active_ambulances.pop(veh_id)
                duration = int(current_time - info["start_time"])
                normal_time = int(duration * 1.4)
                time_saved = max(0, normal_time - duration)
                emergency_logs.append({
                    "id": f"ER-{len(emergency_logs)+1:03d}",
                    "ambulanceId": veh_id,
                    "date": time.strftime("%Y-%m-%d"),
                    "time": time.strftime("%H:%M"),
                    "from": info["start_edge"],
                    "to": "Destination",
                    "normalTime": f"{normal_time} s",
                    "optimizedTime": f"{duration} s",
                    "timeSaved": f"{time_saved} s",
                    "signalsCleared": info["signals_cleared"]
                })
                print(f"[SUMO] Log saved for {veh_id}: {duration}s, {info['signals_cleared']} signals cleared")
 
            _update_caches()
 
    except Exception as e:
        print(f"SUMO Thread Error: {e}")
 
 
if __name__ == "__main__":
    threading.Thread(target=run_sumo, daemon=True).start()
    print("Bridge ready on http://localhost:5000")
    # FIX: host="0.0.0.0" makes Flask listen on ALL interfaces.
    # Previously it bound to 127.0.0.1 only — requests from the browser
    # using "localhost" sometimes failed depending on OS DNS resolution.
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True)