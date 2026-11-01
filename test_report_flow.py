
import psycopg

from app.config import get_settings
from app.db import add_report_to_problem


def main():
    settings = get_settings()
    problem_id = None

    # Только тестовый отправитель — не используй реальные пользовательские данные.
    sender_hash = "test_sender_hash_" + "a" * 40

    try:
        # Создаём отдельную тестовую проблему.
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
                        description,
                        category,
                        status,
                        location
                    )
                    VALUES (
                        'TEST: report flow',
                        'Temporary test problem',
                        'other',
                        'open',
                        extensions.ST_SetSRID(
                            extensions.ST_MakePoint(%s, %s),
                            4326
                        )::extensions.geography
                    )
                    RETURNING id
                    """,
                    (69.6769, 41.2995),
                )
                problem_id = str(cursor.fetchone()[0])

        print(f"Создана тестовая проблема: {problem_id}")

        # Первое сообщение от отправителя.
        first = add_report_to_problem(
            problem_id,
            sender_hash,
            description="Тестовое сообщение 1",
        )

        # Второе сообщение от того же отправителя.
        second = add_report_to_problem(
            problem_id,
            sender_hash,
            description="Тестовое сообщение 2",
        )

        assert first["is_new_sender"] is True, first
        assert first["report_count"] == 1, first
        assert first["independent_reporters"] == 1, first

        assert second["is_new_sender"] is False, second
        assert second["report_count"] == 2, second
        assert second["independent_reporters"] == 1, second

        print("Первое сообщение:", first)
        print("Второе сообщение:", second)
        print("Успех: сообщения и независимые отправители считаются правильно.")

    finally:
        # Удаляем только запись, созданную этим тестом.
        # Связанные reports и problem_senders удалятся через ON DELETE CASCADE.
        if problem_id is not None:
            with psycopg.connect(
                settings.database_url,
                connect_timeout=10,
                sslmode="require",
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM public.problems WHERE id = %s",
                        (problem_id,),
                    )
            print("Тестовая проблема и связанные записи удалены.")


if __name__ == "__main__":
    main()
