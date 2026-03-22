import os
import sys
import traci
import threading
import time
from flask import Flask, request, jsonify
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
CORS(app, origins=["http://localhost:8080", "http://localhost:5173", "http://127.0.0.1:8080", "http://127.0.0.1:5173"])


@app.route('/get_signal_data', methods=['GET'])
def get_signal_data():
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
        return jsonify({"signals": signal_data}), 200
    except Exception:
        return jsonify({"signals": [], "status": "waiting"}), 200


@app.route('/get_ambulance_data', methods=['GET'])
def get_ambulance_data():
    ambulance_list = []
    try:
        boundary = traci.simulation.getNetBoundary()
        min_x, min_y = boundary[0]
        max_x, max_y = boundary[1]
        range_x, range_y = max_x - min_x, max_y - min_y

        active_veh = traci.vehicle.getIDList()
        for veh_id in active_veh:
            if veh_id.startswith("amb_"):
                try:
                    raw_x, raw_y = traci.vehicle.getPosition(veh_id)
                    ambulance_list.append({
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
        return jsonify({"ambulances": ambulance_list}), 200
    except Exception:
        return jsonify({"ambulances": []}), 200


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

    try:
        veh_id = f"amb_{int(traci.simulation.getTime())}"
        route_info = traci.simulation.findRoute(start_edge, dest_edge)

        if not route_info.edges:
            return jsonify({"status": "error", "message": "No valid TraCI route found"}), 400

        traci.route.add(f"route_{veh_id}", route_info.edges)
        traci.vehicle.add(vehID=veh_id, routeID=f"route_{veh_id}", typeID="ambulance")
        traci.vehicle.setColor(veh_id, (255, 0, 0, 255))
        traci.vehicle.setSpeedMode(veh_id, 0)

        traci.simulationStep()

        return jsonify({"status": "success", "message": f"Ambulance {veh_id} deployed"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


def run_sumo():
    active_ambulances = {}
    try:
        traci.start(sumoCmd)
        print("--- SUMO Simulation Started and Bridge Active ---")

        while True:
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
                print(f" Log saved for {veh_id}: {duration}s, {info['signals_cleared']} signals cleared")

    except Exception as e:
        print(f"SUMO Thread Error: {e}")


if __name__ == "__main__":
    threading.Thread(target=run_sumo, daemon=True).start()
    print("Bridge ready on http://localhost:5000")
app.run(port=5000, debug=False, use_reloader=False, threaded=True)