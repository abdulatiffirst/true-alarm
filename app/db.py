"""Работа с PostgreSQL (Supabase).

Функции принимают открытое соединение `conn`. Вне тестов его получают так:

    with connect() as conn:
        accept_report(conn, ...)

Выход из блока `with` фиксирует транзакцию (или откатывает при ошибке).
"""
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings
from app.domain.categories import CATEGORIES
from app.domain.geo import validate_coordinates

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Порядок координат в PostGIS: долгота, потом широта.
_POINT_SQL = "extensions.ST_SetSRID(extensions.ST_MakePoint(%s, %s), 4326)::extensions.geography"


class ReportError(Exception):
    """Заявку нельзя принять."""


class DuplicatePhoto(ReportError):
    """Точно такая же фотография уже была принята."""


class ProblemUnavailable(ReportError):
    """Проблема не найдена или больше не принимает заявки."""


class ProblemMismatch(ReportError):
    """Заявка не подходит к выбранной проблеме (другая категория или слишком далеко)."""


@dataclass(frozen=True)
class AcceptedReport:
    report_id: UUID
    problem_id: UUID
    created_new_problem: bool
    is_new_sender: bool
    report_count: int
    independent_senders: int
    first_report_at: datetime
    last_report_at: datetime
    # Файлы, поставленные в очередь на удаление из-за лимита фотографий.
    queued_for_deletion: tuple[str, ...]


def connect(dsn: str | None = None) -> psycopg.Connection:
    """Новое соединение с базой. Строки результата приходят как словари."""
    dsn = dsn or get_settings().database_url
    if not dsn:
        raise RuntimeError("DATABASE_URL не задан в файле .env")
    options: dict = {"connect_timeout": 10, "row_factory": dict_row}
    if "sslmode" not in dsn:
        options["sslmode"] = "require"
    return psycopg.connect(dsn, **options)


def _validate_report(
    sender_hash: str,
    category: str,
    latitude: float,
    longitude: float,
    photo_sha256: str,
    photo_path: str,
    max_photos: int,
    merge_radius_m: float,
) -> None:
    if not _HEX64.match(sender_hash or ""):
        raise ValueError("sender_hash должен быть HMAC-псевдонимом (64 hex-символа), а не Telegram ID")
    if category not in CATEGORIES:
        raise ValueError(f"Неизвестная категория: {category!r}")
    validate_coordinates(latitude, longitude)
    if not _HEX64.match(photo_sha256 or ""):
        raise ValueError("photo_sha256 должен содержать 64 hex-символа")
    if not photo_path or len(photo_path) > 300:
        raise ValueError("photo_path обязателен и не длиннее 300 символов")
    if max_photos < 1:
        raise ValueError("max_photos должен быть не меньше 1")
    if merge_radius_m <= 0:
        raise ValueError("merge_radius_m должен быть положительным")


def photo_hash_exists(conn: psycopg.Connection, photo_sha256: str) -> bool:
    """Была ли уже принята фотография с таким отпечатком (даже если файл удалён)."""
    if not _HEX64.match(photo_sha256 or ""):
        raise ValueError("photo_sha256 должен содержать 64 hex-символа")
    with conn.cursor() as cur:
        cur.execute("select 1 from public.reports where photo_sha256 = %s", (photo_sha256,))
        return cur.fetchone() is not None


def enqueue_cleanup(
    conn: psycopg.Connection, object_path: str, reason: str, delay_seconds: int = 0
) -> None:
    """Поставить файл в очередь на удаление. Повторная постановка безопасна.

    delay_seconds откладывает первую попытку (для «осиротевших» файлов даём время
    завершиться возможной незавершённой записи заявки).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into public.storage_cleanup (object_path, reason, next_attempt_at)
            values (%s, %s, now() + make_interval(secs => %s::double precision))
            on conflict (object_path) do nothing
            """,
            (object_path, reason, delay_seconds),
        )


def claim_cleanup_batch(conn: psycopg.Connection, limit: int = 50) -> list[dict]:
    """Берёт из очереди файлы, которые пора удалить, и «арендует» их.

    1. Выбрасывает из очереди пути, на которые ссылается живая заявка: такой файл
       удалять нельзя (страховка от ошибочной постановки в очередь).
    2. Для выбранных файлов сразу увеличивает счётчик попыток и откладывает
       следующую попытку (1, 2, 4... минут, не дольше 6 часов). Если процесс упадёт,
       файл просто вернётся в очередь позже. Параллельные запуски не пересекаются.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                delete from public.storage_cleanup c
                where exists (
                    select 1 from public.reports r
                    where r.photo_path = c.object_path or r.thumb_path = c.object_path
                )
                """
            )
            cur.execute(
                """
                with due as (
                    select id from public.storage_cleanup
                    where next_attempt_at <= now()
                    order by next_attempt_at, id
                    limit %s
                    for update skip locked
                )
                update public.storage_cleanup c
                set attempts = c.attempts + 1,
                    next_attempt_at = now() + least(
                        interval '6 hours',
                        interval '1 minute' * power(2, least(c.attempts, 10))
                    )
                from due
                where c.id = due.id
                returning c.id, c.object_path, c.attempts
                """,
                (limit,),
            )
            return cur.fetchall()


