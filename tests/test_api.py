from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main

client = TestClient(main.app)

NOW = datetime.now(timezone.utc)


def make_row(**overrides):
    row = {
        "id": uuid4(),
        "category": "infrastructure",
        "status": "open",
        "latitude": 41.3,
        "longitude": 69.24,
        "first_report_at": NOW - timedelta(hours=80),
        "last_report_at": NOW - timedelta(hours=1),
        "report_count": 4,
        "independent_senders": 1,
        "distance_meters": 150.456,
        "latest_photo_path": "secret/internal/path.jpg",
    }
    row.update(overrides)
    return row


def test_nearby_returns_problem_with_computed_priority(monkeypatch):
    row = make_row()

    def fake_load_nearby(latitude, longitude, radius_meters, limit):
        assert (latitude, longitude, radius_meters, limit) == (41.2995, 69.2401, 1000, 20)
        return [row]

    monkeypatch.setattr(main, "load_nearby", fake_load_nearby)

    response = client.get(
        "/api/problems/nearby",
        params={"latitude": 41.2995, "longitude": 69.2401, "radius_meters": 1000, "limit": 20},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    item = data["items"][0]
    assert item["id"] == str(row["id"])
    # 1 отправитель, 80 часов: цвет считается по датам, а не берётся из базы.
    assert item["priority"] == "orange"
    assert item["priority_score"] == 3
    assert item["independent_senders"] == 1
    assert item["report_count"] == 4
    assert item["distance_meters"] == 150.46
    assert item["category_label"] == "Инфраструктура"


def test_priority_changes_with_time_without_new_reports(monkeypatch):
    young = make_row(first_report_at=NOW - timedelta(hours=1))
    old = make_row(first_report_at=NOW - timedelta(hours=200))
    monkeypatch.setattr(main, "load_nearby", lambda *a, **k: [young, old])

    items = client.get(
        "/api/problems/nearby", params={"latitude": 41.3, "longitude": 69.2}
    ).json()["items"]

    assert [item["priority"] for item in items] == ["green", "red"]


def test_public_fields_are_allowlisted(monkeypatch):
    monkeypatch.setattr(main, "load_nearby", lambda *a, **k: [make_row(sender_hash="x" * 64)])

    item = client.get(
        "/api/problems/nearby", params={"latitude": 41.3, "longitude": 69.2}
    ).json()["items"][0]

    assert "sender_hash" not in item
    assert "latest_photo_path" not in item
    assert "secret/internal/path.jpg" not in str(item)


def test_nearby_rejects_invalid_coordinates():
    response = client.get("/api/problems/nearby", params={"latitude": 100, "longitude": 69.2401})
    assert response.status_code == 422


def test_nearby_handles_database_runtime_error(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("Database unavailable")

    monkeypatch.setattr(main, "load_nearby", broken)

    response = client.get("/api/problems/nearby", params={"latitude": 41.2995, "longitude": 69.2401})

    assert response.status_code == 503
    assert response.json()["detail"] == "Сервис базы данных временно недоступен."
