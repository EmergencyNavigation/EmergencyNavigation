import math
import requests

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


# Emergency type -> words that suggest suitable hospital capability.
# This is a prototype category layer based on available JSON fields/name/operator.
EMERGENCY_KEYWORDS = {
    "Heart Attack": ["cardiac", "cardiology", "heart", "er", "emergency", "medical center", "hospital"],
    "Stroke": ["stroke", "neurology", "neuro", "er", "emergency", "medical center", "hospital"],
    "Accident Trauma": ["trauma", "er", "emergency", "medical center", "hospital"],
    "Child Emergency": ["children", "child", "pediatric", "pediatrics", "cohen", "montefiore"],
    "General Emergency": []
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


def get_search_text(place):
    fields = [
        get_name(place),
        place.get("operator", ""),
        place.get("address", ""),
        place.get("borough", ""),
        place.get("website", "") or ""
    ]
    return " ".join(str(x).lower() for x in fields if x is not None)


def get_real_route(start_lat, start_lon, end_lat, end_lon):

    distance = haversine_km(
        start_lat,
        start_lon,
        end_lat,
        end_lon
    )

    geometry = [
        {"lat": start_lat, "lon": start_lon},
        {"lat": end_lat, "lon": end_lon}
    ]

    return {
        "distance_km": round(distance, 2),
        "duration_min": round(distance * 2.2, 1),
        "geometry": geometry
    }


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
    Lower is better. This softly prioritizes hospitals that match emergency type.
    It does not completely remove other hospitals, so the app can still return a route.
    """
    if emergency_type == "General Emergency":
        return 0 if hospital.get("has_er") else 8

    text = get_search_text(hospital)
    keywords = EMERGENCY_KEYWORDS.get(emergency_type, [])
    matches = any(word in text for word in keywords)

    # ER/24-7 hospitals should generally be preferred for emergency cases.
    er_bonus = 0
    if hospital.get("has_er"):
        er_bonus -= 6
    if hospital.get("is_24_7"):
        er_bonus -= 2

    if matches:
        return max(0, 2 + er_bonus)

    # For child emergency, non-child hospitals are a worse fit.
    if emergency_type == "Child Emergency":
        return 18

    return max(6, 12 + er_bonus)


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
