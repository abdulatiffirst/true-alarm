// Карта True Alarm. Данные запрашиваются для той части карты, которая сейчас видна.
const DEFAULT_VIEW = { latitude: 41.2995, longitude: 69.2401, zoom: 14 };
const MAX_PROBLEMS = 300;

// Карту можно открыть в нужном месте: /?lat=41.0077&lon=70.0923&zoom=16
function viewFromUrl() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("lat") === null || params.get("lon") === null) return null;
    const latitude = Number(params.get("lat"));
    const longitude = Number(params.get("lon"));
    if (
        !Number.isFinite(latitude) || !Number.isFinite(longitude) ||
        Math.abs(latitude) > 90 || Math.abs(longitude) > 180
    ) {
        return null;
    }
    const zoom = Number(params.get("zoom"));
    return {
        latitude,
        longitude,
        zoom: Number.isFinite(zoom) && zoom >= 1 && zoom <= 19 ? zoom : 16,
    };
}

const urlView = viewFromUrl();
const initialView = urlView || DEFAULT_VIEW;
const map = L.map("map").setView([initialView.latitude, initialView.longitude], initialView.zoom);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
}).addTo(map);

const markers = L.layerGroup().addTo(map);
const categorySelect = document.getElementById("category-filter");
const statusElement = document.getElementById("status");
const problemList = document.getElementById("problem-list");
const refreshButton = document.getElementById("refresh-button");

let lastItems = [];
let requestCounter = 0;
let reloadTimer = null;

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    })[character]);
}

function getPriorityColor(score) {
    const colors = { 1: "#22a447", 2: "#eab308", 3: "#f97316", 4: "#dc2626" };
    return colors[Number(score)] || "#64748b";
}

function formatDate(iso) {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("ru-RU");
}

function isValidPoint(problem) {
    return (
        typeof problem.latitude === "number" &&
        typeof problem.longitude === "number" &&
        Number.isFinite(problem.latitude) &&
        Number.isFinite(problem.longitude) &&
        Math.abs(problem.latitude) <= 90 &&
        Math.abs(problem.longitude) <= 180
    );
}

function visibleBounds() {
    const bounds = map.getBounds();
    return {
        south: Math.max(-90, bounds.getSouth()),
        north: Math.min(90, bounds.getNorth()),
        west: Math.max(-180, bounds.getWest()),
        east: Math.min(180, bounds.getEast()),
    };
}

function renderProblems(items) {
    markers.clearLayers();
    problemList.replaceChildren();
    let shown = 0;

    for (const problem of items) {
        if (categorySelect.value !== "all" && problem.category !== categorySelect.value) {
            continue;
        }
        if (!isValidPoint(problem)) {
            continue;
        }

        const marker = L.circleMarker([problem.latitude, problem.longitude], {
            radius: 9,
            color: "#ffffff",
            weight: 2,
            fillColor: getPriorityColor(problem.priority_score),
            fillOpacity: 0.95,
        });
        marker.bindPopup(`
            <strong>${escapeHtml(problem.title)}</strong>
            <p>Сообщений: ${escapeHtml(problem.report_count)}</p>
            <p>Независимых отправителей: ${escapeHtml(problem.independent_senders)}</p>
            <p>Первое сообщение: ${escapeHtml(formatDate(problem.first_report_at))}</p>
        `);
        marker.addTo(markers);

        const card = document.createElement("article");
        card.className = "problem-card";
        card.innerHTML = `
            <h3>${escapeHtml(problem.title)}</h3>
            <p>Сообщений: ${escapeHtml(problem.report_count)}</p>
            <p>Первое сообщение: ${escapeHtml(formatDate(problem.first_report_at))}</p>
        `;
        card.addEventListener("click", () => {
            map.setView([problem.latitude, problem.longitude], Math.max(map.getZoom(), 17));
            marker.openPopup();
        });
        problemList.appendChild(card);
        shown += 1;
    }

    if (shown === 0) {
        problemList.textContent = "В этой области пока нет зарегистрированных проблем.";
    }
    return shown;
}

async function loadProblems() {
    const requestId = ++requestCounter;
    statusElement.textContent = "Загрузка проблем...";
    refreshButton.disabled = true;

    try {
        const bounds = visibleBounds();
        const params = new URLSearchParams({
            south: bounds.south,
            west: bounds.west,
            north: bounds.north,
            east: bounds.east,
            limit: MAX_PROBLEMS,
        });
        const response = await fetch(`/api/problems?${params}`);
        if (requestId !== requestCounter) return; // пришёл устаревший ответ

        if (response.status === 422) {
            lastItems = [];
            markers.clearLayers();
            problemList.textContent = "";
            statusElement.textContent = "Область слишком большая: приблизьте карту.";
            return;
        }
        if (!response.ok) {
            throw new Error(`Ошибка API: HTTP ${response.status}`);
        }

        const data = await response.json();
        if (requestId !== requestCounter) return;

        lastItems = data.items;
        const shown = renderProblems(lastItems);
        statusElement.textContent = data.truncated
            ? `Показаны не все проблемы (${data.count}): приблизьте карту.`
            : `Найдено проблем: ${shown}`;
    } catch (error) {
        if (requestId !== requestCounter) return;
        console.error(error);
        statusElement.textContent = "Не удалось загрузить данные.";
        problemList.textContent = "Проверь подключение к серверу и попробуй обновить страницу.";
    } finally {
        if (requestId === requestCounter) {
            refreshButton.disabled = false;
        }
    }
}

function scheduleReload() {
    clearTimeout(reloadTimer);
    reloadTimer = setTimeout(loadProblems, 300);
}

refreshButton.addEventListener("click", loadProblems);
map.on("moveend", scheduleReload);
categorySelect.addEventListener("change", () => {
    const shown = renderProblems(lastItems);
    statusElement.textContent = `Найдено проблем: ${shown}`;
});

loadProblems();

// Если адрес задаёт место, геопозицию браузера не используем.
if (!urlView && navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
        (position) => {
            // Карта сдвинется, а данные для новой области загрузятся сами (событие moveend).
            map.setView([position.coords.latitude, position.coords.longitude], 15);
        },
        () => {
            // Доступ запрещён: остаёмся на центре Ташкента.
        },
        { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
    );
}
