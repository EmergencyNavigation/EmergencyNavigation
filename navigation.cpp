/**
 * navigation.cpp — NYC Emergency Router
 * ──────────────────────────────────────────────────────────────────────────
 * Data pipeline (nyctraffic.py) writes hospitals.json + hazards.json.
 * This binary reads those files, builds a spatial grid graph over NYC,
 * and runs Dijkstra's algorithm to find the optimal (lowest-cost) path
 * from the patient's location to the nearest unblocked ER.
 *
 * ALGORITHM OVERVIEW
 * ──────────────────
 *  1. Discretise NYC into a grid of 0.01° × 0.01° cells (~1 km each).
 *  2. Each cell is a graph NODE.  Adjacent cells (8-directional) are EDGES
 *     weighted by Haversine distance between their centres.
 *  3. Cells that contain a hazard from hazards.json are BLOCKED —
 *     removed from the graph (infinite cost, cannot be traversed).
 *  4. Dijkstra's algorithm (min-heap priority queue) runs from the patient's
 *     cell, expanding the cheapest-cost neighbour first.
 *  5. Every hospital cell is scored by its total Dijkstra path cost (km).
 *     Hospitals are ranked and returned as JSON to stdout.
 *
 * USAGE
 * ─────
 *   ./navigation <lat> <lon>           → nearest single safe ER  (JSON object)
 *   ./navigation <lat> <lon> --top=3   → top-3 nearest safe ERs  (JSON array)
 *
 *  ALL debug/info output goes to stderr — never stdout — so server.py can
 *  parse stdout as clean JSON without any interference.
 *
 * BUILD
 * ─────
 *   mkdir -p json/include/nlohmann
 *   curl -L https://github.com/nlohmann/json/releases/download/v3.11.3/json.hpp \
 *        -o json/include/nlohmann/json.hpp
 *   g++ -O2 -std=c++17 -I./json/include -o navigation navigation.cpp
 * ──────────────────────────────────────────────────────────────────────────
 */

#include <iostream>
#include <fstream>
#include <vector>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <queue>
#include <cmath>
#include <iomanip>
#include <algorithm>
#include <limits>
#include <nlohmann/json.hpp>

using json = nlohmann::json;
using namespace std;

// ═══════════════════════════════════════════════════════════════════════════
// 1.  CONSTANTS
// ═══════════════════════════════════════════════════════════════════════════

static const double EARTH_RADIUS_KM = 6371.0;
static const double DEG_TO_RAD      = M_PI / 180.0;
static const double INF             = numeric_limits<double>::infinity();

// NYC bounding box — must match nyctraffic.py NYC_BOUNDS = "40.47,-74.26,40.92,-73.70"
static const double MIN_LAT =  40.47;
static const double MAX_LAT =  40.92;
static const double MIN_LON = -74.26;
static const double MAX_LON = -73.70;

// Grid resolution: 0.01° ≈ 1.1 km per cell — matches the original SpatialHashGrid
static const double CELL_SIZE = 0.01;

// Pre-computed grid dimensions
static const int ROWS = static_cast<int>(ceil((MAX_LAT - MIN_LAT) / CELL_SIZE)); // 45
static const int COLS = static_cast<int>(ceil((MAX_LON - MIN_LON) / CELL_SIZE)); // 56

// ═══════════════════════════════════════════════════════════════════════════
// 2.  HAVERSINE DISTANCE  (accurate great-circle km between two lat/lon points)
//     Replaces the old Euclidean sqrt(Δlat² + Δlon²) which gives results in
//     "degree units", not kilometres, and is geometrically incorrect on a sphere.
// ═══════════════════════════════════════════════════════════════════════════

