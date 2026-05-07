"""
NeuroRoute — NYC data builder.

Hospitals: NY State Health Open Data (general info + certifications joined by fac_id),
           addresses geocoded via free US Census Bureau geocoder (cached locally).
           Each hospital tagged with categories: er / cardiac / stroke / children / trauma.
Police:    Overpass (OpenStreetMap).
Hazards:   NYC 311 + 511NY (live) with simulated fallback.

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
from collections import Counter
from datetime import datetime, timedelta

import requests

USER_AGENT = "NeuroRoute/1.0 (CSC331 student project)"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NYC_311_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
NY511_EVENTS_URL = "https://511ny.org/api/getevents"
NY_HEALTH_FACILITY_INFO_URL = "https://health.data.ny.gov/resource/vn5v-hh5r.json"
NY_HEALTH_FACILITY_CERT_URL = "https://health.data.ny.gov/resource/2g9y-7kqm.json"
CENSUS_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

NYC_BOUNDS = "40.47,-74.26,40.92,-73.70"
NYC_BBOX = (40.47, -74.26, 40.92, -73.70)
NYC_COUNTIES = ["New York", "Kings", "Queens", "Bronx", "Richmond"]

COUNTY_TO_BOROUGH = {
    "New York": "Manhattan",
    "Kings": "Brooklyn",
    "Queens": "Queens",
    "Bronx": "Bronx",
    "Richmond": "Staten Island",
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GEOCODE_CACHE_PATH = os.path.join(BASE_DIR, "geocode_cache.json")

HAZARD_311_TYPES = [
    "Sewer",
    "Street Condition",
    "Highway Condition",
    "Traffic Signal Condition",
]

# NY State Health Facility Cert "attribute_value" -> our category.
# Multiple cert types can map to the same category (collapsed via set on fac_id).
CERT_TO_CATEGORY = {
    "Emergency Department": "er",
    "Cardiac Catheterization - Percutaneous Coronary Intervention (PCI)": "cardiac",
    "Cardiac Catheterization - Adult Diagnostic": "cardiac",
    "Cardiac Surgery - Adult": "cardiac",
    "Primary Stroke Center": "stroke",
    "Comprehensive Stroke Center": "stroke",
    "Thrombectomy Capable Stroke Center": "stroke",
    "Pediatric Intensive Care": "children",
    "Pediatric ICU": "children",
    "Pediatric": "children",
}

# NYC trauma centers — substring keywords matched (case-insensitive) against NY State facility_name.
# Source: NYC FDNY 911 EMS Trauma Triage Protocol + ACS Verified Trauma Centers.
# https://www.facs.org/quality-programs/trauma/quality/verification-review-and-consultation-program/
# Last verified: 2026-05.
NYC_TRAUMA_KEYWORDS = [
    ("Bellevue", "Level I"),
    ("Kings County", "Level I"),
    ("Lincoln Medical", "Level I"),
    ("Jacobi", "Level I"),
    ("Elmhurst Hospital Center", "Level I"),
    ("Harlem Hospital", "Level I"),
    ("Weill Cornell", "Level I"),
    ("Maimonides Medical Center", "Level I"),
    ("Staten Island University Hosp-North", "Level I"),
    ("Jamaica Hospital Medical Center", "Level I"),
]

ORDINAL_WORDS = {
    "First": "1st", "Second": "2nd", "Third": "3rd", "Fourth": "4th",
    "Fifth": "5th", "Sixth": "6th", "Seventh": "7th", "Eighth": "8th",
    "Ninth": "9th", "Tenth": "10th", "Eleventh": "11th", "Twelfth": "12th",
}


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


def write_json(filename, data):
    path = os.path.join(BASE_DIR, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def in_nyc(lat, lon):
    s, w, n, e = NYC_BBOX
    return s <= lat <= n and w <= lon <= e


def socrata_get(url, params, token_env=None):
    headers = {"User-Agent": USER_AGENT}
    if token_env:
        token = os.environ.get(token_env)
        if token:
            headers["X-App-Token"] = token
    response = requests.get(url, params=params, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()


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


def normalize_ordinals(address):
    return " ".join(ORDINAL_WORDS.get(w, w) for w in address.split())


def _geocode_census(address):
    time.sleep(0.3)
    try:
        response = requests.get(
            CENSUS_GEOCODER_URL,
            params={"address": address, "benchmark": "Public_AR_Current", "format": "json"},
            timeout=20,
        )
        response.raise_for_status()
        matches = response.json().get("result", {}).get("addressMatches", [])
        if matches:
            coords = matches[0].get("coordinates", {})
            lat, lon = coords.get("y"), coords.get("x")
            if lat is not None and lon is not None:
                return lat, lon
    except Exception as e:
        print(f"  ! census geocode error for '{address}': {e}")
    return None, None


def _geocode_nominatim(address):
    time.sleep(1.1)  # Nominatim rate limit: 1 req/sec
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        response.raise_for_status()
        results = response.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:
        print(f"  ! nominatim geocode error for '{address}': {e}")
    return None, None


def geocode_address(address, cache, name=None):
    cached = cache.get(address)
    if cached and cached.get("lat") is not None:
        return cached["lat"], cached["lon"]

    lat, lon = _geocode_census(address)
    if lat is None:
        lat, lon = _geocode_nominatim(address)
    if lat is None:
        normalized = normalize_ordinals(address)
        if normalized != address:
            lat, lon = _geocode_nominatim(normalized)
    if lat is None and name:
        lat, lon = _geocode_nominatim(f"{name}, NYC")

    cache[address] = {"lat": lat, "lon": lon}
    return lat, lon


def load_geocode_cache():
    if not os.path.exists(GEOCODE_CACHE_PATH):
        return {}
    try:
        with open(GEOCODE_CACHE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ! geocode cache unreadable ({e}), starting fresh")
        return {}


def save_geocode_cache(cache):
    with open(GEOCODE_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def _count_valid_cache_entries(cache):
    return sum(1 for v in cache.values() if v.get("lat") is not None)


def build_ny_address(record):
    address1 = record.get("address1")
    if not address1:
        return None
    parts = [address1]
    city = record.get("city")
    if city:
        parts.append(city)
    zip_code = record.get("fac_zip")
    parts.append(f"NY {zip_code}" if zip_code else "NY")
    return ", ".join(parts)


def fetch_ny_hospitals():
    print("  - querying NY State Health Facility General Info...")
    counties_quoted = ",".join(f"'{c}'" for c in NYC_COUNTIES)
    params = {
        "$where": f"county in({counties_quoted}) AND description='Hospital'",
        "$limit": 5000,
    }
    records = socrata_get(NY_HEALTH_FACILITY_INFO_URL, params, "NY_STATE_APP_TOKEN")
    print(f"  - received {len(records)} records")

    seen = set()
    unique = []
    for r in records:
        fac_id = r.get("fac_id")
        if not fac_id or fac_id in seen:
            continue
        seen.add(fac_id)
        unique.append(r)
    print(f"  - {len(unique)} unique hospitals after dedup")
    return unique


def fetch_ny_certifications():
    print("  - querying NY State Health Facility Certifications...")
    counties_quoted = ",".join(f"'{c}'" for c in NYC_COUNTIES)
    params = {
        "$where": f"county in({counties_quoted})",
        "$limit": 50000,
    }
    records = socrata_get(NY_HEALTH_FACILITY_CERT_URL, params, "NY_STATE_APP_TOKEN")
    print(f"  - received {len(records)} certification rows")

    categories_by_fac = {}
    for r in records:
        category = CERT_TO_CATEGORY.get(r.get("attribute_value"))
        if not category:
            continue
        fac_id = r.get("fac_id")
        if not fac_id:
            continue
        categories_by_fac.setdefault(fac_id, set()).add(category)
    return categories_by_fac


def apply_trauma_keywords(hospitals, categories_by_fac):
    matched_keywords = set()
    levels_by_fac = {}
    for h in hospitals:
        name = (h.get("facility_name") or "").lower()
        for keyword, level in NYC_TRAUMA_KEYWORDS:
            if keyword.lower() in name:
                fac_id = h.get("fac_id")
                if fac_id:
                    categories_by_fac.setdefault(fac_id, set()).add("trauma")
                    levels_by_fac[fac_id] = level
                    matched_keywords.add(keyword)
                break
    print(f"  - matched {len(matched_keywords)}/{len(NYC_TRAUMA_KEYWORDS)} trauma centers")
    for keyword, level in NYC_TRAUMA_KEYWORDS:
        if keyword not in matched_keywords:
            print(f"    ! trauma keyword not found in NY State data: '{keyword}' ({level})")
    return levels_by_fac


def transform_ny_hospital(record, categories, trauma_level, cache):
    fac_id = record.get("fac_id")
    name = record.get("facility_name")
    if not name or not fac_id:
        return None
    address = build_ny_address(record)
    if not address:
        return None
    lat, lon = geocode_address(address, cache, name=name)
    if lat is None or lon is None:
        return None

    cats_list = sorted(categories) if categories else []

    result = {
        "id": int(fac_id) if fac_id.isdigit() else fac_id,
        "lat": lat,
        "lon": lon,
        "tags": {"name": name},
        "categories": cats_list,
        "has_er": "er" in cats_list,
        "operator": record.get("operator_name"),
        "address": address,
        "phone": record.get("fac_phone"),
        "borough": COUNTY_TO_BOROUGH.get(record.get("county")),
    }
    if trauma_level:
        result["trauma_level"] = trauma_level
    return result


def fetch_hospitals():
    print("Fetching NYC hospitals from NY State Open Data...")
    try:
        ny_hospitals = fetch_ny_hospitals()
        categories_by_fac = fetch_ny_certifications()
    except Exception as e:
        print(f"  ! NY State fetch failed: {e}")
        return False

    print("  - applying NYC trauma center designations...")
    trauma_levels = apply_trauma_keywords(ny_hospitals, categories_by_fac)

    print("  - geocoding addresses (Census + Nominatim fallback)...")
    cache = load_geocode_cache()
    valid_before = _count_valid_cache_entries(cache)

    items = []
    skipped = 0
    for h in ny_hospitals:
        fac_id = h.get("fac_id")
        cats = categories_by_fac.get(fac_id, set())
        trauma_level = trauma_levels.get(fac_id)
        result = transform_ny_hospital(h, cats, trauma_level, cache)
        if result:
            items.append(result)
        else:
            skipped += 1

    valid_after = _count_valid_cache_entries(cache)
    save_geocode_cache(cache)
    print(f"  - geocoded {valid_after - valid_before} new addresses ({valid_after} valid in cache)")
    if skipped:
        print(f"  - skipped {skipped} hospitals (missing data or geocoding failed)")

    write_json("hospitals.json", items)
    print(f"  - saved {len(items)} hospitals -> hospitals.json")

    cat_counts = Counter()
    for h in items:
        for c in h["categories"]:
            cat_counts[c] += 1
    for cat in ["er", "cardiac", "stroke", "children", "trauma"]:
        n = cat_counts.get(cat, 0)
        pct = (100 * n / len(items)) if items else 0
        print(f"      {cat:>8}: {n:>3} ({pct:.0f}%)")
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
    raw = socrata_get(NYC_311_URL, params, "NYC_OPEN_DATA_APP_TOKEN")
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

    for action in actions:
        if action == "hospitals":
            fetch_hospitals()
        elif action == "police":
            fetch_police_stations()
        elif action == "hazards":
            fetch_hazards(use_mock=use_mock_hazards)

    print("\nDone.")


if __name__ == "__main__":
    main()
