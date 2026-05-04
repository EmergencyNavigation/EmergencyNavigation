#include <iostream>
#include <fstream>
#include <vector>
#include <cmath>
#include <queue>
#include <limits>
#include <algorithm>
#include <string>
#include "json.hpp"

using namespace std;
using json = nlohmann::json;

struct Hospital {
    string name;
    double lat;
    double lon;
};

double distanceKm(double lat1, double lon1, double lat2, double lon2) {
    const double R = 6371.0;
    double dLat = (lat2 - lat1) * M_PI / 180.0;
    double dLon = (lon2 - lon1) * M_PI / 180.0;

    lat1 *= M_PI / 180.0;
    lat2 *= M_PI / 180.0;

    double a = sin(dLat / 2) * sin(dLat / 2) +
               sin(dLon / 2) * sin(dLon / 2) * cos(lat1) * cos(lat2);

    return R * 2 * atan2(sqrt(a), sqrt(1 - a));
}

vector<Hospital> loadHospitals() {
    vector<Hospital> hospitals;
    ifstream file("hospitals.json");

    if (!file.is_open()) {
        cerr << "Could not open hospitals.json\n";
        return hospitals;
    }

    json data;
    file >> data;

    for (auto &item : data) {
        if (!item.contains("lat") || !item.contains("lon")) continue;

        string name = "Unknown Hospital";
        if (item.contains("tags") && item["tags"].contains("name")) {
            name = item["tags"]["name"];
        }

        hospitals.push_back({
            name,
            item["lat"],
            item["lon"]
        });
    }

    return hospitals;
}

int main(int argc, char* argv[]) {
    if (argc < 3) {
        cout << "{\"error\":\"Usage: ./navigation <lat> <lon>\"}" << endl;
        return 1;
    }

    double userLat = atof(argv[1]);
    double userLon = atof(argv[2]);

    vector<Hospital> hospitals = loadHospitals();

    if (hospitals.empty()) {
        cout << "{\"error\":\"No hospitals found. Run python3 nyctraffic.py first.\"}" << endl;
        return 1;
    }

    Hospital best = hospitals[0];
    double bestDistance = distanceKm(userLat, userLon, best.lat, best.lon);

    for (auto &h : hospitals) {
        double d = distanceKm(userLat, userLon, h.lat, h.lon);
        if (d < bestDistance) {
            best = h;
            bestDistance = d;
        }
    }

    json result;
    result["name"] = best.name;
    result["lat"] = best.lat;
    result["lon"] = best.lon;
    result["distance_km"] = round(bestDistance * 100.0) / 100.0;

    cout << result.dump() << endl;
    return 0;
}