def finish_cleanup(
    conn: psycopg.Connection, *, deleted_ids: list[int], failed: dict[int, str] | None = None
) -> None:
    """Убирает из очереди удалённые файлы и запоминает причину неудач у остальных."""
    with conn.transaction():
        with conn.cursor() as cur:
            if deleted_ids:
                cur.execute("delete from public.storage_cleanup where id = any(%s)", (deleted_ids,))
            for row_id, error in (failed or {}).items():
                cur.execute(
                    "update public.storage_cleanup set last_error = %s where id = %s",
                    (error[:200], row_id),
                )


def accept_report(
    conn: psycopg.Connection,
    *,
    sender_hash: str,
    category: str,
    latitude: float,
    longitude: float,
    photo_sha256: str,
    photo_path: str,
    thumb_path: str | None = None,
    problem_id: UUID | str | None = None,
    max_photos: int | None = None,
    merge_radius_m: float | None = None,
) -> AcceptedReport:
    """Принять заявку целиком в одной транзакции.

    * problem_id=None: создаётся новая проблема. Иначе заявка привязывается к
      существующей (категория должна совпасть, точка быть в радиусе объединения).
    * Отправитель считается независимым один раз на проблему.
    * Точный дубликат фотографии отклоняется (DuplicatePhoto), ничего не сохраняется.
    * Сверх лимита фотографий старые файлы отвязываются от заявок и ставятся в
      очередь на удаление. Сами заявки и счётчики остаются.
    Строка проблемы блокируется до конца транзакции, поэтому параллельные заявки
    выстраиваются в очередь и ничего не теряют.
    """
    settings = get_settings()
    max_photos = settings.max_photos_per_problem if max_photos is None else max_photos
    merge_radius_m = settings.merge_radius_m if merge_radius_m is None else merge_radius_m
    _validate_report(
        sender_hash, category, latitude, longitude, photo_sha256, photo_path, max_photos, merge_radius_m
    )

    with conn.transaction():
        with conn.cursor() as cur:
            if problem_id is None:
                created_new = True
                cur.execute(
                    f"insert into public.problems (category, location) values (%s, {_POINT_SQL}) "
                    "returning id",
                    (category, longitude, latitude),
                )
                pid = cur.fetchone()["id"]
            else:
                created_new = False
                try:
                    pid = UUID(str(problem_id))
                except ValueError:
                    raise ProblemUnavailable("Некорректный идентификатор проблемы") from None
                cur.execute(
                    f"""
                    select category, status,
                           extensions.ST_DWithin(location, {_POINT_SQL}, %s) as is_near
                    from public.problems
                    where id = %s
                    for update
                    """,
                    (longitude, latitude, merge_radius_m, pid),
                )
                problem = cur.fetchone()
                if problem is None or problem["status"] != "open":
                    raise ProblemUnavailable("Проблема не найдена или больше не принимает заявки")
                if problem["category"] != category:
                    raise ProblemMismatch("Категория заявки не совпадает с категорией проблемы")
                if not problem["is_near"]:
                    raise ProblemMismatch("Точка заявки слишком далеко от проблемы")

            cur.execute(
                """
                insert into public.problem_senders (problem_id, sender_hash)
                values (%s, %s)
                on conflict do nothing
                """,
                (pid, sender_hash),
            )
            is_new_sender = cur.rowcount == 1

            try:
                with conn.transaction():  # точка сохранения, чтобы ловить дубликат
                    cur.execute(
                        """
                        insert into public.reports
                            (problem_id, sender_hash, photo_sha256, photo_path, thumb_path)
                        values (%s, %s, %s, %s, %s)
                        returning id
                        """,
                        (pid, sender_hash, photo_sha256, photo_path, thumb_path),
                    )
                    report_id = cur.fetchone()["id"]
            except psycopg.errors.UniqueViolation as exc:
                if exc.diag.constraint_name == "reports_photo_sha256_key":
                    raise DuplicatePhoto("Эта фотография уже была принята") from None
                raise

            if created_new:
                cur.execute(
                    "select report_count, independent_senders, first_report_at, last_report_at "
                    "from public.problems where id = %s",
                    (pid,),
                )
            else:
                cur.execute(
                    """
                    update public.problems
                    set report_count = report_count + 1,
                        independent_senders = independent_senders + %s,
                        last_report_at = clock_timestamp()
                    where id = %s
                    returning report_count, independent_senders, first_report_at, last_report_at
                    """,
                    (int(is_new_sender), pid),
                )
            totals = cur.fetchone()

            # Оставляем max_photos последних фотографий, остальное — в очередь удаления.
            cur.execute(
                """
                select id, photo_path, thumb_path
                from public.reports
                where problem_id = %s and photo_path is not null
                order by created_at desc, seq desc
                offset %s
                """,
                (pid, max_photos),
            )
            stale = cur.fetchall()
            queued: list[str] = []
            for row in stale:
                for path in (row["photo_path"], row["thumb_path"]):
                    if path:
                        enqueue_cleanup(conn, path, "over_limit")
                        queued.append(path)
            if stale:
                cur.execute(
                    "update public.reports set photo_path = null, thumb_path = null where id = any(%s)",
                    ([row["id"] for row in stale],),
                )

    return AcceptedReport(
        report_id=report_id,
        problem_id=pid,
        created_new_problem=created_new,
        is_new_sender=is_new_sender,
        report_count=totals["report_count"],
        independent_senders=totals["independent_senders"],
        first_report_at=totals["first_report_at"],
        last_report_at=totals["last_report_at"],
        queued_for_deletion=tuple(queued),
    )