double haversine(double lat1, double lon1, double lat2, double lon2) {
    double dLat = (lat2 - lat1) * DEG_TO_RAD;
    double dLon = (lon2 - lon1) * DEG_TO_RAD;
    double a = sin(dLat / 2) * sin(dLat / 2)
             + cos(lat1 * DEG_TO_RAD) * cos(lat2 * DEG_TO_RAD)
             * sin(dLon / 2) * sin(dLon / 2);
    return EARTH_RADIUS_KM * 2.0 * atan2(sqrt(a), sqrt(1.0 - a));
}

// ═══════════════════════════════════════════════════════════════════════════
// 3.  GRID ↔ COORDINATE HELPERS
// ═══════════════════════════════════════════════════════════════════════════

int    latToRow(double lat) { return static_cast<int>(floor((lat - MIN_LAT) / CELL_SIZE)); }
int    lonToCol(double lon) { return static_cast<int>(floor((lon - MIN_LON) / CELL_SIZE)); }
double rowToLat(int row)    { return MIN_LAT + (row + 0.5) * CELL_SIZE; }
double colToLon(int col)    { return MIN_LON + (col + 0.5) * CELL_SIZE; }
int    nodeId(int row, int col) { return row * COLS + col; }
bool   inBounds(int r, int c)   { return r >= 0 && r < ROWS && c >= 0 && c < COLS; }

// ═══════════════════════════════════════════════════════════════════════════
// 4.  DATA STRUCTURES
// ═══════════════════════════════════════════════════════════════════════════

struct Hospital {
    string name;
    double lat, lon;
    int    row, col;   // pre-computed grid cell
};

// Dijkstra priority-queue entry: (cost_km, nodeId)
using PQEntry = pair<double, int>;

// ═══════════════════════════════════════════════════════════════════════════
// 5.  SPATIAL HASH GRID  — O(1) hazard lookup
//     Stores flat nodeIds of all cells that contain a hazard/road-block.
//     Used both during graph construction and to filter hospitals.
// ═══════════════════════════════════════════════════════════════════════════

class SpatialHashGrid {
    unordered_set<int> blockedCells;

public:
    void addHazard(double lat, double lon) {
        int r = latToRow(lat);
        int c = lonToCol(lon);
        if (inBounds(r, c)) blockedCells.insert(nodeId(r, c));
    }

    bool isBlocked(int row, int col) const {
        return blockedCells.count(nodeId(row, col)) > 0;
    }

    int count() const { return static_cast<int>(blockedCells.size()); }
};

// ═══════════════════════════════════════════════════════════════════════════
// 6.  DIJKSTRA'S ALGORITHM
//
//  Graph model
//  ───────────
//  Nodes : every (row, col) cell in the NYC grid  (ROWS × COLS ≈ 2 520 nodes)
//  Edges : 8-directional neighbours (N NE E SE S SW W NW)
//  Weight: Haversine distance between the two cell centres (km)
//  Blocked cells (hazards) are never expanded — you cannot enter them.
//
//  Complexity: O((V + E) log V)  where V = ROWS×COLS, E ≤ 8V
//  In practice this runs in < 5 ms for the NYC grid.
//
//  Returns: dist[] — minimum path cost (km) from src to every cell.
//           dist[nodeId(r,c)] == INF means the cell is unreachable.
// ═══════════════════════════════════════════════════════════════════════════

