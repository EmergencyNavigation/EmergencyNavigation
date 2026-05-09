import math
import requests

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


# Frontend emergencyType label -> hospital category in hospitals.json.
# Categories come from NY State DOH certifications (er/cardiac/stroke/children)
# plus ACS/DOH trauma center designations baked in nyctraffic.py.
EMERGENCY_TO_CATEGORY = {
    "Heart Attack":      "cardiac",
    "Stroke":            "stroke",
    "Accident Trauma":   "trauma",
    "Child Emergency":   "children",
    "General Emergency": "er",
}


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )

    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_name(place):
    return place.get("name") or place.get("tags", {}).get("name", "Unknown Destination")


def _straight_line_route(start_lat, start_lon, end_lat, end_lon):
    """Haversine fallback used when OSRM is unreachable — keeps the app responsive."""
    distance = haversine_km(start_lat, start_lon, end_lat, end_lon)
    return {
        "distance_km": round(distance, 2),
        "duration_min": round(distance * 2.2, 1),
        "geometry": [
            {"lat": start_lat, "lon": start_lon},
            {"lat": end_lat, "lon": end_lon},
        ],
    }


def get_real_route(start_lat, start_lon, end_lat, end_lon):
    """
    Get driving distance/time/geometry from OSRM public server.
    Falls back to straight-line haversine if OSRM is unavailable
    (network error, rate limit, etc.).
    """
    url = f"{OSRM_URL}/{start_lon},{start_lat};{end_lon},{end_lat}"
    params = {"overview": "full", "geometries": "geojson"}
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            return _straight_line_route(start_lat, start_lon, end_lat, end_lon)
        route = data["routes"][0]
        geometry = [
            {"lat": coord[1], "lon": coord[0]}
            for coord in route["geometry"]["coordinates"]
        ]
        return {
            "distance_km": round(route["distance"] / 1000, 2),
            "duration_min": round(route["duration"] / 60, 1),
            "geometry": geometry,
        }
    except Exception as e:
        print(f"  ! OSRM failed ({e}); using straight-line fallback")
        return _straight_line_route(start_lat, start_lon, end_lat, end_lon)


def hazard_penalty(route_geometry, hazards):
    penalty = 0

    for point in route_geometry[::10]:
        for hazard in hazards:
            if "lat" not in hazard or "lon" not in hazard:
                continue

            d = haversine_km(point["lat"], point["lon"], hazard["lat"], hazard["lon"])

            if d < 0.5:
                severity = hazard.get("severity", "Medium")
                if severity == "High":
                    penalty += 7
                elif severity == "Medium":
                    penalty += 4
                else:
                    penalty += 2

    return penalty


def hospital_category_penalty(hospital, emergency_type):
    """
    Lower is better. Prioritizes hospitals whose `categories` array
    contains the specialty required by the emergency type. Falls back
    to any ER, then to a hard penalty for non-ER specialty hospitals
    (psych, cancer, rehab, etc.) so the system still returns a route.
    """
    cats = hospital.get("categories") or []
    required = EMERGENCY_TO_CATEGORY.get(emergency_type, "er")

    if required in cats:
        return 0                # exact specialty match
    if "er" in cats:
        return 8                # any ER (best fallback if specialty not nearby)
    return 18                   # specialty-only hospital, unsuitable for emergencies


def choose_candidates(user_lat, user_lon, destinations, service_type, emergency_type):
    for d in destinations:
        d["straight_distance"] = haversine_km(user_lat, user_lon, d["lat"], d["lon"])

    if service_type == "police":
        return sorted(destinations, key=lambda d: d["straight_distance"])[:8]

    # For hospitals, check slightly more candidates because specialty may beat nearest.
    return sorted(
        destinations,
        key=lambda d: d["straight_distance"] + hospital_category_penalty(d, emergency_type)
    )[:10]


def rank_destinations(user_lat, user_lon, destinations, hazards, service_type="hospital", emergency_type="General Emergency"):
    ranked = []
    candidates = choose_candidates(user_lat, user_lon, destinations, service_type, emergency_type)

    for dest in candidates:
        route = get_real_route(user_lat, user_lon, dest["lat"], dest["lon"])
        if route is None:
            continue

        h_penalty = hazard_penalty(route["geometry"], hazards)
        c_penalty = 0

        if service_type == "hospital":
            c_penalty = hospital_category_penalty(dest, emergency_type)

        score = route["duration_min"] + h_penalty + c_penalty

        ranked.append({
            "name": get_name(dest),
            "lat": dest["lat"],
            "lon": dest["lon"],
            "service_type": service_type,
            "emergency_type": emergency_type,
            "distance_km": route["distance_km"],
            "duration_min": route["duration_min"],
            "hazard_penalty": h_penalty,
            "category_penalty": c_penalty,
            "score": round(score, 2),
            "geometry": route["geometry"]
        })

    ranked.sort(key=lambda x: x["score"])
    return ranked


# Backward compatibility with old server.py imports.
def rank_hospitals(user_lat, user_lon, hospitals, hazards):
    return rank_destinations(user_lat, user_lon, hospitals, hazards, "hospital", "General Emergency")
