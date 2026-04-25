import requests
import json
import random

# --- CONFIGURATION ---
OVERPASS_URL = "http://overpass-api.de/api/interpreter"
# NYC Bounding Box: (south, west, north, east)
NYC_BOUNDS = "40.47,-74.26,40.92,-73.70"

def fetch_hospitals():
    print("🛰️  Connecting to Overpass API for Hospitals...")
    query = f"""
    [out:json];
    node["amenity"="hospital"]({NYC_BOUNDS});
    out body;
    """
    try:
        response = requests.get(OVERPASS_URL, params={'data': query}, timeout=30)
        if response.status_code == 200:
            data = response.json().get('elements', [])
            with open('hospitals.json', 'w') as f:
                json.dump(data, f, indent=4)
            print(f"✅ Success: Saved {len(data)} hospitals to hospitals.json")
            return data
    except Exception as e:
        print(f"❌ Hospital API Error: {e}")
    return []

def fetch_traffic():
    print("🚦 Fetching Traffic Events (Simulated 511NY)...")
    # In a real scenario, you'd use: requests.get("https://api.511ny.org/api/getevents?key=YOUR_KEY")
    # For your presentation, we generate 'Live Hazards' near the hospitals to test rerouting.
    
    hazards = []
    types = ["Accident", "Flooding", "Road Closure", "Construction"]
    
    # Generate 10 random hazards within NYC coordinates
    for i in range(10):
        hazard = {
            "event_id": 1000 + i,
            "type": random.choice(types),
            "lat": random.uniform(40.5, 40.9),
            "lon": random.uniform(-74.2, -73.8),
            "severity": "High"
        }
        hazards.append(hazard)
        
    with open('hazards.json', 'w') as f:
        json.dump(hazards, f, indent=4)
    print(f"✅ Success: Saved {len(hazards)} live hazards to hazards.json")

if __name__ == "__main__":
    print("--- STARTING DATA INGESTION PIPELINE ---")
    fetch_hospitals()
    fetch_traffic()
    print("--- PIPELINE COMPLETE: READY FOR C++ ENGINE ---")