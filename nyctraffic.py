import requests
import json
import random

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NYC_BOUNDS = "40.47,-74.26,40.92,-73.70"

def fetch_hospitals():
    print("Fetching NYC hospitals...")

    query = f"""
    [out:json][timeout:25];
    node["amenity"="hospital"]({NYC_BOUNDS});
    out body;
    """

    try:
        response = requests.get(OVERPASS_URL, params={"data": query}, timeout=60)

        print("Status code:", response.status_code)

        if response.status_code != 200:
            print("API error:")
            print(response.text[:500])
            return

        data = response.json().get("elements", [])

        with open("hospitals.json", "w") as f:
            json.dump(data, f, indent=4)

        print(f"Saved {len(data)} hospitals to hospitals.json")

    except Exception as e:
        print("Error fetching hospitals:", e)

def fetch_hazards():
    print("Generating hazards...")
    hazards = []

    for i in range(10):
        hazards.append({
            "event_id": 1000 + i,
            "type": random.choice(["Accident", "Flooding", "Closure"]),
            "lat": random.uniform(40.5, 40.9),
            "lon": random.uniform(-74.2, -73.8),
            "severity": "High"
        })

    with open("hazards.json", "w") as f:
        json.dump(hazards, f, indent=4)

    print("Saved hazards.json")

if __name__ == "__main__":
    fetch_hospitals()
    fetch_hazards()