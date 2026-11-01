
import psycopg
from app.config import get_settings
from app.db import find_nearby_problems

settings = get_settings()
test_title = "TEMP TEST - True Alarm database check"

with psycopg.connect(
    settings.database_url,
    connect_timeout=10,
    sslmode="require",
) as connection:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO public.problems (
                title,
                category,
                location
            )
            VALUES (
                %s,
                %s,
                extensions.ST_SetSRID(
                    extensions.ST_MakePoint(%s, %s),
                    4326
                )::extensions.geography
            )
            RETURNING id
            """,
            (test_title, "other", 69.2401, 41.2995),
        )
        test_id = cursor.fetchone()[0]

try:
    results = find_nearby_problems(
        latitude=41.2995,
        longitude=69.2401,
        radius_meters=30,
    )

    found = any(str(problem["id"]) == str(test_id) for problem in results)

    print(f"Тестовая запись: {test_id}")
    print(f"Найдена через PostGIS: {found}")

    if not found:
        raise RuntimeError("Тестовая запись не найдена.")

    print("Успех: создание записи и географический поиск работают.")

finally:
    with psycopg.connect(
        settings.database_url,
        connect_timeout=10,
        sslmode="require",
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM public.problems WHERE id = %s",
                (test_id,),
            )
    print("Тестовая запись удалена.")
