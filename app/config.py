from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.priority import PriorityConfig


class Settings(BaseSettings):
    """Все настройки проекта. Значения берутся из переменных окружения или из файла .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # лишние переменные из .env не вызывают ошибку
    )

    app_env: str = "local"
    log_level: str = "INFO"

    # Пороги приоритета (единственное место, где заданы эти числа).
    priority_age_thresholds_hours: tuple[int, int, int] = (24, 72, 168)
    priority_sender_thresholds: tuple[int, int, int] = (3, 6, 11)

    # Радиус поиска похожих проблем для объединения заявок, в метрах.
    merge_radius_m: int = 30

    # Секрет для псевдонимов отправителей. Пустое значение допустимо только пока
    # бот не запущен; настоящее значение задаётся в .env.
    sender_hmac_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_priority_config() -> PriorityConfig:
    s = get_settings()
    return PriorityConfig(
        age_thresholds_hours=s.priority_age_thresholds_hours,
        sender_thresholds=s.priority_sender_thresholds,
    )