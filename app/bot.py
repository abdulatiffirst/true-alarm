import asyncio
import logging
from io import BytesIO
from uuid import uuid4

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from app import db
from app.cleanup import run_cleanup
from app.config import Settings, get_settings
from app.domain.categories import CATEGORIES
from app.domain.pseudonym import pseudonymize_telegram_id
from app.photos import PhotoRejected, ProcessedPhoto, process_photo
from app.storage import Storage

logger = logging.getLogger(__name__)


class ReportForm(StatesGroup):
    category = State()
    photo = State()
    location = State()


def categories_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"category:{key}")]
            for key, label in CATEGORIES.items()
        ]
    )


def location_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отправить текущую геопозицию", request_location=True)]],
        resize_keyboard=True,
    )


async def download_photo(bot: Bot, file_id: str) -> bytes:
    telegram_file = await bot.get_file(file_id)
    if not telegram_file.file_path:
        raise RuntimeError("Telegram не вернул путь к файлу")
    buffer = BytesIO()
    await bot.download_file(telegram_file.file_path, destination=buffer)
    return buffer.getvalue()


def _process(settings: Settings, data: bytes) -> ProcessedPhoto:
    return process_photo(
        data,
        max_bytes=settings.max_upload_bytes,
        max_pixels=settings.max_image_pixels,
        max_side=settings.photo_max_side,
        thumb_side=settings.thumb_max_side,
        quality=settings.jpeg_quality,
    )


def _photo_already_accepted(sha256: str) -> bool:
    with db.connect() as conn:
        return db.photo_hash_exists(conn, sha256)


def _accept(**kwargs) -> db.AcceptedReport:
    with db.connect() as conn:
        return db.accept_report(conn, **kwargs)


def _queue_orphans(paths: list[str]) -> None:
    """Файлы загружены, но заявка не сохранена: ставим их в очередь на удаление.

    Первая попытка откладывается на 10 минут: если ответ базы потерялся уже после
    успешного сохранения, живую фотографию удалять нельзя (при удалении очередь
    дополнительно проверяет, не ссылается ли на файл заявка).
    """
    if not paths:
        return
    try:
        with db.connect() as conn:
            for path in paths:
                db.enqueue_cleanup(conn, path, "orphan", delay_seconds=600)
    except Exception:
        logger.exception("Не удалось поставить файлы в очередь удаления")


_background_tasks: set[asyncio.Task] = set()


