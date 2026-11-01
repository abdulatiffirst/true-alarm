"""Сценарий бота от /start до сохранения: настоящая база, поддельные Telegram и Supabase Storage."""
import asyncio
from datetime import datetime

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Location, Message, PhotoSize, Update, User

import app.bot as bot_module
from app.bot import build_dispatcher
from app.config import Settings, get_settings
from app.storage import Storage
from tests.helpers import FakeSupabase, make_jpeg

CHAT = Chat(id=1, type="private")


class Session:
    """Один человек, который общается с ботом. Все ответы бота запоминаются."""

    def __init__(self, dp, bot, user_id=1):
        self.dp, self.bot, self.user = dp, bot, User(id=user_id, is_bot=False, first_name="t")
        self.n = 0
        self.sent: list[str] = []

    def _message(self, **kw):
        self.n += 1
        return Message(message_id=self.n, date=datetime.now(), chat=Chat(id=self.user.id, type="private"),
                       from_user=self.user, **kw)

    async def _feed(self, **update_kw):
        self.n += 1
        before = len(self.sent)
        await self.dp.feed_update(self.bot, Update(update_id=self.n, **update_kw))
        return self.sent[before:]

    async def command(self, text):
        msg = self._message(text=text, entities=[{"type": "bot_command", "offset": 0, "length": len(text)}])
        return await self._feed(message=msg)

    async def choose(self, key):
        cb = CallbackQuery(id=str(self.n), from_user=self.user, chat_instance="ci",
                           data=f"category:{key}", message=self._message(text="x"))
        return await self._feed(callback_query=cb)

    async def photo(self, file_id, size=1000):
        photo = [PhotoSize(file_id=file_id, file_unique_id=file_id + "u", width=800, height=600, file_size=size)]
        return await self._feed(message=self._message(photo=photo))

    async def location(self, lat=41.2995, lon=69.2401):
        return await self._feed(message=self._message(location=Location(latitude=lat, longitude=lon)))

    async def text(self, text):
        return await self._feed(message=self._message(text=text))


@pytest.fixture
def env(monkeypatch, test_dsn, conn):
    monkeypatch.setenv("DATABASE_URL", test_dsn)
    monkeypatch.setenv("SENDER_HMAC_SECRET", "s" * 32)
    get_settings.cache_clear()

    sent_log: list[str] = []
    files: dict[str, bytes] = {}

    async def fake_answer(self, text, **kwargs):
        sent_log.append(text)

    async def fake_callback_answer(self, *args, **kwargs):
        return True

    async def fake_download(bot, file_id):
        return files[file_id]

    monkeypatch.setattr(Message, "answer", fake_answer)
    monkeypatch.setattr(CallbackQuery, "answer", fake_callback_answer)
    monkeypatch.setattr(bot_module, "download_photo", fake_download)

    fake = FakeSupabase()
    settings = Settings(_env_file=None, sender_hmac_secret="s" * 32, database_url=test_dsn)
    storage = Storage("https://abc.supabase.co", "key", fake.bucket, transport=fake.transport)

    class Env:
        pass

    e = Env()
    e.fake, e.files, e.sent_log, e.settings, e.storage, e.conn = fake, files, sent_log, settings, storage, conn
    yield e
    get_settings.cache_clear()


def run_scenario(env, scenario):
    async def main():
        dp = build_dispatcher(env.settings, env.storage)
        bot = Bot("123456:TEST-TOKEN-NOT-REAL")
        try:
            await scenario(lambda uid=1: _session(env, dp, bot, uid))
        finally:
            await bot.session.close()

    asyncio.run(main())


def _session(env, dp, bot, uid):
    s = Session(dp, bot, uid)
    s.sent = env.sent_log
    return s


def counts(conn):
    return (
        conn.execute("select count(*) as n from public.problems").fetchone()["n"],
        conn.execute("select count(*) as n from public.reports").fetchone()["n"],
    )


