"""Интеграционные тесты схемы и приёма заявок. Нужна отдельная тестовая база (TEST_DATABASE_URL)."""
import hashlib
import threading
import time
from uuid import uuid4

import psycopg
import pytest

from app import db

LAT, LON = 41.2995, 69.2401


def sender(n: int) -> str:
    return hashlib.sha256(f"sender-{n}".encode()).hexdigest()


def photo(n: int) -> str:
    return hashlib.sha256(f"photo-{n}".encode()).hexdigest()


def report(conn, *, s=1, p=None, category="waste", lat=LAT, lon=LON, problem_id=None, max_photos=10, **extra):
    p = p if p is not None else uuid4().int
    return db.accept_report(
        conn,
        sender_hash=sender(s),
        category=category,
        latitude=lat,
        longitude=lon,
        photo_sha256=photo(p),
        photo_path=f"{photo(p)}.jpg",
        thumb_path=f"{photo(p)}_t.jpg",
        problem_id=problem_id,
        max_photos=max_photos,
        **extra,
    )


def one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def test_new_problem_is_created_with_first_report(conn):
    result = report(conn, s=1, p=1)
    conn.commit()

    assert result.created_new_problem and result.is_new_sender
    assert (result.report_count, result.independent_senders) == (1, 1)
    row = one(conn, "select has_file, photo_path from public.reports where id = %s", (result.report_id,))
    assert row["has_file"] is True
    assert row["photo_path"] == f"{photo(1)}.jpg"


def test_same_sender_second_photo_counts_once_and_keeps_first_date(conn):
    first = report(conn, s=1, p=1)
    time.sleep(0.01)
    second = report(conn, s=1, p=2, problem_id=first.problem_id)
    conn.commit()

    assert not second.created_new_problem and not second.is_new_sender
    assert second.report_count == 2
    assert second.independent_senders == 1
    assert second.first_report_at == first.first_report_at
    assert second.last_report_at > first.last_report_at


def test_second_sender_increases_independent_count(conn):
    first = report(conn, s=1, p=1)
    second = report(conn, s=2, p=2, problem_id=first.problem_id)
    assert (second.report_count, second.independent_senders) == (2, 2)


def test_exact_duplicate_photo_is_rejected_and_nothing_is_saved(conn):
    first = report(conn, s=1, p=1)
    conn.commit()
    problems_before = one(conn, "select count(*) as n from public.problems")["n"]

    # Тот же файл, другая точка и другой отправитель: новая проблема не должна появиться.
    with pytest.raises(db.DuplicatePhoto):
        report(conn, s=2, p=1, lat=LAT + 0.5)

    assert one(conn, "select count(*) as n from public.problems")["n"] == problems_before
    assert one(conn, "select count(*) as n from public.reports")["n"] == 1
    assert db.photo_hash_exists(conn, photo(1)) is True
    assert db.photo_hash_exists(conn, photo(999)) is False
    assert first.report_count == 1


def test_duplicate_stays_rejected_after_file_is_removed(conn):
    first = report(conn, s=1, p=1)
    conn.execute("update public.reports set photo_path = null, thumb_path = null where id = %s", (first.report_id,))
    conn.commit()
    with pytest.raises(db.DuplicatePhoto):
        report(conn, s=2, p=1)


def test_only_ten_latest_photos_remain_but_counters_do_not_drop(conn):
    first = report(conn, s=1, p=0)
    for i in range(1, 11):  # всего 11 заявок
        report(conn, s=1, p=i, problem_id=first.problem_id)
    conn.commit()

    problem = one(conn, "select * from public.problems where id = %s", (first.problem_id,))
    assert problem["report_count"] == 11
    assert problem["independent_senders"] == 1
    assert problem["first_report_at"] == first.first_report_at

    rows = conn.execute(
        "select photo_path, thumb_path, has_file from public.reports where problem_id = %s "
        "order by created_at, seq",
        (first.problem_id,),
    ).fetchall()
    assert len(rows) == 11  # история заявок осталась целиком
    assert [r["has_file"] for r in rows] == [False] + [True] * 10  # самую старую фотографию убрали
    assert rows[0]["photo_path"] is None and rows[0]["thumb_path"] is None

    queued = {r["object_path"] for r in conn.execute("select object_path from public.storage_cleanup")}
    assert queued == {f"{photo(0)}.jpg", f"{photo(0)}_t.jpg"}


