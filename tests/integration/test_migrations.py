from pathlib import Path

from app import db
from scripts.apply_migrations import MIGRATIONS_DIR, apply_migrations


def test_migrations_are_idempotent(test_dsn):
    with db.connect(test_dsn) as conn:
        assert apply_migrations(conn) == []  # уже применены фикстурой


def test_migration_files_are_not_empty():
    files = sorted(Path(MIGRATIONS_DIR).glob("*.sql"))
    assert files, "нет файлов миграций"
    assert all(f.stat().st_size > 0 for f in files)
