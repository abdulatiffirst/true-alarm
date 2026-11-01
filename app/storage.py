"""Клиент Supabase Storage (загрузка и удаление файлов нашего бакета).

Мы управляем только своим хранилищем. Файлы в инфраструктуре Telegram
этим кодом не удаляются.
"""
from urllib.parse import quote

import httpx

from app.config import Settings


class StorageError(Exception):
    """Операция с хранилищем не удалась. Текст не содержит секретов."""


class Storage:
    def __init__(
        self,
        base_url: str,
        service_key: str,
        bucket: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not base_url or not service_key or not bucket:
            raise RuntimeError("Для Storage нужны SUPABASE_URL, SUPABASE_SERVICE_KEY и имя бакета")
        self._base = base_url.rstrip("/")
        self._key = service_key
        self._bucket = bucket
        self._transport = transport
        self._timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs) -> "Storage":
        return cls(
            settings.supabase_url,
            settings.supabase_service_key,
            settings.supabase_storage_bucket,
            **kwargs,
        )

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._key}", "apikey": self._key}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
                response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise StorageError(f"network error: {type(exc).__name__}") from None
        if response.status_code >= 400:
            raise StorageError(f"HTTP {response.status_code}")
        return response

    async def upload(self, path: str, data: bytes, content_type: str = "image/jpeg") -> None:
        url = f"{self._base}/storage/v1/object/{self._bucket}/{quote(path)}"
        headers = self._headers(content_type) | {"x-upsert": "false"}
        await self._request("POST", url, content=data, headers=headers)

    async def delete(self, paths: list[str]) -> None:
        """Удаляет файлы одним запросом. Уже отсутствующие файлы ошибкой не считаются."""
        if not paths:
            return
        url = f"{self._base}/storage/v1/object/{self._bucket}"
        await self._request(
            "DELETE", url, json={"prefixes": list(paths)}, headers=self._headers("application/json")
        )