def test_retention_prefers_acceptance_order_when_times_are_equal(conn):
    first = report(conn, s=1, p=0)
    for i in range(1, 4):
        report(conn, s=1, p=i, problem_id=first.problem_id, max_photos=3)
    # Делаем время одинаковым у всех заявок: порядок должен определять seq.
    conn.execute("update public.reports set created_at = now() where problem_id = %s", (first.problem_id,))
    report(conn, s=1, p=4, problem_id=first.problem_id, max_photos=3)
    conn.commit()

    kept = [
        r["photo_path"]
        for r in conn.execute(
            "select photo_path from public.reports where problem_id = %s and photo_path is not null "
            "order by seq",
            (first.problem_id,),
        )
    ]
    assert kept == [f"{photo(i)}.jpg" for i in (2, 3, 4)]


def test_removing_photos_never_touches_counters_or_dates(conn):
    first = report(conn, s=1, p=1)
    conn.commit()
    before = one(conn, "select report_count, independent_senders, first_report_at from public.problems")
    conn.execute("update public.reports set photo_path = null, thumb_path = null")
    conn.commit()
    after = one(conn, "select report_count, independent_senders, first_report_at from public.problems")
    assert before == after


def test_database_forbids_decreasing_history(conn):
    report(conn, s=1, p=1)
    conn.commit()
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("update public.problems set report_count = report_count - 1")
    conn.rollback()
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("update public.problems set first_report_at = now() + interval '1 day'")
    conn.rollback()


def test_attach_rules(conn):
    base = report(conn, s=1, p=1, category="waste")
    conn.commit()

    with pytest.raises(db.ProblemMismatch):  # другая категория
        report(conn, s=2, p=2, category="infrastructure", problem_id=base.problem_id)
    with pytest.raises(db.ProblemMismatch):  # слишком далеко (около 1 км)
        report(conn, s=2, p=3, lat=LAT + 0.01, problem_id=base.problem_id)
    with pytest.raises(db.ProblemUnavailable):  # нет такой проблемы
        report(conn, s=2, p=4, problem_id=uuid4())
    with pytest.raises(db.ProblemUnavailable):  # некорректный идентификатор
        report(conn, s=2, p=5, problem_id="not-a-uuid")

    conn.execute("update public.problems set status = 'hidden', hidden_at = now()")
    conn.commit()
    with pytest.raises(db.ProblemUnavailable):  # скрытая проблема не принимает заявки
        report(conn, s=2, p=6, problem_id=base.problem_id)

    # Все отказы откатились целиком: счётчики прежние.
    assert one(conn, "select report_count from public.problems")["report_count"] == 1
    assert one(conn, "select count(*) as n from public.reports")["n"] == 1


def test_raw_telegram_id_cannot_be_stored_even_by_direct_sql(conn):
    problem = report(conn, s=1, p=1)
    conn.commit()
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "insert into public.problem_senders (problem_id, sender_hash) values (%s, %s)",
            (problem.problem_id, "123456789"),
        )
    conn.rollback()


