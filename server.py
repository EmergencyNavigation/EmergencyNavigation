"""
server.py — NYC Emergency Router  |  REST API Bridge
═══════════════════════════════════════════════════════════════════════════════

ARCHITECTURE — how the three files connect:

  nyctraffic.py
    └─ Runs ONCE (or on refresh) to fetch real hospital data from OpenStreetMap
       (Overpass API) and generate simulated hazards.
    └─ Writes: hospitals.json  +  hazards.json

  navigation  (compiled from navigation.cpp)
    └─ Reads hospitals.json + hazards.json
    └─ Builds a spatial grid graph of NYC
    └─ Runs Dijkstra's algorithm from patient location → nearest safe ER
    └─ Prints a single JSON result to stdout
    └─ Called via subprocess by THIS file — it cannot receive HTTP requests

  server.py  ← YOU ARE HERE
    └─ Flask HTTP server — the ONLY layer the browser talks to
    └─ Receives requests from the Leaflet.js frontend
    └─ Calls the C++ binary via subprocess for ER routing
    └─ Proxies routing requests to OSRM for turn-by-turn directions
    └─ Manages live hazard additions/removals from the UI

  Frontend (index.html / Leaflet.js)
    └─ Sends fetch() / AJAX calls to this server on port 5000
    └─ Renders hospitals, hazards, and the Dijkstra-optimal route on the map

═══════════════════════════════════════════════════════════════════════════════

ENDPOINTS
─────────
  GET  /api/status              health check — confirms binary + data files exist
  GET  /api/hospitals           all hospitals loaded from hospitals.json
  GET  /api/hazards             current hazard list (used for ACTIVE ALERTS panel)
  POST /api/nearest-er          { lat, lon, top? }  → Dijkstra result from C++ binary
  POST /api/route               { from_lat, from_lon, to_lat, to_lon, mode }
                                  → GeoJSON route + steps from OSRM
  POST /api/block-road          { lat, lon, type? } → add a manual road block
  DELETE /api/block-road/<id>   → remove a road block (Unblock button)
  POST /api/refresh-data        → re-run nyctraffic.py to refresh data files

INSTALL & RUN
─────────────
  pip install flask flask-cors requests
  python3 nyctraffic.py          # build hospitals.json + hazards.json first
  g++ -O2 -std=c++17 -I./json/include -o navigation navigation.cpp
  python3 server.py              # starts on http://localhost:5000

"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess
import json
import os
import threading
import requests as req

app = Flask(__name__)
CORS(app)   # Allow cross-origin requests from the Leaflet frontend

# ── File / binary paths (keep everything in the same directory) ─────────────
HOSPITALS_FILE = "hospitals.json"
HAZARDS_FILE   = "hazards.json"
CPP_BINARY     = "./navigation"      # compiled from navigation.cpp

# ── Thread lock for safe concurrent writes to hazards.json ──────────────────
_lock = threading.Lock()


# ───────────────────────────────────────────────────────────────────────────
# HELPERS
# ───────────────────────────────────────────────────────────────────────────

def _load_hazards() -> list:
    if not os.path.exists(HAZARDS_FILE):
        return []
    with open(HAZARDS_FILE) as f:
        return json.load(f)

def _save_hazards(hazards: list):
    with open(HAZARDS_FILE, "w") as f:
        json.dump(hazards, f, indent=4)


# ═══════════════════════════════════════════════════════════════════════════
# STATUS  — lets the frontend show a "backend ready" indicator
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/status")
def status():
    binary_ok    = os.path.isfile(CPP_BINARY) and os.access(CPP_BINARY, os.X_OK)
    hospitals_ok = os.path.isfile(HOSPITALS_FILE)
    hazards_ok   = os.path.isfile(HAZARDS_FILE)
    return jsonify({
        "status":         "ok",
        "cpp_binary":     binary_ok,
        "hospitals_file": hospitals_ok,
        "hazards_file":   hazards_ok,
        "ready":          binary_ok and hospitals_ok,
    })


# ═══════════════════════════════════════════════════════════════════════════
# HOSPITALS  — feed the sidebar list and map markers
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/hospitals")
def get_hospitals():
    if not os.path.exists(HOSPITALS_FILE):
        return jsonify({"error": "hospitals.json not found — run /api/refresh-data"}), 404

    with open(HOSPITALS_FILE) as f:
        raw = json.load(f)

    hospitals = []
    for item in raw:
        name = item.get("tags", {}).get("name", "Unknown Hospital")
        lat  = item.get("lat")
        lon  = item.get("lon")
        if lat and lon:
            hospitals.append({"name": name, "lat": lat, "lon": lon})

    return jsonify(hospitals)


# ═══════════════════════════════════════════════════════════════════════════
# HAZARDS  — feed the ACTIVE ALERTS panel and map overlay
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/hazards")
def get_hazards():
    with _lock:
        return jsonify(_load_hazards())


@app.route("/api/block-road", methods=["POST"])
def block_road():
    """
    Add a road block / hazard manually (BLOCK A ROAD form in the UI).
    Body: { lat, lon, type? }
    Returns the new hazard object so the frontend can immediately show it.
    """
    data = request.get_json(force=True)
    lat  = data.get("lat")
    lon  = data.get("lon")
    if lat is None or lon is None:
        return jsonify({"error": "lat and lon required"}), 400

    with _lock:
        hazards = _load_hazards()
        new_id  = max((h["event_id"] for h in hazards), default=999) + 1
        hazard  = {
            "event_id": new_id,
            "type":     data.get("type", "Road Closure"),
            "lat":      float(lat),
            "lon":      float(lon),
            "severity": data.get("severity", "High"),
            "manual":   True,
        }
        hazards.append(hazard)
        _save_hazards(hazards)

    return jsonify({"success": True, "hazard": hazard}), 201


@app.route("/api/block-road/<int:event_id>", methods=["DELETE"])
def unblock_road(event_id):
    """Remove a road block by its event_id (Unblock button in the UI)."""
    with _lock:
        hazards = _load_hazards()
        before  = len(hazards)
        hazards = [h for h in hazards if h["event_id"] != event_id]
        if len(hazards) == before:
            return jsonify({"error": f"No hazard with event_id {event_id}"}), 404
        _save_hazards(hazards)

    return jsonify({"success": True, "removed_id": event_id})


# ═══════════════════════════════════════════════════════════════════════════
# NEAREST ER  — the core endpoint; delegates to the C++ Dijkstra binary
#
# Flow:
#   1. Frontend sends patient lat/lon (from map click or GPS).
#   2. This endpoint calls the compiled ./navigation binary via subprocess.
#   3. The binary reads hospitals.json + hazards.json, runs Dijkstra, and
#      prints a JSON result to stdout.
#   4. We parse that stdout and forward it to the frontend.
#
# New fields returned by the updated navigation.cpp:
#   { name, lat, lon, distance_km, path_cost_km, route_available }
#   path_cost_km  = Dijkstra optimal path distance (accounts for hazards)
#   distance_km   = straight-line Haversine distance (for display)
#   route_available = false if no hazard-free path exists
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/nearest-er", methods=["POST"])
def nearest_er():
    data = request.get_json(force=True)
    lat  = data.get("lat")
    lon  = data.get("lon")
    top  = int(data.get("top", 1))   # optional: return top-N results

    if lat is None or lon is None:
        return jsonify({"error": "lat and lon are required"}), 400

    if not os.path.isfile(CPP_BINARY) or not os.access(CPP_BINARY, os.X_OK):
        return jsonify({
            "error": f"C++ binary '{CPP_BINARY}' not found or not executable.",
            "fix":   "Run: g++ -O2 -std=c++17 -I./json/include -o navigation navigation.cpp"
        }), 500

    # Build the command — --top=N returns an array instead of single object
    cmd = [CPP_BINARY, str(lat), str(lon)]
    if top > 1:
        cmd.append(f"--top={top}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            cwd=os.path.dirname(os.path.abspath(__file__))  # run in project dir
        )

        # stderr is the debug log from the C++ binary — log it server-side only
        if result.stderr:
            print("[C++ stderr]", result.stderr.strip())

        stdout = result.stdout.strip()
        if not stdout:
            return jsonify({"error": "C++ binary returned no output",
                            "stderr": result.stderr}), 500

        er_data = json.loads(stdout)

        # Surface any error the binary reported
        if isinstance(er_data, dict) and "error" in er_data:
            return jsonify(er_data), 404

        return jsonify(er_data)

    except subprocess.TimeoutExpired:
        return jsonify({"error": "C++ Dijkstra engine timed out (>15 s)"}), 504
    except json.JSONDecodeError:
        return jsonify({"error": "C++ binary returned invalid JSON",
                        "raw": stdout}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# ROUTE  — turn-by-turn directions via OSRM
#
# The C++ binary tells us WHICH hospital is nearest (via Dijkstra on the grid).
# This endpoint provides the actual navigable road route between two points.
# Uses the free OSRM demo server — swap for a local instance in production.
# ═══════════════════════════════════════════════════════════════════════════

OSRM_BASE = "http://router.project-osrm.org/route/v1/driving"

@app.route("/api/route", methods=["POST"])
def get_route():
    """
    Body: { from_lat, from_lon, to_lat, to_lon, mode }
    mode: 'fastest' (default) | 'shortest'

    Returns: { geometry (GeoJSON), distance_m, duration_s, steps[] }
    """
    data     = request.get_json(force=True)
    from_lat = data.get("from_lat")
    from_lon = data.get("from_lon")
    to_lat   = data.get("to_lat")
    to_lon   = data.get("to_lon")
    mode     = data.get("mode", "fastest")

    if None in (from_lat, from_lon, to_lat, to_lon):
        return jsonify({"error": "from_lat, from_lon, to_lat, to_lon all required"}), 400

    # OSRM format: lon,lat (note: reversed)
    coords = f"{from_lon},{from_lat};{to_lon},{to_lat}"
    params = {
        "overview":     "full",
        "geometries":   "geojson",
        "steps":        "true",
        "alternatives": "true" if mode == "shortest" else "false",
    }

    try:
        resp = req.get(f"{OSRM_BASE}/{coords}", params=params, timeout=10)
        resp.raise_for_status()
        osrm = resp.json()

        if osrm.get("code") != "Ok" or not osrm.get("routes"):
            return jsonify({"error": "OSRM returned no routes",
                            "code": osrm.get("code")}), 502

        routes = osrm["routes"]
        route  = (min(routes, key=lambda r: r["distance"])
                  if mode == "shortest" and len(routes) > 1
                  else routes[0])

        steps = []
        for leg in route.get("legs", []):
            for step in leg.get("steps", []):
                m = step.get("maneuver", {})
                steps.append({
                    "instruction": step.get("name", ""),
                    "type":        m.get("type", ""),
                    "modifier":    m.get("modifier", ""),
                    "distance_m":  round(step.get("distance", 0)),
                    "duration_s":  round(step.get("duration", 0)),
                })

        return jsonify({
            "geometry":   route["geometry"],
            "distance_m": round(route["distance"]),
            "duration_s": round(route["duration"]),
            "steps":      steps,
        })

    except req.exceptions.Timeout:
        return jsonify({"error": "OSRM timed out"}), 504
    except req.exceptions.RequestException as e:
        return jsonify({"error": f"OSRM error: {e}"}), 502


# ═══════════════════════════════════════════════════════════════════════════
# REFRESH DATA  — re-run nyctraffic.py to pull fresh data
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/refresh-data", methods=["POST"])
def refresh_data():
    try:
        result = subprocess.run(
            ["python3", "nyctraffic.py"],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        return jsonify({
            "success": result.returncode == 0,
            "stdout":  result.stdout,
            "stderr":  result.stderr,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "nyctraffic.py timed out (>60 s)"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("━" * 62)
    print("  NYC Emergency Router — API Server")
    print("  http://localhost:5000")
    print("━" * 62)
    print()
    print("  Pre-flight checklist:")
    print("  [1] pip install flask flask-cors requests")
    print("  [2] python3 nyctraffic.py")
    print("  [3] g++ -O2 -std=c++17 -I./json/include \\")
    print("          -o navigation navigation.cpp")
    print()
    app.run(debug=True, host="0.0.0.0", port=5000)
