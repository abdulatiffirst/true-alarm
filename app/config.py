
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.priority import PriorityConfig


class Settings(BaseSettings):
    """Все настройки проекта. Значения берутся из переменных окружения или из файла .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"
    log_level: str = "INFO"

    # Пороги приоритета.
    priority_age_thresholds_hours: tuple[int, int, int] = (24, 72, 168)
    priority_sender_thresholds: tuple[int, int, int] = (3, 6, 11)

    # Радиус поиска похожих проблем для объединения заявок, в метрах.
    merge_radius_m: int = 30

    # Сколько последних фотографий хранится по одной проблеме.
    max_photos_per_problem: int = 10

    # Ограничения и параметры обработки фотографий.
    max_upload_bytes: int = 10_000_000  # максимальный размер принимаемого файла
    max_image_pixels: int = 40_000_000  # максимум пикселей (защита от «бомб» из пикселей)
    photo_max_side: int = 1600  # длинная сторона сохраняемого фото, пикселей
    thumb_max_side: int = 400  # длинная сторона превью, пикселей
    jpeg_quality: int = 85

    # Секрет для псевдонимов отправителей.
    sender_hmac_secret: str = ""

    # Подключение к Supabase.
    database_url: str = ""
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_storage_bucket: str = "problem-photos"

    # Токен Telegram-бота.
    telegram_bot_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_priority_config() -> PriorityConfig:
    s = get_settings()
    return PriorityConfig(
        age_thresholds_hours=s.priority_age_thresholds_hours,
        sender_thresholds=s.priority_sender_thresholds,
    )
