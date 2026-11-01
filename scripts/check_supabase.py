"""Проверка подключения к базе. Запуск из корня проекта:  python -m scripts.check_supabase"""
from app.db import connect

if __name__ == "__main__":
    try:
        with connect() as conn:
            ok = conn.execute("select 1 as ok").fetchone()["ok"] == 1
        print("Успешно: подключение к PostgreSQL работает." if ok else "Ошибка: неожиданный ответ базы.")
    except Exception as error:
        print(f"Ошибка подключения: {type(error).__name__}")
        print("Проверь DATABASE_URL, пароль и настройки сети.")
        raise
