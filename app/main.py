from datetime import datetime, timezone
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_priority_config, get_settings
from app.db import connect, list_in_bounds, list_nearby
from app.domain.categories import CATEGORIES
from app.domain.priority import Priority, compute_priority

settings = get_settings()

app = FastAPI(title="True Alarm", version="0.2.0")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Числовая шкала для текущей страницы карты (будет заменена на этапе карты).
_PRIORITY_SCORE = {Priority.GREEN: 1, Priority.YELLOW: 2, Priority.ORANGE: 3, Priority.RED: 4}


def load_nearby(latitude: float, longitude: float, radius_meters: float, limit: int) -> list[dict]:
    with connect() as conn:
        return list_nearby(
            conn,
            latitude=latitude,
            longitude=longitude,
            radius_meters=radius_meters,
            limit=limit,
        )


def load_in_view(south: float, west: float, north: float, east: float, limit: int) -> list[dict]:
    with connect() as conn:
        return list_in_bounds(conn, south=south, west=west, north=north, east=east, limit=limit)


# Слишком большую область не отдаём: карта мира не нужна, а выборка была бы тяжёлой.
MAX_VIEW_SPAN_DEGREES = 3.0


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def public_problem(row: dict, now: datetime) -> dict:
    """Только разрешённые публичные поля. Приоритет считается при каждом запросе."""
    priority = compute_priority(
        first_report_at=row["first_report_at"],
        senders_count=row["independent_senders"],
        now=now,
        config=get_priority_config(),
    )
    label = CATEGORIES.get(row["category"], row["category"])
    item = {
        "id": str(row["id"]),
        "category": row["category"],
        "category_label": label,
        "title": label,
        "description": None,
        "status": row["status"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "first_report_at": _iso_utc(row["first_report_at"]),
        "last_report_at": _iso_utc(row["last_report_at"]),
        "report_count": row["report_count"],
        "independent_senders": row["independent_senders"],
        "priority": priority.value,
        "priority_score": _PRIORITY_SCORE[priority],
    }
    if row.get("distance_meters") is not None:
        item["distance_meters"] = round(float(row["distance_meters"]), 2)
    return item


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    """Проверка, что сервис запущен."""
    return {"status": "ok", "env": settings.app_env}


@app.get("/api/problems/nearby")
def nearby_problems(
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    radius_meters: float = Query(default=1000, gt=0, le=10000),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """Возвращает открытые проблемы рядом с указанными координатами."""
    try:
        rows = load_nearby(latitude, longitude, radius_meters, limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (RuntimeError, psycopg.Error):
        raise HTTPException(
            status_code=503,
            detail="Сервис базы данных временно недоступен.",
        ) from None

    now = datetime.now(timezone.utc)
    items = [public_problem(row, now) for row in rows]
    return {"count": len(items), "items": items}


@app.get("/api/problems")
def problems_in_view(
    south: float = Query(ge=-90, le=90),
    west: float = Query(ge=-180, le=180),
    north: float = Query(ge=-90, le=90),
    east: float = Query(ge=-180, le=180),
    limit: int = Query(default=300, ge=1, le=500),
) -> dict:
    """Открытые проблемы в видимой области карты."""
    if not (south < north and west < east):
        raise HTTPException(status_code=422, detail="Некорректные границы области.")
    if north - south > MAX_VIEW_SPAN_DEGREES or east - west > MAX_VIEW_SPAN_DEGREES:
        raise HTTPException(status_code=422, detail="Область слишком большая: приблизьте карту.")
    try:
        rows = load_in_view(south, west, north, east, limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (RuntimeError, psycopg.Error):
        raise HTTPException(
            status_code=503,
            detail="Сервис базы данных временно недоступен.",
        ) from None

    now = datetime.now(timezone.utc)
    items = [public_problem(row, now) for row in rows]
    # truncated: в области больше проблем, чем мы вернули; нужно приблизить карту.
    return {"count": len(items), "truncated": len(items) >= limit, "items": items}
