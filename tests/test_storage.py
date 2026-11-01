import asyncio
import json

import httpx
import pytest

from app.storage import Storage, StorageError


def make_storage(handler):
    return Storage("https://abc.supabase.co/", "SERVICE-KEY-123", "bkt", transport=httpx.MockTransport(handler))


def test_upload_request_shape():
    seen = {}

    def handler(request):
        seen["method"], seen["url"] = request.method, str(request.url)
        seen["headers"], seen["body"] = request.headers, request.content
        return httpx.Response(200, json={})

    asyncio.run(make_storage(handler).upload("a_t.jpg", b"DATA"))

    assert seen["method"] == "POST"
    assert seen["url"] == "https://abc.supabase.co/storage/v1/object/bkt/a_t.jpg"
    assert seen["headers"]["authorization"] == "Bearer SERVICE-KEY-123"
    assert seen["headers"]["content-type"] == "image/jpeg"
    assert seen["headers"]["x-upsert"] == "false"
    assert seen["body"] == b"DATA"


def test_delete_uses_bulk_endpoint_with_prefixes():
    seen = {}

    def handler(request):
        seen["method"], seen["url"] = request.method, str(request.url)
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json=[])  # пустой ответ: файлов уже нет, это не ошибка

    asyncio.run(make_storage(handler).delete(["a.jpg", "b.jpg"]))

    assert seen["method"] == "DELETE"
    assert seen["url"] == "https://abc.supabase.co/storage/v1/object/bkt"
    assert seen["json"] == {"prefixes": ["a.jpg", "b.jpg"]}


def test_delete_of_empty_list_makes_no_request():
    def handler(request):
        raise AssertionError("запрос не должен отправляться")

    asyncio.run(make_storage(handler).delete([]))


def test_http_error_becomes_storage_error_without_secrets():
    storage = make_storage(lambda request: httpx.Response(500, json={"error": "SERVICE-KEY-123"}))
    with pytest.raises(StorageError) as exc:
        asyncio.run(storage.delete(["a.jpg"]))
    assert str(exc.value) == "HTTP 500"
    assert "SERVICE-KEY" not in str(exc.value)


def test_network_error_becomes_storage_error():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(StorageError) as exc:
        asyncio.run(make_storage(handler).upload("a.jpg", b"x"))
    assert "ConnectError" in str(exc.value)


def test_missing_configuration_is_reported():
    with pytest.raises(RuntimeError):
        Storage("", "key", "bkt")