def test_happy_path_saves_cleaned_photo_and_thumbnail(env):
    env.files["f1"] = make_jpeg()

    async def scenario(new_session):
        s = new_session()
        await s.command("/start")
        await s.choose("waste")
        await s.photo("f1")
        await s.location()

    run_scenario(env, scenario)

    assert counts(env.conn) == (1, 1)
    assert "Заявка принята" in env.sent_log[-1]
    row = env.conn.execute("select photo_path, thumb_path, photo_sha256 from public.reports").fetchone()
    assert set(env.fake.objects) == {row["photo_path"], row["thumb_path"]}
    for blob in env.fake.objects.values():  # в хранилище нет метаданных исходника
        assert b"SecretMaker" not in blob and b"Exif" not in blob
    import hashlib

    assert row["photo_sha256"] == hashlib.sha256(env.files["f1"]).hexdigest()


def test_same_photo_is_rejected_at_the_photo_step(env):
    env.files["f1"] = make_jpeg()

    async def scenario(new_session):
        a = new_session(1)
        await a.command("/start"); await a.choose("waste"); await a.photo("f1"); await a.location()
        b = new_session(2)
        await b.command("/start"); await b.choose("infrastructure")
        env.sent_log.clear()
        await b.photo("f1")
        assert any("уже была отправлена" in t for t in env.sent_log)

    run_scenario(env, scenario)

    assert counts(env.conn) == (1, 1)
    assert len(env.fake.objects) == 2  # второй раз ничего не загружалось


def test_non_image_is_rejected_and_user_can_retry(env):
    env.files["bad"] = b"this is not a picture"
    env.files["good"] = make_jpeg()

    async def scenario(new_session):
        s = new_session()
        await s.command("/start"); await s.choose("waste")
        env.sent_log.clear()
        await s.photo("bad")
        assert any("Не удалось прочитать" in t for t in env.sent_log)
        await s.photo("good")  # сценарий остался на шаге фото
        await s.location()

    run_scenario(env, scenario)

    assert counts(env.conn) == (1, 1)


def test_oversized_photo_is_rejected_before_download(env):
    async def scenario(new_session):
        s = new_session()
        await s.command("/start"); await s.choose("waste")
        env.sent_log.clear()
        await s.photo("never-downloaded", size=50_000_000)  # скачивание не вызывается: файла нет в env.files
        assert any("слишком большое" in t for t in env.sent_log)

    run_scenario(env, scenario)
    assert counts(env.conn) == (0, 0)


def test_double_location_message_creates_only_one_report(env):
    env.files["f1"] = make_jpeg()

    async def scenario(new_session):
        s = new_session()
        await s.command("/start"); await s.choose("waste"); await s.photo("f1")
        await asyncio.gather(s.location(), s.location())

    run_scenario(env, scenario)

    assert counts(env.conn) == (1, 1)


def test_storage_failure_saves_nothing_and_user_is_told(env):
    env.files["f1"] = make_jpeg()
    env.fake.fail_upload_if = lambda path: True

    async def scenario(new_session):
        s = new_session()
        await s.command("/start"); await s.choose("waste"); await s.photo("f1"); await s.location()

    run_scenario(env, scenario)

    assert counts(env.conn) == (0, 0)
    assert "техническ" in env.sent_log[-1]


def test_thumbnail_upload_failure_queues_the_orphan_full_photo(env):
    env.files["f1"] = make_jpeg()
    env.fake.fail_upload_if = lambda path: path.endswith("_t.jpg")

    async def scenario(new_session):
        s = new_session()
        await s.command("/start"); await s.choose("waste"); await s.photo("f1"); await s.location()

    run_scenario(env, scenario)

    assert counts(env.conn) == (0, 0)
    queued = env.conn.execute("select object_path, reason, next_attempt_at > now() as later from public.storage_cleanup").fetchall()
    assert len(queued) == 1 and queued[0]["reason"] == "orphan" and queued[0]["later"]
    assert queued[0]["object_path"] in env.fake.objects  # файл ждёт удаления, а не потерян


def test_cancel_after_photo_saves_nothing(env):
    env.files["f1"] = make_jpeg()

    async def scenario(new_session):
        s = new_session()
        await s.command("/start"); await s.choose("waste"); await s.photo("f1")
        await s.command("/cancel")
        await s.location()  # после отмены местоположение уже ничего не запускает

    run_scenario(env, scenario)

    assert counts(env.conn) == (0, 0)
    assert env.fake.objects == {}
