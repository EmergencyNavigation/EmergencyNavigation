"""
NeuroRoute — NYC data builder.

Fetches real hospital + police station locations from Overpass (OpenStreetMap)
and generates simulated road hazards for the demo.

Usage:
    python3 nyctraffic.py                  # same as --all
    python3 nyctraffic.py --all
    python3 nyctraffic.py --hospitals
    python3 nyctraffic.py --police
    python3 nyctraffic.py --hazards
"""

import json
import os
import random
import sys
import time
from datetime import datetime, timedelta

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NYC_311_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
NY511_EVENTS_URL = "https://511ny.org/api/getevents"
NYC_BOUNDS = "40.47,-74.26,40.92,-73.70"
NYC_BBOX = (40.47, -74.26, 40.92, -73.70)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env():
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

HAZARD_311_TYPES = [
    "Sewer",
    "Street Condition",
    "Highway Condition",
    "Traffic Signal Condition",
]


def overpass_query(amenity):
    return f"""
[out:json][timeout:25];
(
  node["amenity"="{amenity}"]({NYC_BOUNDS});
  way["amenity"="{amenity}"]({NYC_BOUNDS});
  relation["amenity"="{amenity}"]({NYC_BOUNDS});
);
out center tags;
"""


USER_AGENT = "NeuroRoute/1.0 (CSC331 student project)"


