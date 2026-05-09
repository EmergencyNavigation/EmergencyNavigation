from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import json
import os
from route_engine import rank_destinations

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
DATA_DIR = os.path.join(BASE_DIR, "data")


def data_path(filename):
    return os.path.join(DATA_DIR, filename)

app = Flask(__name__)
CORS(app)

# ---------------- FRONTEND ROUTES ----------------

@app.route("/")
def home():
    return send_from_directory(FRONTEND_DIR, "index.html")

@app.route("/style.css")
def css():
    return send_from_directory(FRONTEND_DIR, "style.css")

@app.route("/app.js")
def js():
    return send_from_directory(FRONTEND_DIR, "app.js")

# ---------------- HELPERS ----------------

def load_json_file(filename):
    path = data_path(filename)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def normalize_place(item):
    if "lat" not in item or "lon" not in item:
        return None

    return {
        "id": item.get("id"),
        "name": item.get("name") or item.get("tags", {}).get("name", "Unknown Destination"),
        "lat": item["lat"],
        "lon": item["lon"],
        "tags": item.get("tags", {}),
        "categories": item.get("categories", []),
        "trauma_level": item.get("trauma_level"),
        "has_er": item.get("has_er", False),
        "is_24_7": item.get("is_24_7", False),
        "operator": item.get("operator"),
        "address": item.get("address"),
        "website": item.get("website"),
        "borough": item.get("borough"),
    }


def normalize_places(raw_items):
    results = []
    for item in raw_items:
        place = normalize_place(item)
        if place:
            results.append(place)
    return results

# ---------------- API ROUTES ----------------

@app.route("/api/status")
def status():
    return jsonify({
        "status": "ok",
        "hospitals_exists": os.path.exists(data_path("hospitals.json")),
        "police_exists": os.path.exists(data_path("police.json")),
        "hazards_exists": os.path.exists(data_path("hazards.json"))
    })

@app.route("/api/hospitals")
def hospitals():
    data = load_json_file("hospitals.json")
    if data is None:
        return jsonify({"error": "hospitals.json not found"}), 404
    return jsonify(normalize_places(data))

@app.route("/api/hazards")
def hazards():
    data = load_json_file("hazards.json")
    if data is None:
        return jsonify({"error": "hazards.json not found"}), 404
    return jsonify(data)

@app.route("/api/police")
def police():
    data = load_json_file("police.json")
    if data is None:
        return jsonify({"error": "police.json not found"}), 404
    return jsonify(data)

@app.route("/api/nearest-er", methods=["POST"])
def nearest_er():
    data = request.get_json() or {}

    lat = data.get("lat")
    lon = data.get("lon")
    service_type = data.get("service_type") or data.get("destinationType") or "hospital"
    emergency_type = data.get("emergency_type") or data.get("emergencyType") or "General Emergency"

    if lat is None or lon is None:
        return jsonify({"error": "lat and lon required"}), 400

    if service_type not in ["hospital", "police"]:
        return jsonify({"error": "service_type must be hospital or police"}), 400

    data_file = "police.json" if service_type == "police" else "hospitals.json"

    raw_destinations = load_json_file(data_file)
    if raw_destinations is None:
        return jsonify({"error": f"{data_file} not found"}), 404

    raw_hazards = load_json_file("hazards.json")
    if raw_hazards is None:
        return jsonify({"error": "hazards.json not found"}), 404

    destinations = normalize_places(raw_destinations)

    ranked = rank_destinations(
        lat,
        lon,
        destinations,
        raw_hazards,
        service_type=service_type,
        emergency_type=emergency_type
    )

    if not ranked:
        return jsonify({"error": "No route found"}), 500

    if service_type == "police":
        decision = "Selected police station using real driving time plus hazard penalty."
    else:
        decision = f"Selected hospital for {emergency_type} using real driving time, hazard penalty, and hospital category priority."

    return jsonify({
        "best": ranked[0],
        "top3": ranked[:3],
        "service_type": service_type,
        "emergency_type": emergency_type,
        "decision": decision
    })

# ---------------- RUN SERVER ----------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Server running at http://127.0.0.1:{port}")
    app.run(debug=True, port=port)