_NEARBY_SQL = f"""
    with pt as (select {_POINT_SQL} as g)
    select
        p.id,
        p.category,
        p.status,
        extensions.ST_Y(p.location::extensions.geometry) as latitude,
        extensions.ST_X(p.location::extensions.geometry) as longitude,
        p.first_report_at,
        p.last_report_at,
        p.report_count,
        p.independent_senders,
        extensions.ST_Distance(p.location, pt.g) as distance_meters,
        (
            select coalesce(r.thumb_path, r.photo_path)
            from public.reports r
            where r.problem_id = p.id and r.photo_path is not null
            order by r.created_at desc, r.seq desc
            limit 1
        ) as latest_photo_path
    from public.problems p
    cross join pt
    where p.status = 'open'
      and extensions.ST_DWithin(p.location, pt.g, %s)
      {{category_filter}}
    order by distance_meters asc
    limit %s
"""


def list_nearby(
    conn: psycopg.Connection,
    *,
    latitude: float,
    longitude: float,
    radius_meters: float,
    limit: int = 20,
    category: str | None = None,
) -> list[dict]:
    """Открытые проблемы рядом с точкой, от ближайшей к дальней."""
    validate_coordinates(latitude, longitude)
    if not 0 < radius_meters <= 10_000:
        raise ValueError("Радиус должен быть больше 0 и не более 10000 м")
    if not 1 <= limit <= 100:
        raise ValueError("Лимит должен быть от 1 до 100")
    params: list = [longitude, latitude, radius_meters]
    category_filter = ""
    if category is not None:
        if category not in CATEGORIES:
            raise ValueError(f"Неизвестная категория: {category!r}")
        category_filter = "and p.category = %s"
        params.append(category)
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(_NEARBY_SQL.format(category_filter=category_filter), params)
        return cur.fetchall()


def find_candidates(
    conn: psycopg.Connection,
    *,
    category: str,
    latitude: float,
    longitude: float,
    radius_m: float | None = None,
    limit: int = 5,
) -> list[dict]:
    """Открытые проблемы той же категории в радиусе объединения (для выбора отправителем)."""
    radius_m = get_settings().merge_radius_m if radius_m is None else radius_m
    return list_nearby(
        conn,
        latitude=latitude,
        longitude=longitude,
        radius_meters=radius_m,
        limit=limit,
        category=category,
    )


def list_in_bounds(
    conn: psycopg.Connection,
    *,
    south: float,
    west: float,
    north: float,
    east: float,
    limit: int = 300,
) -> list[dict]:
    """Открытые проблемы в прямоугольной области (видимая часть карты), сначала свежие."""
    validate_coordinates(south, west)
    validate_coordinates(north, east)
    if not (south < north and west < east):
        raise ValueError("Некорректные границы области")
    if not 1 <= limit <= 500:
        raise ValueError("Лимит должен быть от 1 до 500")
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                p.id,
                p.category,
                p.status,
                extensions.ST_Y(p.location::extensions.geometry) as latitude,
                extensions.ST_X(p.location::extensions.geometry) as longitude,
                p.first_report_at,
                p.last_report_at,
                p.report_count,
                p.independent_senders
            from public.problems p
            where p.status = 'open'
              and extensions.ST_Intersects(
                    p.location,
                    extensions.ST_MakeEnvelope(%s, %s, %s, %s, 4326)::extensions.geography
                  )
            order by p.last_report_at desc, p.id
            limit %s
            """,
            (west, south, east, north, limit),
        )
        return cur.fetchall()