def schedule_cleanup(storage: Storage) -> None:
    """Запускает удаление файлов из очереди в фоне, не задерживая ответ пользователю."""

    async def runner() -> None:
        try:
            stats = await run_cleanup(storage)
            if stats.deleted or stats.failed:
                logger.info("Очистка хранилища: удалено=%d, ошибок=%d", stats.deleted, stats.failed)
        except Exception:
            logger.exception("Очистка хранилища завершилась ошибкой")

    task = asyncio.create_task(runner())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def build_dispatcher(settings: Settings, storage: Storage | None = None) -> Dispatcher:
    dp = Dispatcher()
    holder = {"storage": storage}

    def get_storage() -> Storage:
        if holder["storage"] is None:
            holder["storage"] = Storage.from_settings(settings)
        return holder["storage"]

    dp.message.filter(F.chat.type == "private")

    # Команды регистрируются ПЕРВЫМИ: иначе обработчики состояний «перехватят» /cancel.
    @dp.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "Заявка отменена. Чтобы начать заново, отправьте /start.",
            reply_markup=ReplyKeyboardRemove(),
        )

    @dp.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(ReportForm.category)
        await message.answer(
            "Добро пожаловать в True Alarm!\n\n"
            "Бот принимает сообщения о мусоре и повреждениях городской среды. "
            "Выберите тип проблемы:",
            reply_markup=categories_keyboard(),
        )

    @dp.callback_query(ReportForm.category, F.data.startswith("category:"))
    async def choose_category(callback: CallbackQuery, state: FSMContext) -> None:
        category_key = (callback.data or "").split(":", 1)[-1]
        if category_key not in CATEGORIES:
            await callback.answer("Неизвестная категория.", show_alert=True)
            return
        await state.update_data(category=category_key)
        await state.set_state(ReportForm.photo)
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                f"Категория: {CATEGORIES[category_key]}\n\n"
                "Отправьте одну фотографию проблемы. Для отмены: /cancel."
            )

    @dp.message(ReportForm.photo, F.photo)
    async def receive_photo(message: Message, state: FSMContext) -> None:
        best = message.photo[-1]
        if best.file_size and best.file_size > settings.max_upload_bytes:
            await message.answer(
                f"Фото слишком большое (максимум {settings.max_upload_bytes // 1_000_000} МБ). "
                "Отправьте другое фото или /cancel."
            )
            return
        # Проверяем фото сразу, чтобы человек не узнал о проблеме только в конце.
        try:
            data = await download_photo(message.bot, best.file_id)
            processed = await asyncio.to_thread(_process, settings, data)
            if await asyncio.to_thread(_photo_already_accepted, processed.sha256):
                await message.answer(
                    "Эта фотография уже была отправлена ранее. Сделайте новый снимок проблемы "
                    "или отправьте /cancel."
                )
                return
        except PhotoRejected as exc:
            await message.answer(f"{exc.user_message}\nОтправьте другое фото или /cancel.")
            return
        except Exception:
            logger.exception("Не удалось проверить фото")
            await message.answer("Не удалось получить фото. Попробуйте ещё раз или отправьте /cancel.")
            return

        await state.update_data(photo_file_id=best.file_id)
        await state.set_state(ReportForm.location)
        await message.answer(
            "Фото получено.\n\n"
            "Теперь отправьте местоположение проблемы. Нажмите кнопку ниже или прикрепите "
            "точку на карте через меню вложений Telegram: проблема может быть не там, "
            "где вы сейчас находитесь. Для отмены: /cancel.",
            reply_markup=location_keyboard(),
        )

    @dp.message(ReportForm.photo)
    async def photo_required(message: Message) -> None:
        await message.answer("Нужна фотография. Отправьте одно фото или /cancel для отмены.")

    @dp.message(ReportForm.location, F.location)
    async def receive_location(message: Message, state: FSMContext) -> None:
        if message.from_user is None or message.location is None:
            await message.answer("Не удалось прочитать местоположение. Попробуйте ещё раз.")
            return

        data = await state.get_data()
        category_key = data.get("category")
        photo_file_id = data.get("photo_file_id")
        # Сразу сбрасываем сценарий: повторное сообщение не запустит сохранение второй раз.
        await state.clear()

        if category_key not in CATEGORIES or not photo_file_id:
            await message.answer(
                "Сценарий устарел. Отправьте /start, чтобы начать заново.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        uploaded: list[str] = []
        try:
            data = await download_photo(message.bot, photo_file_id)
            processed = await asyncio.to_thread(_process, settings, data)

            if await asyncio.to_thread(_photo_already_accepted, processed.sha256):
                await message.answer(
                    "Эта фотография уже была отправлена ранее, поэтому заявка не создана. "
                    "Сделайте новый снимок проблемы.",
                    reply_markup=ReplyKeyboardRemove(),
                )
                return

            base = uuid4().hex
            photo_path, thumb_path = f"{base}.jpg", f"{base}_t.jpg"
            storage_client = get_storage()
            await storage_client.upload(photo_path, processed.full)
            uploaded.append(photo_path)
            await storage_client.upload(thumb_path, processed.thumb)
            uploaded.append(thumb_path)

            sender_hash = pseudonymize_telegram_id(message.from_user.id, settings.sender_hmac_secret)
            result = await asyncio.to_thread(
                _accept,
                sender_hash=sender_hash,
                category=category_key,
                latitude=message.location.latitude,
                longitude=message.location.longitude,
                photo_sha256=processed.sha256,
                photo_path=photo_path,
                thumb_path=thumb_path,
            )
        except PhotoRejected as exc:
            await message.answer(
                f"{exc.user_message}\nОтправьте /start, чтобы создать заявку заново.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        except db.DuplicatePhoto:
            await asyncio.to_thread(_queue_orphans, uploaded)
            await message.answer(
                "Эта фотография уже была отправлена ранее, поэтому заявка не создана.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        except Exception:
            logger.exception("Не удалось сохранить заявку")
            await asyncio.to_thread(_queue_orphans, uploaded)
            await message.answer(
                "Не удалось сохранить заявку из-за технической ошибки. Попробуйте позже.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        await message.answer(
            f"Заявка принята. Спасибо!\n\nКатегория: {CATEGORIES[category_key]}",
            reply_markup=ReplyKeyboardRemove(),
        )
        logger.info("Заявка принята: problem=%s report=%s", result.problem_id, result.report_id)
        if result.queued_for_deletion:
            schedule_cleanup(get_storage())

    @dp.message(ReportForm.location)
    async def location_required(message: Message) -> None:
        await message.answer(
            "Это не местоположение. Нажмите кнопку ниже или прикрепите точку на карте "
            "через меню вложений. Для отмены: /cancel.",
            reply_markup=location_keyboard(),
        )

    return dp


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # не пишем адреса запросов в лог

    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")
    if len(settings.sender_hmac_secret) < 16:
        raise RuntimeError("SENDER_HMAC_SECRET должен содержать минимум 16 символов")
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL не задан")
    if not settings.supabase_url or not settings.supabase_service_key:
        raise RuntimeError("SUPABASE_URL и SUPABASE_SERVICE_KEY должны быть заданы")

    bot = Bot(token=settings.telegram_bot_token)
    storage = Storage.from_settings(settings)
    dp = build_dispatcher(settings, storage)
    schedule_cleanup(storage)  # доделать удаление файлов, оставшееся от прошлого запуска
    logger.info("Бот True Alarm запущен (polling).")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