vector<double> dijkstra(int srcRow, int srcCol, const SpatialHashGrid& hazards) {
    const int TOTAL = ROWS * COLS;
    vector<double> dist(TOTAL, INF);

    // Min-heap: (cost, nodeId) — C++ priority_queue is max-heap by default,
    // so we use greater<> to flip it into a min-heap.
    priority_queue<PQEntry, vector<PQEntry>, greater<PQEntry>> pq;

    int srcId    = nodeId(srcRow, srcCol);
    dist[srcId]  = 0.0;
    pq.push({0.0, srcId});

    // 8-directional movement: row delta, col delta
    static const int DR[] = {-1,-1,-1, 0, 0, 1, 1, 1};
    static const int DC[] = {-1, 0, 1,-1, 1,-1, 0, 1};

    while (!pq.empty()) {
        auto [cost, uid] = pq.top();
        pq.pop();

        // Lazy deletion: skip this entry if we already found a cheaper path
        if (cost > dist[uid]) continue;

        int row = uid / COLS;
        int col = uid % COLS;

        // Early exit: we have found the minimum cost to every reachable node
        // once the queue empties — but we don't early-exit per hospital so that
        // a single Dijkstra run scores ALL hospitals simultaneously.

        // Relax each valid neighbour
        for (int d = 0; d < 8; ++d) {
            int nr = row + DR[d];
            int nc = col + DC[d];

            if (!inBounds(nr, nc))         continue;   // outside grid
            if (hazards.isBlocked(nr, nc)) continue;   // hazard cell — cannot enter

            int    nid      = nodeId(nr, nc);
            double edgeCost = haversine(rowToLat(row), colToLon(col),
                                        rowToLat(nr),  colToLon(nc));
            double newCost  = dist[uid] + edgeCost;

            if (newCost < dist[nid]) {
                dist[nid] = newCost;
                pq.push({newCost, nid});
            }
        }
    }

    return dist;
}

// ═══════════════════════════════════════════════════════════════════════════
// 7.  ROUTING ENGINE
// ═══════════════════════════════════════════════════════════════════════════

class RoutingEngine {
public:
    vector<Hospital> hospitals;
    SpatialHashGrid  hazards;

    // ── Load hospitals.json (written by nyctraffic.py) ─────────────────────
    bool loadHospitals(const string& filename) {
        ifstream file(filename);
        if (!file.is_open()) {
            cerr << "[ERROR] Cannot open " << filename << endl;
            return false;
        }
        json j;
        try { file >> j; }
        catch (const json::exception& e) {
            cerr << "[ERROR] JSON parse error in " << filename
                 << ": " << e.what() << endl;
            return false;
        }

        unordered_set<string> seen;  // deduplicate by name
        for (auto& item : j) {
            if (!item.contains("lat") || !item.contains("lon")) continue;

            string name = "Unknown Hospital";
            if (item.contains("tags") && item["tags"].is_object()
                && item["tags"].contains("name")) {
                name = item["tags"]["name"].get<string>();
            }
            if (seen.count(name)) continue;
            seen.insert(name);

            double lat = item["lat"].get<double>();
            double lon = item["lon"].get<double>();
            if (lat == 0.0 && lon == 0.0) continue;   // skip malformed entries

            int r = latToRow(lat);
            int c = lonToCol(lon);
            if (!inBounds(r, c)) continue;             // outside NYC bbox

            hospitals.push_back({name, lat, lon, r, c});
        }

        cerr << "[INFO] Loaded " << hospitals.size() << " hospitals" << endl;
        return !hospitals.empty();
    }

    // ── Load hazards.json (written by nyctraffic.py) ───────────────────────
    void loadHazards(const string& filename) {
        ifstream file(filename);
        if (!file.is_open()) {
            cerr << "[WARN] " << filename << " not found — routing without hazards" << endl;
            return;
        }
        json j;
        try { file >> j; }
        catch (...) {
            cerr << "[WARN] Could not parse " << filename << endl;
            return;
        }
        for (auto& item : j) {
            if (item.contains("lat") && item.contains("lon")) {
                hazards.addHazard(item["lat"].get<double>(),
                                  item["lon"].get<double>());
            }
        }
        cerr << "[INFO] Hazard grid: " << hazards.count() << " blocked cells" << endl;
    }

