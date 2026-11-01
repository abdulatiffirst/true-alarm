"""Применяет SQL-миграции из supabase/migrations по порядку имён файлов.

Запуск из корня проекта:  python -m scripts.apply_migrations
Уже применённые миграции пропускаются.
"""
from pathlib import Path

import psycopg

from app.db import connect

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "supabase" / "migrations"


def apply_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Применяет новые миграции и возвращает их имена."""
    with conn.transaction():
        conn.execute(
            """
            create table if not exists public.schema_migrations (
                filename   text primary key,
                applied_at timestamptz not null default now()
            )
            """
        )
        conn.execute("alter table public.schema_migrations enable row level security")
    done = {row["filename"] for row in conn.execute("select filename from public.schema_migrations")}
    applied: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        if path.name in done:
            continue
        sql = path.read_text(encoding="utf-8")
        if not sql.strip():
            raise RuntimeError(f"Файл миграции пуст: {path.name}")
        with conn.transaction():
            conn.execute(sql)
            conn.execute("insert into public.schema_migrations (filename) values (%s)", (path.name,))
        applied.append(path.name)
    return applied


def main() -> None:
    with connect() as conn:
        print(f"База: {conn.info.host}:{conn.info.port}/{conn.info.dbname}")
        applied = apply_migrations(conn)
    if applied:
        print("Применены миграции:", ", ".join(applied))
    else:
        print("Новых миграций нет.")


if __name__ == "__main__":
    main()