def fetch_overpass(amenity):
    print(f"  - querying Overpass for amenity={amenity}...")
    response = requests.post(
        OVERPASS_URL,
        data={"data": overpass_query(amenity)},
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    elements = response.json().get("elements", [])
    print(f"  - received {len(elements)} elements")
    return elements


def coords_of(element):
    if element["type"] == "node":
        return element.get("lat"), element.get("lon")
    center = element.get("center", {})
    return center.get("lat"), center.get("lon")


def build_address(tags):
    parts = []
    house = tags.get("addr:housenumber")
    street = tags.get("addr:street")
    if house and street:
        parts.append(f"{house} {street}")
    elif street:
        parts.append(street)
    city = tags.get("addr:city")
    if city:
        parts.append(city)
    state = tags.get("addr:state")
    postcode = tags.get("addr:postcode")
    if state and postcode:
        parts.append(f"{state} {postcode}")
    elif state:
        parts.append(state)
    elif postcode:
        parts.append(postcode)
    return ", ".join(parts) if parts else None


def parse_int(value):
    if value is None:
        return None
    s = str(value).strip()
    return int(s) if s.isdigit() else None


def transform_hospital(element):
    lat, lon = coords_of(element)
    if lat is None or lon is None:
        return None
    tags = element.get("tags", {})
    name = tags.get("name")
    if not name:
        return None

    return {
        "id": element["id"],
        "lat": lat,
        "lon": lon,
        "tags": {"name": name},
        "has_er": tags.get("emergency") == "yes",
        "beds": parse_int(tags.get("beds")),
        "is_24_7": tags.get("opening_hours") == "24/7",
        "operator": tags.get("operator"),
        "address": build_address(tags),
        "phone": tags.get("phone"),
        "website": tags.get("website"),
        "borough": tags.get("addr:city"),
    }


def transform_police(element):
    lat, lon = coords_of(element)
    if lat is None or lon is None:
        return None
    tags = element.get("tags", {})
    name = tags.get("name")
    if not name:
        return None

    return {
        "id": element["id"],
        "lat": lat,
        "lon": lon,
        "tags": {"name": name},
        "operator": tags.get("operator") or tags.get("operator:short"),
        "address": build_address(tags),
        "phone": tags.get("phone"),
        "website": tags.get("website"),
        "borough": tags.get("addr:city"),
    }


def write_json(filename, data):
    path = os.path.join(BASE_DIR, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def fetch_hospitals():
    print("Fetching NYC hospitals...")
    try:
        elements = fetch_overpass("hospital")
    except Exception as e:
        print(f"  ! Overpass failed: {e}")
        return False

    items = [h for h in (transform_hospital(e) for e in elements) if h]
    er_count = sum(1 for h in items if h["has_er"])
    write_json("hospitals.json", items)
    print(f"  - saved {len(items)} hospitals ({er_count} with ER) -> hospitals.json")
    return True


def fetch_police_stations():
    print("Fetching NYC police stations...")
    try:
        elements = fetch_overpass("police")
    except Exception as e:
        print(f"  ! Overpass failed: {e}")
        return False

    items = [p for p in (transform_police(e) for e in elements) if p]
    write_json("police.json", items)
    print(f"  - saved {len(items)} police stations -> police.json")
    return True


def severity_from_311(complaint_type, descriptor):
    desc = (descriptor or "").lower()
    if any(k in desc for k in ("blocked", "closed", "collapse", "flooding")):
        return "High"
    if complaint_type in ("Sewer", "Highway Condition"):
        return "High"
    return "Medium"


def address_from_311(record):
    addr = record.get("incident_address")
    borough = record.get("borough")
    if addr:
        return f"{addr}, {borough}" if borough else addr
    s1 = record.get("intersection_street_1")
    s2 = record.get("intersection_street_2")
    if s1 and s2:
        return f"{s1} & {s2}, {borough}" if borough else f"{s1} & {s2}"
    return borough


def in_nyc(lat, lon):
    s, w, n, e = NYC_BBOX
    return s <= lat <= n and w <= lon <= e


def fetch_hazards_from_311(days_back=7, limit=80):
    print("  - querying NYC 311 Service Requests...")
    since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00")
    types_quoted = ",".join(f"'{t}'" for t in HAZARD_311_TYPES)
    params = {
        "$where": (
            f"complaint_type in({types_quoted}) "
            f"AND status='Open' "
            f"AND created_date > '{since}'"
        ),
        "$limit": limit,
        "$order": "created_date DESC",
    }
    response = requests.get(
        NYC_311_URL,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    raw = response.json()
    print(f"  - received {len(raw)} 311 records")

    hazards = []
    for r in raw:
        try:
            lat = float(r["latitude"])
            lon = float(r["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not in_nyc(lat, lon):
            continue
        hazards.append({
            "event_id": r["unique_key"],
            "source": "NYC 311",
            "type": r.get("complaint_type"),
            "descriptor": r.get("descriptor"),
            "severity": severity_from_311(r.get("complaint_type"), r.get("descriptor")),
            "lat": lat,
            "lon": lon,
            "address": address_from_311(r),
            "borough": r.get("borough"),
            "created": r.get("created_date"),
        })
    return hazards


def severity_from_511(raw_severity):
    s = (raw_severity or "").lower()
    if s in ("severe", "major"):
        return "High"
    if s == "moderate":
        return "Medium"
    return "Low"


def fetch_hazards_from_511ny():
    api_key = os.environ.get("NY511_API_KEY")
    if not api_key:
        return None  # signal: not configured, skip
    print("  - querying 511NY events...")
    response = requests.get(
        NY511_EVENTS_URL,
        params={"key": api_key, "format": "json"},
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    raw = response.json()
    print(f"  - received {len(raw)} 511NY events (NY state)")

    hazards = []
    for r in raw:
        try:
            lat = float(r["Latitude"])
            lon = float(r["Longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not in_nyc(lat, lon):
            continue
        hazards.append({
            "event_id": f"511-{r.get('ID')}",
            "source": "511NY",
            "type": r.get("EventType") or r.get("EventCategory") or "Unknown",
            "descriptor": r.get("Description"),
            "severity": severity_from_511(r.get("Severity")),
            "lat": lat,
            "lon": lon,
            "address": r.get("RoadwayName"),
            "borough": None,
            "created": r.get("StartDate"),
        })
    return hazards


def fetch_hazards_simulated():
    types = ["Accident", "Flooding", "Closure"]
    severities = ["Low", "Medium", "High"]
    return [
        {
            "event_id": f"sim-{1000 + i}",
            "source": "simulated",
            "type": random.choice(types),
            "severity": random.choice(severities),
            "lat": round(random.uniform(40.55, 40.88), 5),
            "lon": round(random.uniform(-74.20, -73.75), 5),
        }
        for i in range(10)
    ]


def fetch_hazards(use_mock=False):
    if use_mock:
        print("Generating hazards (simulated)...")
        hazards = fetch_hazards_simulated()
    else:
        print("Fetching live hazards (NYC 311 + 511NY)...")
        hazards = []

        try:
            h311 = fetch_hazards_from_311()
            hazards.extend(h311)
            print(f"  - NYC 311: {len(h311)} hazards in NYC bbox")
        except Exception as e:
            print(f"  ! NYC 311 fetch failed: {e}")

        try:
            h511 = fetch_hazards_from_511ny()
            if h511 is None:
                print("  - 511NY: skipped (NY511_API_KEY not set in .env)")
            else:
                hazards.extend(h511)
                print(f"  - 511NY: {len(h511)} hazards in NYC bbox")
        except Exception as e:
            print(f"  ! 511NY fetch failed: {e}")

        if not hazards:
            print("  ! no live hazards retrieved, falling back to simulated")
            hazards = fetch_hazards_simulated()

    write_json("hazards.json", hazards)
    sources = sorted({h.get("source", "?") for h in hazards})
    print(f"  - saved {len(hazards)} hazards [sources: {', '.join(sources)}] -> hazards.json")
    return True


def main():
    args = sys.argv[1:] or ["--all"]
    actions = []
    use_mock_hazards = "--hazards-mock" in args

    if "--all" in args:
        actions = ["hospitals", "police", "hazards"]
    else:
        if "--hospitals" in args:
            actions.append("hospitals")
        if "--police" in args:
            actions.append("police")
        if "--hazards" in args or use_mock_hazards:
            actions.append("hazards")

    if not actions:
        print("Usage: python3 nyctraffic.py [--all|--hospitals|--police|--hazards|--hazards-mock]")
        sys.exit(1)

    overpass_calls = 0
    for action in actions:
        if action in ("hospitals", "police"):
            if overpass_calls > 0:
                time.sleep(2)
            overpass_calls += 1
        if action == "hospitals":
            fetch_hospitals()
        elif action == "police":
            fetch_police_stations()
        elif action == "hazards":
            fetch_hazards(use_mock=use_mock_hazards)

    print("\nDone.")


if __name__ == "__main__":
    main()