    // ── Core query: find the nearest safe ER via Dijkstra ─────────────────
    //
    //  1. Map patient (lat,lon) → grid cell.
    //  2. Run single-source Dijkstra from that cell over the full NYC grid.
    //  3. Look up every hospital's grid cell in the dist[] result.
    //  4. Sort hospitals by path cost (cheapest = nearest safe ER).
    //  5. Return top-N as a JSON array (or single object if topN == 1).
    // ──────────────────────────────────────────────────────────────────────
    json findNearestER(double pLat, double pLon, int topN = 1) {
        int pRow = latToRow(pLat);
        int pCol = lonToCol(pLon);

        if (!inBounds(pRow, pCol)) {
            return {{"error", "Patient location is outside the NYC grid boundary"}};
        }
        if (hospitals.empty()) {
            return {{"error", "No hospitals loaded — run: python3 nyctraffic.py"}};
        }

        cerr << "[INFO] Dijkstra from patient cell [" << pRow << "," << pCol
             << "] (" << pLat << ", " << pLon << ")" << endl;

        // Single Dijkstra run scores ALL hospitals simultaneously
        vector<double> dist = dijkstra(pRow, pCol, hazards);

        // Score each hospital
        struct Candidate {
            const Hospital* h;
            double pathCost;       // Dijkstra optimal path distance (km)
            double straightLine;   // Haversine direct distance (km) — fallback
            bool   reachable;
        };

        vector<Candidate> cands;
        cands.reserve(hospitals.size());

        for (const auto& h : hospitals) {
            if (hazards.isBlocked(h.row, h.col)) continue; // ER itself is blocked

            double pathCost   = dist[nodeId(h.row, h.col)];
            double directDist = haversine(pLat, pLon, h.lat, h.lon);
            cands.push_back({&h, pathCost, directDist, pathCost < INF});
        }

        if (cands.empty()) {
            return {{"error", "All ERs are in hazard zones — no safe route found"}};
        }

        // Sort: reachable before unreachable; within reachable, by path cost
        sort(cands.begin(), cands.end(), [](const Candidate& a, const Candidate& b) {
            if (a.reachable != b.reachable) return a.reachable > b.reachable;
            if (a.reachable)  return a.pathCost    < b.pathCost;
            return a.straightLine < b.straightLine; // both unreachable: straight-line
        });

        // Build JSON
        auto makeObj = [](const Candidate& c) -> json {
            json o;
            o["name"]            = c.h->name;
            o["lat"]             = c.h->lat;
            o["lon"]             = c.h->lon;
            o["distance_km"]     = round(c.straightLine * 100.0) / 100.0;
            o["path_cost_km"]    = c.reachable
                                   ? round(c.pathCost * 100.0) / 100.0
                                   : nullptr;          // null = no safe path
            o["route_available"] = c.reachable;
            return o;
        };

        int n = min(topN, static_cast<int>(cands.size()));
        if (topN == 1) return makeObj(cands[0]);

        json arr = json::array();
        for (int i = 0; i < n; ++i) arr.push_back(makeObj(cands[i]));
        return arr;
    }
};

// ═══════════════════════════════════════════════════════════════════════════
// 8.  MAIN  — invoked by server.py via subprocess
//
//  stdout → clean JSON only  (parsed by server.py / api.py)
//  stderr → debug log        (captured separately, never mixed into JSON)
// ═══════════════════════════════════════════════════════════════════════════

int main(int argc, char* argv[]) {
    if (argc < 3) {
        cout << R"({"error":"Usage: ./navigation <lat> <lon> [--top=N]"})" << endl;
        return 1;
    }

    double pLat = atof(argv[1]);
    double pLon = atof(argv[2]);
    int    topN = 1;

    for (int i = 3; i < argc; ++i) {
        string arg(argv[i]);
        if (arg.rfind("--top=", 0) == 0) {
            try { topN = max(1, stoi(arg.substr(6))); }
            catch (...) {}
        }
    }

    RoutingEngine engine;
    if (!engine.loadHospitals("hospitals.json")) {
        cout << R"({"error":"hospitals.json missing — run: python3 nyctraffic.py"})" << endl;
        return 1;
    }
    engine.loadHazards("hazards.json");

    json result = engine.findNearestER(pLat, pLon, topN);

    // stdout carries ONLY the JSON — server.py parses this directly
    cout << result.dump() << endl;
    return 0;
}