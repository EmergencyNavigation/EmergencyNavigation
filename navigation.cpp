#include <iostream>
#include <fstream>
#include <vector>
#include <string>
#include <unordered_map>
#include <cmath>
#include <iomanip>
#include <nlohmann/json.hpp>

using json = nlohmann::json;
using namespace std;

// --- 1. Data Structures ---
struct Hospital {
    string name;
    double lat, lon;
};

// --- 2. The Spatial Hash Grid ---
class SpatialHashGrid {
private:
    double cellSize; 
    unordered_map<string, bool> blockedCells;

    string getCellKey(double lat, double lon) {
        int x = floor(lat / cellSize);
        int y = floor(lon / cellSize);
        return to_string(x) + "_" + to_string(y);
    }

public:
    SpatialHashGrid(double size) : cellSize(size) {}

    void addHazard(double lat, double lon) {
        blockedCells[getCellKey(lat, lon)] = true;
    }

    bool isAreaBlocked(double lat, double lon) {
        return blockedCells.count(getCellKey(lat, lon)) > 0;
    }
};

// --- 3. The Core Routing Engine ---
class RoutingEngine {
public:
    unordered_map<string, Hospital> hospitalMap;
    SpatialHashGrid grid;

    RoutingEngine() : grid(0.01) {} 

    void loadHospitals(string filename) {
        ifstream file(filename);
        if (!file.is_open()) return;
        json j;
        file >> j;
        for (auto& item : j) {
            // Using .value() to avoid crashes if a tag is missing
            string name = item["tags"].value("name", "Unknown Hospital");
            hospitalMap[name] = {name, item.value("lat", 0.0), item.value("lon", 0.0)};
        }
        cout << "Loaded " << hospitalMap.size() << " hospitals into Hash Map." << endl;
    }

    void loadHazards(string filename) {
        ifstream file(filename);
        if (!file.is_open()) return;
        json j;
        file >> j;
        for (auto& item : j) {
            grid.addHazard(item["lat"], item["lon"]);
        }
        cout << "Hazard grid initialized." << endl;
    }

    // --- 4. The Visualization Function ---
    void printPseudoMap(double minLat, double maxLat, double minLon, double maxLon) {
        cout << "\n--- NYC EMERGENCY SPATIAL GRID ---" << endl;
        cout << "[ H = Hospital | X = Hazard | . = Clear ]\n" << endl;

        // Rows (Latitude)
        for (double lat = maxLat; lat >= minLat; lat -= 0.01) {
            cout << fixed << setprecision(2) << lat << " "; 
            // Columns (Longitude)
            for (double lon = minLon; lon <= maxLon; lon += 0.01) {
                
                bool hasHospital = false;
                for (auto const& [name, h] : hospitalMap) {
                    // Check if hospital is within this grid cell's bounds
                    if (abs(h.lat - lat) < 0.005 && abs(h.lon - lon) < 0.005) {
                        hasHospital = true;
                        break;
                    }
                }

                if (grid.isAreaBlocked(lat, lon)) {
                    cout << " X "; 
                } else if (hasHospital) {
                    cout << " H "; 
                } else {
                    cout << " . "; 
                }
            }
            cout << endl;
        }
        cout << "      ";
        for (double lon = minLon; lon <= maxLon; lon += 0.01) cout << " " << (int)(abs(lon)*100)%100 << " ";
        cout << "\n\n(Grid uses 0.01 degree hashing)\n" << endl;
    }
};

int main(int argc, char* argv[]) {
    // 1. Check for lat/lon arguments from the web server
    if (argc < 3) {
        cout << "{\"error\": \"Missing coordinates\"}" << endl;
        return 1;
    }

    RoutingEngine engine;
    engine.loadHospitals("hospitals.json");
    engine.loadHazards("hazards.json");

    double pLat = atof(argv[1]);
    double pLon = atof(argv[2]);

    // 2. Logic to find the nearest hospital
    string bestHospital = "";
    double min_dist = 999.9;

    for (auto const& [name, h] : engine.hospitalMap) {
        // Skip if blocked by a hazard in the Spatial Hash Grid
        if (engine.grid.isAreaBlocked(h.lat, h.lon)) continue;

        double d = sqrt(pow(h.lat - pLat, 2) + pow(h.lon - pLon, 2));
        if (d < min_dist) {
            min_dist = d;
            bestHospital = name;
        }
    }

    // 3. Output ONLY JSON (No "Pseudo Map" or extra text)
    if (bestHospital != "") {
        cout << "{\"name\": \"" << bestHospital << "\", \"lat\": " 
             << engine.hospitalMap[bestHospital].lat << ", \"lon\": " 
             << engine.hospitalMap[bestHospital].lon << "}" << endl;
    } else {
        cout << "{\"error\": \"No safe path found\"}" << endl;
    }

    return 0;
}