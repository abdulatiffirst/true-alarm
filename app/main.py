from fastapi import FastAPI

from app.config import get_settings

settings = get_settings()

app = FastAPI(title="True Alarm", version="0.0.1")


@app.get("/health")
def health() -> dict:
    """Проверка, что сервис запущен."""
    return {"status": "ok", "env": settings.app_env}