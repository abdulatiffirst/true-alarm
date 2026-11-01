from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as main

client = TestClient(main.app)
NOW = datetime.now(timezone.utc)
VIEW = {"south": 41.28, "west": 69.22, "north": 41.32, "east": 69.26}


def row(**overrides):
    base = {
        "id": uuid4(),
        "category": "waste",
        "status": "open",
        "latitude": 41.30,
        "longitude": 69.24,
        "first_report_at": NOW - timedelta(hours=2),
        "last_report_at": NOW - timedelta(hours=1),
        "report_count": 2,
        "independent_senders": 2,
    }
    base.update(overrides)
    return base


def test_returns_problems_for_the_visible_area(monkeypatch):
    seen = {}

    def fake(south, west, north, east, limit):
        seen.update(south=south, west=west, north=north, east=east, limit=limit)
        return [row()]

    monkeypatch.setattr(main, "load_in_view", fake)

    response = client.get("/api/problems", params=VIEW)

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1 and data["truncated"] is False
    assert data["items"][0]["priority"] == "green"
    assert "distance_meters" not in data["items"][0]  # для области расстояния нет
    assert seen == {**VIEW, "limit": 300}


def test_truncated_flag_when_limit_is_reached(monkeypatch):
    monkeypatch.setattr(main, "load_in_view", lambda *a: [row(), row()])
    data = client.get("/api/problems", params={**VIEW, "limit": 2}).json()
    assert data["truncated"] is True


@pytest.mark.parametrize(
    "params",
    [
        {"south": 41.32, "west": 69.22, "north": 41.28, "east": 69.26},  # юг севернее севера
        {"south": 41.28, "west": 69.26, "north": 41.32, "east": 69.22},  # запад восточнее востока
        {"south": 41.28, "west": 69.22, "north": 41.28, "east": 69.26},  # нулевая высота
        {"south": 30, "west": 60, "north": 50, "east": 80},  # слишком большая область
        {"south": 41.28, "west": 69.22, "north": 95, "east": 69.26},  # координата вне диапазона
    ],
)
def test_bad_or_too_large_areas_are_rejected(monkeypatch, params):
    monkeypatch.setattr(main, "load_in_view", lambda *a: pytest.fail("до базы дойти не должно"))
    assert client.get("/api/problems", params=params).status_code == 422


def test_too_large_area_message_tells_user_what_to_do(monkeypatch):
    monkeypatch.setattr(main, "load_in_view", lambda *a: [])
    response = client.get("/api/problems", params={"south": 30, "west": 60, "north": 50, "east": 80})
    assert "приблизьте карту" in response.json()["detail"]


def test_database_failure_gives_503_without_details(monkeypatch):
    def broken(*args):
        raise RuntimeError("password=secret host=db.internal")

    monkeypatch.setattr(main, "load_in_view", broken)
    response = client.get("/api/problems", params=VIEW)
    assert response.status_code == 503
    assert "secret" not in response.text
