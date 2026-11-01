import asyncio
from datetime import datetime

import pytest
from aiogram import Bot
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, Message, Update, User

from app.bot import ReportForm, build_dispatcher
from app.config import Settings


@pytest.mark.parametrize("state", [ReportForm.category, ReportForm.photo, ReportForm.location])
def test_cancel_works_in_every_step(monkeypatch, state):
    sent = []

    async def fake_answer(self, text, **kwargs):
        sent.append(text)

    monkeypatch.setattr(Message, "answer", fake_answer)

    async def scenario():
        dp = build_dispatcher(Settings(_env_file=None))
        bot = Bot("123456:TEST-TOKEN-NOT-REAL")
        try:
            key = StorageKey(bot_id=bot.id, chat_id=1, user_id=1)
            await dp.storage.set_state(key, state)

            message = Message(
                message_id=1,
                date=datetime.now(),
                chat=Chat(id=1, type="private"),
                from_user=User(id=1, is_bot=False, first_name="t"),
                text="/cancel",
                entities=[{"type": "bot_command", "offset": 0, "length": 7}],
            )
            await dp.feed_update(bot, Update(update_id=1, message=message))

            assert sent and "отменена" in sent[0]
            assert await dp.storage.get_state(key) is None
        finally:
            await bot.session.close()

    asyncio.run(scenario())
