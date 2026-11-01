
from app.db import check_database_connection


if __name__ == "__main__":
    try:
        if check_database_connection():
            print("Успешно: подключение к Supabase PostgreSQL работает.")
        else:
            print("Ошибка: база данных вернула неожиданный результат.")

    except Exception as error:
        print(f"Ошибка подключения: {type(error).__name__}")
        print("Проверь DATABASE_URL, пароль и настройки сети.")
        raise
