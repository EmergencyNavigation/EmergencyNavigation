import requests
import math


OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


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


def get_real_route(start_lat, start_lon, end_lat, end_lon):
    """
    Gets real driving route from OSRM.
    Returns distance, duration, and route geometry.
    """

    url = (
        f"{OSRM_URL}/"
        f"{start_lon},{start_lat};{end_lon},{end_lat}"
        f"?overview=full&geometries=geojson"
    )

    try:
        response = requests.get(url, timeout=10)
        data = response.json()

        if data.get("code") != "Ok":
            return None

        route = data["routes"][0]

        geometry = [
            {"lat": coord[1], "lon": coord[0]}
            for coord in route["geometry"]["coordinates"]
        ]

        return {
            "distance_km": round(route["distance"] / 1000, 2),
            "duration_min": round(route["duration"] / 60, 1),
            "geometry": geometry
        }

    except Exception as e:
        print("OSRM error:", e)
        return None


def hazard_penalty(route_geometry, hazards):
    """
    Adds penalty if route passes near hazards.
    Simple prototype: checks route points near hazard points.
    """

    penalty = 0

    for point in route_geometry[::10]:  # check every 10th route point
        for hazard in hazards:
            d = haversine_km(
                point["lat"],
                point["lon"],
                hazard["lat"],
                hazard["lon"]
            )

            if d < 0.5:
                penalty += 5

    return penalty


def rank_hospitals(user_lat, user_lon, hospitals, hazards):
    ranked = []

    # Use top 8 nearest by straight-line distance first
    # This avoids calling OSRM for all hospitals.
    for h in hospitals:
        h["straight_distance"] = haversine_km(
            user_lat, user_lon, h["lat"], h["lon"]
        )

    candidates = sorted(hospitals, key=lambda h: h["straight_distance"])[:8]

    for hospital in candidates:
        route = get_real_route(
            user_lat,
            user_lon,
            hospital["lat"],
            hospital["lon"]
        )

        if route is None:
            continue

        penalty = hazard_penalty(route["geometry"], hazards)

        score = route["duration_min"] + penalty

        ranked.append({
            "name": hospital["name"],
            "lat": hospital["lat"],
            "lon": hospital["lon"],
            "distance_km": route["distance_km"],
            "duration_min": route["duration_min"],
            "hazard_penalty": penalty,
            "score": round(score, 2),
            "geometry": route["geometry"]
        })

    ranked.sort(key=lambda x: x["score"])

    return ranked