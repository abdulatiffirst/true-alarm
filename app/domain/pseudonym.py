import hashlib
import hmac

MIN_SECRET_LENGTH = 16


def pseudonymize_telegram_id(telegram_id: int, secret: str) -> str:
    """Стабильный псевдоним Telegram ID (HMAC-SHA256, 64 hex-символа).

    Один и тот же ID с одним секретом всегда даёт один псевдоним.
    Это псевдонимизация, а не анонимность: тот, у кого есть секрет, может
    проверить, соответствует ли конкретный ID псевдониму.
    """
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(f"Секрет слишком короткий: нужно минимум {MIN_SECRET_LENGTH} символов")
    message = f"tg:{telegram_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()