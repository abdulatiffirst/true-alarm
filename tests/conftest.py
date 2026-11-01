import os

import pytest
from psycopg.conninfo import conninfo_to_dict

from app import db
from app.config import get_settings
from scripts.apply_migrations import apply_migrations

# Интеграционные тесты стирают таблицы. Поэтому они работают ТОЛЬКО с отдельной
# тестовой базой, заданной в TEST_DATABASE_URL, и отказываются от рабочей (DATABASE_URL).


def _identity(dsn: str) -> tuple:
    parts = conninfo_to_dict(dsn)
    return (parts.get("host"), str(parts.get("port", "5432")), parts.get("dbname"), parts.get("user"))


@pytest.fixture(scope="session")
def test_dsn() -> str:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL не задан: интеграционные тесты пропущены")
    working = get_settings().database_url
    if working and _identity(dsn) == _identity(working):
        pytest.fail("Отказ: TEST_DATABASE_URL указывает на ту же базу, что и DATABASE_URL")
    with db.connect(dsn) as conn:
        apply_migrations(conn)
    return dsn


@pytest.fixture
def conn(test_dsn):
    """Соединение с чистыми таблицами для каждого теста."""
    connection = db.connect(test_dsn)
    connection.execute(
        "truncate public.reports, public.problem_senders, public.storage_cleanup, "
        "public.problems restart identity cascade"
    )
    connection.commit()
    yield connection
    connection.close()
