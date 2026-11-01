"""Видимая область карты: выборка из настоящей базы и ответ настоящего API."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings

CENTER = (41.2999, 69.2405)  # рядом с центром, где раньше «смотрела» страница
FAR = (41.3500, 69.3000)  # около 7 км от центра


def h(label) -> str:
    return hashlib.sha256(str(label).encode()).hexdigest()


def add(conn, i, point, category="waste"):
    return db.accept_report(
        conn,
        sender_hash=h(f"s{i}"),
        category=category,
        latitude=point[0],
        longitude=point[1],
        photo_sha256=h(f"p{i}"),
        photo_path=f"p{i}.jpg",
        thumb_path=f"p{i}_t.jpg",
    )


def around(point, d=0.01):
    return {"south": point[0] - d, "west": point[1] - d, "north": point[0] + d, "east": point[1] + d}


def test_list_in_bounds_returns_only_problems_inside(conn):
    near = add(conn, 1, CENTER)
    far = add(conn, 2, FAR)
    conn.commit()

    a = around(CENTER)
    inside_center = db.list_in_bounds(conn, **a)
    b = around(FAR)
    inside_far = db.list_in_bounds(conn, **b)
    both = db.list_in_bounds(conn, south=41.28, west=69.22, north=41.37, east=69.32)

    assert [r["id"] for r in inside_center] == [near.problem_id]
    assert [r["id"] for r in inside_far] == [far.problem_id]
    assert {r["id"] for r in both} == {near.problem_id, far.problem_id}


def test_hidden_problems_and_empty_areas(conn):
    p = add(conn, 1, CENTER)
    conn.execute("update public.problems set status='hidden', hidden_at=now()")
    conn.commit()
    assert db.list_in_bounds(conn, **around(CENTER)) == []
    assert p.report_count == 1


def test_limit_and_newest_first(conn):
    first = add(conn, 1, CENTER)
    second = add(conn, 2, (CENTER[0] + 0.0005, CENTER[1]))
    conn.commit()
    rows = db.list_in_bounds(conn, **around(CENTER), limit=1)
    assert [r["id"] for r in rows] == [second.problem_id]  # свежая проблема первой
    assert first.problem_id != second.problem_id


def test_invalid_bounds_rejected(conn):
    with pytest.raises(ValueError):
        db.list_in_bounds(conn, south=2, west=0, north=1, east=1)
    with pytest.raises(ValueError):
        db.list_in_bounds(conn, **around(CENTER), limit=0)


@pytest.fixture
def api(monkeypatch, test_dsn, conn):
    monkeypatch.setenv("DATABASE_URL", test_dsn)
    get_settings.cache_clear()
    import app.main as main

    yield TestClient(main.app)
    get_settings.cache_clear()


def test_regression_problem_far_from_old_fixed_center_is_visible(api, conn):
    """Раньше страница всегда спрашивала про радиус вокруг одной точки и не видела это."""
    add(conn, 1, CENTER)
    add(conn, 2, FAR, category="infrastructure")
    conn.commit()

    far_view = api.get("/api/problems", params=around(FAR)).json()
    assert far_view["count"] == 1
    item = far_view["items"][0]
    assert item["category"] == "infrastructure" and item["category_label"] == "Инфраструктура"
    assert abs(item["latitude"] - FAR[0]) < 1e-6 and abs(item["longitude"] - FAR[1]) < 1e-6
    assert item["priority"] == "green" and item["priority_score"] == 1
    assert item["report_count"] == 1 and item["independent_senders"] == 1
    assert "sender_hash" not in item and "photo_path" not in item

    # Старый запрос «радиус 5 км вокруг центра» эту проблему не находил:
    old = api.get(
        "/api/problems/nearby",
        params={"latitude": CENTER[0], "longitude": CENTER[1], "radius_meters": 5000, "limit": 100},
    ).json()
    assert old["count"] == 1  # только та, что рядом с центром

    whole_city = api.get("/api/problems", params={"south": 41.28, "west": 69.22, "north": 41.37, "east": 69.32}).json()
    assert whole_city["count"] == 2


def test_empty_area_returns_empty_list_not_error(api):
    data = api.get("/api/problems", params=around((40.0, 65.0))).json()
    assert data == {"count": 0, "truncated": False, "items": []}


# Реальные координаты двух проблем из базы пользователя (около 78 км от центра Ташкента).
USER_A = (41.007694, 70.092348)
USER_B = (41.007673, 70.092358)


def test_real_user_coordinates_are_visible_in_their_area_but_not_in_tashkent(api, conn):
    add(conn, 1, USER_A)
    add(conn, 2, USER_B)
    conn.commit()

    tashkent = api.get("/api/problems", params=around((41.2995, 69.2401), 0.015)).json()
    theirs = api.get("/api/problems", params=around(USER_A, 0.01)).json()
    zoomed_out = api.get("/api/problems", params={"south": 40.7, "west": 69.0, "north": 41.5, "east": 70.3}).json()

    assert tashkent["count"] == 0  # на стартовом виде карты их не видно: это и была жалоба
    assert theirs["count"] == 2
    assert zoomed_out["count"] == 2


def test_second_report_nearby_would_find_the_first_as_candidate(conn):
    """Две заявки с разницей 2,5 м: при отправке второй бот (этап 4) предложит первую как кандидата."""
    first = add(conn, 1, USER_A)
    conn.commit()
    found = db.find_candidates(conn, category="waste", latitude=USER_B[0], longitude=USER_B[1], radius_m=30)
    assert [row["id"] for row in found] == [first.problem_id]
    assert found[0]["distance_meters"] < 5
