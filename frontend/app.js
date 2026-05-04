const map = L.map("map").setView([40.7128, -74.0060], 11);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "OpenStreetMap"
}).addTo(map);

let patientMarker = null;
let hospitalMarker = null;
let routeLine = null;

const hospitalIcon = L.icon({
    iconUrl: "https://cdn-icons-png.flaticon.com/512/1484/1484846.png",
    iconSize: [25, 25],
    iconAnchor: [12, 25],
});

const bestIcon = L.icon({
    iconUrl: "https://cdn-icons-png.flaticon.com/512/1828/1828884.png",
    iconSize: [32, 32],
    iconAnchor: [16, 32],
});

async function checkStatus() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();

        console.log("Server status:", data);
    } catch {
        console.log("Server offline");
    }
}

async function loadHospitals() {
    const res = await fetch("/api/hospitals");
    const hospitals = await res.json();

    document.getElementById("hospitalCount").innerText = hospitals.length;

    hospitals.forEach(h => {
        L.marker([h.lat, h.lon], { icon: hospitalIcon })
            .addTo(map)
            .bindPopup(`🏥 ${h.name}`);
    });
}

async function loadHazards() {
    const res = await fetch("/api/hazards");
    const hazards = await res.json();

    document.getElementById("hazardCount").innerText = hazards.length;

    const hazardsList = document.getElementById("hazardsList");
    hazardsList.innerHTML = "";

    hazards.forEach(h => {
        L.circleMarker([h.lat, h.lon], {
            radius: 8,
            color: "red",
            fillColor: "red",
            fillOpacity: 0.8
        }).addTo(map).bindPopup(`⚠️ ${h.type} - ${h.severity}`);

        const div = document.createElement("div");
        div.className = "hazardItem";
        div.innerHTML = `
            <b>${h.type}</b><br/>
            Severity: ${h.severity}
        `;
        hazardsList.appendChild(div);
    });
}

async function calculateRoute(lat, lon) {
    const emergencyType = document.getElementById("emergencyType").value;

    if (patientMarker) map.removeLayer(patientMarker);
    if (hospitalMarker) map.removeLayer(hospitalMarker);
    if (routeLine) map.removeLayer(routeLine);

    patientMarker = L.marker([lat, lon])
        .addTo(map)
        .bindPopup("🚨 Patient Location")
        .openPopup();

    document.getElementById("result").innerHTML = `
        <h3>Processing Emergency...</h3>
        <p>Emergency type: ${emergencyType}</p>
        <p>Calculating real driving route...</p>
    `;

    let data;

    try {
        const res = await fetch("/api/nearest-er", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lat, lon, emergencyType })
        });

        data = await res.json();

        if (data.error) {
            document.getElementById("result").innerHTML = `
                <h3>Error</h3>
                <p>${data.error}</p>
            `;
            return;
        }

    } catch {
        document.getElementById("result").innerHTML = `
            <h3>Error</h3>
            <p>Failed to connect to server.</p>
        `;
        return;
    }

    const bestHospital = data.best;
    const top3 = data.top3;

    hospitalMarker = L.marker([bestHospital.lat, bestHospital.lon], { icon: bestIcon })
        .addTo(map)
        .bindPopup(`⭐ BEST OPTION: ${bestHospital.name}`)
        .openPopup();

    routeLine = L.polyline(
        bestHospital.geometry.map(p => [p.lat, p.lon]),
        {
            color: "#dc2626",
            weight: 6,
            opacity: 0.9
        }
    ).addTo(map);

    map.fitBounds(routeLine.getBounds(), {
        padding: [50, 50]
    });

    let html = `<h3>Top 3 ER for ${emergencyType}</h3>`;

    top3.forEach((h, i) => {
        html += `
            <p>
                <b>${i + 1}. ${h.name}</b><br/>
                Driving distance: ${h.distance_km} km<br/>
                Estimated time: ${h.duration_min} min<br/>
                Hazard penalty: ${h.hazard_penalty}<br/>
                Final score: ${h.score}
            </p>
        `;
    });

    document.getElementById("result").innerHTML = html;

    document.getElementById("decisionText").innerHTML = `
        Emergency type: <b>${emergencyType}</b><br>
        Selected <b>${bestHospital.name}</b> because it had the lowest adjusted routing score.<br><br>
        Decision formula:<br>
        <b>score = driving time + hazard penalty</b>
    `;

    document.getElementById("routeTime").innerText = new Date().toLocaleTimeString();
}

map.on("click", function(e) {
    calculateRoute(e.latlng.lat, e.latlng.lng);
});

document.getElementById("clearBtn").addEventListener("click", () => {
    if (patientMarker) map.removeLayer(patientMarker);
    if (hospitalMarker) map.removeLayer(hospitalMarker);
    if (routeLine) map.removeLayer(routeLine);

    patientMarker = null;
    hospitalMarker = null;
    routeLine = null;

    document.getElementById("result").innerHTML = "Cleared. Click map again.";
    document.getElementById("decisionText").innerHTML = "No emergency selected yet.";
    document.getElementById("routeTime").innerText = "Not yet";
});

document.getElementById("locateBtn").addEventListener("click", () => {
    if (!navigator.geolocation) {
        alert("Your browser does not support location.");
        return;
    }

    document.getElementById("result").innerHTML = `
        <h3>Locating...</h3>
        <p>Getting your current location.</p>
    `;

    navigator.geolocation.getCurrentPosition(
        function(position) {
            const lat = position.coords.latitude;
            const lon = position.coords.longitude;

            map.setView([lat, lon], 13);
            calculateRoute(lat, lon);
        },
        function() {
            document.getElementById("result").innerHTML = `
                <h3>Location Error</h3>
                <p>Please allow location access or click on the map manually.</p>
            `;
        }
    );
});

loadHospitals();
loadHazards();
checkStatus();