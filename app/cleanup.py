"""Удаление файлов из нашего хранилища по очереди storage_cleanup."""
import asyncio
import logging
from dataclasses import dataclass

from app import db
from app.storage import Storage, StorageError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CleanupStats:
    deleted: int
    failed: int


def _claim(dsn: str | None, limit: int) -> list[dict]:
    with db.connect(dsn) as conn:
        return db.claim_cleanup_batch(conn, limit)


def _finish(dsn: str | None, deleted_ids: list[int], failed: dict[int, str]) -> None:
    with db.connect(dsn) as conn:
        db.finish_cleanup(conn, deleted_ids=deleted_ids, failed=failed)


async def run_cleanup(
    storage: Storage,
    *,
    dsn: str | None = None,
    batch_size: int = 50,
    max_batches: int = 5,
) -> CleanupStats:
    """Удаляет файлы из очереди. Безопасно запускать повторно и параллельно."""
    deleted = failed = 0
    for _ in range(max_batches):
        claimed = await asyncio.to_thread(_claim, dsn, batch_size)
        if not claimed:
            break
        ids = [row["id"] for row in claimed]
        try:
            await storage.delete([row["object_path"] for row in claimed])
        except StorageError as exc:
            failed += len(claimed)
            logger.warning("Удаление файлов не удалось (%s), повторим позже: %d шт.", exc, len(claimed))
            await asyncio.to_thread(_finish, dsn, [], {row_id: str(exc) for row_id in ids})
        else:
            deleted += len(claimed)
            await asyncio.to_thread(_finish, dsn, ids, {})
        if len(claimed) < batch_size:
            break
    return CleanupStats(deleted=deleted, failed=failed)
