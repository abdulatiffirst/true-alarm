"""Ручной запуск удаления файлов из очереди.  Запуск:  python -m scripts.run_cleanup"""
import asyncio

from app.cleanup import run_cleanup
from app.config import get_settings
from app.storage import Storage


async def main() -> None:
    stats = await run_cleanup(Storage.from_settings(get_settings()), max_batches=100)
    print(f"Удалено файлов: {stats.deleted}, не удалось: {stats.failed}")


if __name__ == "__main__":
    asyncio.run(main())
