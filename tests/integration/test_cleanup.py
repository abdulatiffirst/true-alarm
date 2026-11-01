"""Очередь удаления файлов. Нужна отдельная тестовая база (TEST_DATABASE_URL)."""
import asyncio
import hashlib

import pytest

from app import db
from app.cleanup import run_cleanup
from app.storage import Storage
from tests.helpers import FakeSupabase

LAT, LON = 41.2995, 69.2401


def h(label) -> str:
    return hashlib.sha256(str(label).encode()).hexdigest()


def make_report(conn, i, problem_id=None, max_photos=10):
    return db.accept_report(
        conn,
        sender_hash=h(f"sender-{i}"),
        category="waste",
        latitude=LAT,
        longitude=LON,
        photo_sha256=h(f"photo-{i}"),
        photo_path=f"p{i}.jpg",
        thumb_path=f"p{i}_t.jpg",
        problem_id=problem_id,
        max_photos=max_photos,
    )


def storage_for(fake: FakeSupabase) -> Storage:
    return Storage("https://abc.supabase.co", "key", fake.bucket, transport=fake.transport)


def queue_rows(conn):
    return conn.execute("select * from public.storage_cleanup order by id").fetchall()


def test_queued_files_are_deleted_and_removed_from_queue(conn, test_dsn):
    fake = FakeSupabase()
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        fake.objects[name] = b"x"
        db.enqueue_cleanup(conn, name, "over_limit")
    conn.commit()

    stats = asyncio.run(run_cleanup(storage_for(fake), dsn=test_dsn))

    assert (stats.deleted, stats.failed) == (3, 0)
    assert fake.objects == {}
    assert len(fake.delete_calls) == 1  # одним запросом
    assert queue_rows(conn) == []


def test_missing_files_are_not_an_error(conn, test_dsn):
    fake = FakeSupabase()  # файла в хранилище нет
    db.enqueue_cleanup(conn, "ghost.jpg", "orphan")
    conn.commit()

    stats = asyncio.run(run_cleanup(storage_for(fake), dsn=test_dsn))

    assert (stats.deleted, stats.failed) == (1, 0)
    assert queue_rows(conn) == []


def test_file_used_by_a_live_report_is_never_deleted(conn, test_dsn):
    fake = FakeSupabase()
    live = make_report(conn, 1)
    fake.objects["p1.jpg"] = b"live"
    fake.objects["p1_t.jpg"] = b"live"
    fake.objects["junk.jpg"] = b"junk"
    # Ошибка вызывающего кода: живые файлы попали в очередь вместе с настоящим мусором.
    for name in ("p1.jpg", "p1_t.jpg", "junk.jpg"):
        db.enqueue_cleanup(conn, name, "orphan")
    conn.commit()

    asyncio.run(run_cleanup(storage_for(fake), dsn=test_dsn))

    assert set(fake.objects) == {"p1.jpg", "p1_t.jpg"}
    assert all("p1" not in path for call in fake.delete_calls for path in call)
    assert queue_rows(conn) == []
    assert live.report_count == 1


def test_failure_keeps_file_queued_with_backoff_and_retry_succeeds(conn, test_dsn):
    fake = FakeSupabase()
    fake.objects["a.jpg"] = b"x"
    fake.fail_deletes = True
    db.enqueue_cleanup(conn, "a.jpg", "over_limit")
    conn.commit()

    stats = asyncio.run(run_cleanup(storage_for(fake), dsn=test_dsn))
    assert (stats.deleted, stats.failed) == (0, 1)
    row = queue_rows(conn)[0]
    assert row["attempts"] == 1 and row["last_error"] == "HTTP 500"
    assert row["next_attempt_at"] > conn.execute("select now() as n").fetchone()["n"]
    assert "a.jpg" in fake.objects

    # Сразу после неудачи повторная попытка не делается (файл «арендован» до срока).
    calls_before = len(fake.delete_calls)
    asyncio.run(run_cleanup(storage_for(fake), dsn=test_dsn))
    assert len(fake.delete_calls) == calls_before

    # Срок наступил, хранилище снова работает: файл удаляется, строка уходит из очереди.
    fake.fail_deletes = False
    conn.execute("update public.storage_cleanup set next_attempt_at = now()")
    conn.commit()
    stats = asyncio.run(run_cleanup(storage_for(fake), dsn=test_dsn))
    assert (stats.deleted, stats.failed) == (1, 0)
    assert fake.objects == {} and queue_rows(conn) == []


def test_orphans_wait_before_first_attempt(conn, test_dsn):
    fake = FakeSupabase()
    fake.objects["o.jpg"] = b"x"
    db.enqueue_cleanup(conn, "o.jpg", "orphan", delay_seconds=600)
    conn.commit()

    asyncio.run(run_cleanup(storage_for(fake), dsn=test_dsn))

    assert fake.delete_calls == [] and "o.jpg" in fake.objects
    assert len(queue_rows(conn)) == 1


def test_parallel_runs_never_process_the_same_file_twice(conn, test_dsn):
    fake = FakeSupabase()
    names = [f"f{i}.jpg" for i in range(40)]
    for name in names:
        fake.objects[name] = b"x"
        db.enqueue_cleanup(conn, name, "over_limit")
    conn.commit()
    storage = storage_for(fake)

    async def both():
        return await asyncio.gather(
            run_cleanup(storage, dsn=test_dsn, batch_size=10),
            run_cleanup(storage, dsn=test_dsn, batch_size=10),
        )

    asyncio.run(both())

    sent = [path for call in fake.delete_calls for path in call]
    assert sorted(sent) == sorted(names)  # каждый файл ровно один раз
    assert fake.objects == {} and queue_rows(conn) == []


def test_large_backlog_is_processed_in_batches(conn, test_dsn):
    fake = FakeSupabase()
    for i in range(120):
        fake.objects[f"f{i}.jpg"] = b"x"
        db.enqueue_cleanup(conn, f"f{i}.jpg", "over_limit")
    conn.commit()

    stats = asyncio.run(run_cleanup(storage_for(fake), dsn=test_dsn, batch_size=50, max_batches=5))

    assert stats.deleted == 120
    assert [len(c) for c in fake.delete_calls] == [50, 50, 20]


def test_after_eleven_photos_exactly_ten_files_remain_in_storage(conn, test_dsn):
    """Весь путь: 11 заявок, лимит 10, очистка. В хранилище остаются 10 последних фото и превью."""
    fake = FakeSupabase()
    first = make_report(conn, 0)
    fake.objects.update({"p0.jpg": b"x", "p0_t.jpg": b"x"})
    for i in range(1, 11):
        make_report(conn, i, problem_id=first.problem_id)
        fake.objects.update({f"p{i}.jpg": b"x", f"p{i}_t.jpg": b"x"})
    conn.commit()
    assert len(fake.objects) == 22  # кратковременный технический избыток

    asyncio.run(run_cleanup(storage_for(fake), dsn=test_dsn))

    assert len(fake.objects) == 20
    assert set(fake.objects) == {f"p{i}{s}.jpg" for i in range(1, 11) for s in ("", "_t")}
    problem = conn.execute("select report_count from public.problems").fetchone()
    assert problem["report_count"] == 11  # счётчики не уменьшились
    assert conn.execute("select count(*) as n from public.reports").fetchone()["n"] == 11