def test_find_candidates_filters_by_category_status_and_distance(conn):
    near = report(conn, s=1, p=1, category="waste")
    report(conn, s=2, p=2, category="infrastructure")  # другая категория
    report(conn, s=3, p=3, category="waste", lat=LAT + 0.01)  # далеко
    hidden = report(conn, s=4, p=4, category="waste", lat=LAT + 0.0001)
    conn.execute("update public.problems set status='hidden', hidden_at=now() where id=%s", (hidden.problem_id,))
    conn.commit()

    found = db.find_candidates(conn, category="waste", latitude=LAT, longitude=LON, radius_m=30)

    assert [row["id"] for row in found] == [near.problem_id]
    assert found[0]["latest_photo_path"] == f"{photo(1)}_t.jpg"


def test_list_nearby_excludes_hidden_and_sorts_by_distance(conn):
    a = report(conn, s=1, p=1, lat=LAT + 0.002)
    b = report(conn, s=2, p=2, lat=LAT + 0.001)
    c = report(conn, s=3, p=3, lat=LAT + 0.0005)
    conn.execute("update public.problems set status='hidden', hidden_at=now() where id=%s", (c.problem_id,))
    conn.commit()

    rows = db.list_nearby(conn, latitude=LAT, longitude=LON, radius_meters=1000)

    assert [r["id"] for r in rows] == [b.problem_id, a.problem_id]
    assert rows[0]["distance_meters"] < rows[1]["distance_meters"]


def test_row_level_security_is_enabled_everywhere(conn):
    rows = conn.execute(
        "select relname, relrowsecurity from pg_class where relnamespace = 'public'::regnamespace "
        "and relname in ('problems','problem_senders','reports','storage_cleanup','schema_migrations')"
    ).fetchall()
    assert len(rows) == 5
    assert all(r["relrowsecurity"] for r in rows)


def _run_threads(test_dsn, jobs):
    results, errors = [], []

    def work(job):
        try:
            with db.connect(test_dsn) as c:
                results.append(job(c))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(job,)) for job in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results, errors


def test_concurrent_reports_lose_nothing(conn, test_dsn):
    first = report(conn, s=0, p=0)
    conn.commit()
    n = 12
    jobs = [
        (lambda c, i=i: report(c, s=i, p=i, problem_id=first.problem_id))
        for i in range(1, n + 1)
    ]

    results, errors = _run_threads(test_dsn, jobs)

    assert not errors, errors
    assert len(results) == n
    problem = one(conn, "select * from public.problems where id = %s", (first.problem_id,))
    assert problem["report_count"] == n + 1
    assert problem["independent_senders"] == n + 1
    assert one(conn, "select count(*) as k from public.reports")["k"] == n + 1
    assert one(conn, "select count(*) as k from public.reports where has_file")["k"] == 10
    # Файлы трёх вытесненных заявок (по два пути: фото и превью) ждут удаления, ничего не потеряно.
    assert one(conn, "select count(*) as k from public.storage_cleanup")["k"] == (n + 1 - 10) * 2
    # Остались ровно 10 разных фотографий.
    assert one(conn, "select count(distinct photo_path) as k from public.reports where has_file")["k"] == 10


def test_concurrent_identical_photos_accept_exactly_one(conn, test_dsn):
    jobs = [(lambda c, i=i: report(c, s=i, p=777)) for i in range(1, 7)]
    results, errors = _run_threads(test_dsn, jobs)
    conn.rollback()

    assert len(results) == 1
    assert len(errors) == 5 and all(isinstance(e, db.DuplicatePhoto) for e in errors)
    assert one(conn, "select count(*) as k from public.reports")["k"] == 1
    assert one(conn, "select count(*) as k from public.problems")["k"] == 1


def test_same_sender_concurrent_counts_once(conn, test_dsn):
    first = report(conn, s=1, p=0)
    conn.commit()
    jobs = [(lambda c, i=i: report(c, s=1, p=i, problem_id=first.problem_id)) for i in range(1, 9)]

    results, errors = _run_threads(test_dsn, jobs)

    assert not errors, errors
    problem = one(conn, "select report_count, independent_senders from public.problems")
    assert problem["report_count"] == 9
    assert problem["independent_senders"] == 1
