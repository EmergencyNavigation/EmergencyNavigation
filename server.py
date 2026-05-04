from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import subprocess
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

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

# ---------------- API ROUTES ----------------

@app.route("/api/status")
def status():
    return jsonify({
        "status": "ok",
        "navigation_exists": os.path.exists("./navigation"),
        "hospitals_exists": os.path.exists("hospitals.json")
    })

@app.route("/api/hospitals")
def hospitals():
    if not os.path.exists("hospitals.json"):
        return jsonify({"error": "hospitals.json not found"}), 404

    with open("hospitals.json") as f:
        data = json.load(f)

    results = []
    for item in data:
        if "lat" in item and "lon" in item:
            results.append({
                "name": item.get("tags", {}).get("name", "Unknown Hospital"),
                "lat": item["lat"],
                "lon": item["lon"]
            })

    return jsonify(results)

@app.route("/api/hazards")
def hazards():
    if not os.path.exists("hazards.json"):
        return jsonify({"error": "hazards.json not found"}), 404

    with open("hazards.json") as f:
        return jsonify(json.load(f))

@app.route("/api/nearest-er", methods=["POST"])
def nearest_er():
    data = request.get_json()
    lat = data.get("lat")
    lon = data.get("lon")

    if lat is None or lon is None:
        return jsonify({"error": "lat and lon required"}), 400

    result = subprocess.run(
        ["./navigation", str(lat), str(lon)],
        capture_output=True,
        text=True
    )

    try:
        return jsonify(json.loads(result.stdout))
    except:
        return jsonify({"error": "C++ returned invalid output", "raw": result.stdout})

# ---------------- RUN SERVER ----------------

if __name__ == "__main__":
    print("Server running at http://127.0.0.1:5000")
    app.run(debug=True, port=5000)