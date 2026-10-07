import asyncio
import contextlib
import io
import logging
import socket
import time
import uuid
from dataclasses import dataclass, field
from urllib.request import getproxies, proxy_bypass

import uvicorn
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    ErrorEvent,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonCommands,
    MenuButtonWebApp,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from .catalog import PRESETS
from .media import MAX_BYTES, Media, normalize
from .provider import MockProvider, OpenAIProvider
from .runtime import RUNTIME_DIR, BotRuntime
from .service import Service
from .store import DomainError, Store


def buttons(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=b[0], callback_data=b[1], style=b[2] if len(b) > 2 else None)
                for b in row
            ]
            for row in rows
        ]
    )


def make_telegram_session():
    # Match HTTPX's existing network route; do not replace or print proxy credentials.
    proxies = getproxies()
    proxy = None if proxy_bypass("api.telegram.org") else proxies.get("https", proxies.get("all"))
    return AiohttpSession(proxy=proxy, timeout=20)


ICONS = {"hair": "💇", "clothes": "👕", "glasses": "👓", "background": "🌄", "enhance": "✨", "merge": "🧩"}
_choices = [(ICONS[key] + " " + p.label, "preset:" + key) for key, p in PRESETS.items()]
MENU = buttons([_choices[i : i + 2] for i in range(0, len(_choices), 2)])
NAV = {
    "edit": "📸 Изменить фото",
    "merge": "🧩 Объединить фото",
    "balance": "💎 Мои попытки",
    "results": "🖼 Мои результаты",
    "help": "❓ Как пользоваться",
    "support": "💬 Поддержка",
}
MAIN = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=NAV[key], style="primary" if key == "edit" else None) for key in pair]
        for pair in [("edit", "merge"), ("balance", "results"), ("help", "support")]
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Выберите действие ниже",
)
BACK = buttons([[("🏠 Главное меню", "nav:home")]])
CONSENT = buttons([[("Мне 18+, принимаю условия и согласен", "consent")]])
COMMANDS = [
    BotCommand(command=name, description=description)
    for name, description in [
        ("start", "Начать и выбрать функцию"),
        ("help", "Как пользоваться фотостудией"),
        ("demo", "Получить тестовые попытки"),
        ("balance", "Остаток попыток"),
        ("id", "Мой ID для пилотного доступа"),
        ("cancel", "Сбросить новую заявку"),
        ("result", "Получить готовый результат по ID"),
        ("delete", "Удалить локальные фото"),
        ("terms", "Условия тестирования"),
        ("privacy", "Обработка фотографий"),
        ("support", "Поддержка"),
    ]
]


@dataclass
class Draft:
    preset: str
    token: str = field(default_factory=lambda: uuid.uuid4().hex)
    created: float = field(default_factory=time.time)
    photos: list[str] = field(default_factory=list)
    photo_messages: set[int] = field(default_factory=set)
    description: str = ""


def build_dispatcher(settings, store, media, service):
    trial = settings.trial_access
    if trial != service.trial_access:
        raise ValueError("trial_access_service_mismatch")
    router = Router()
    router.message.filter(F.chat.type == "private")
    router.callback_query.filter(F.message.chat.type == "private")
    dp = Dispatcher(events_isolation=SimpleEventIsolation())
    drafts = {}

    def pricing():
        if trial:
            return "Любая функция, включая объединение — 1 бесплатная генерация. Всего 3 реальные генерации."
        return f"Правка — {service.cost('hair')} попытка. Объединение двух фото — {service.cost('merge')}."

    async def trial_balance(message, user):
        if not store.has_consent(user):
            await message.answer("Сначала подтвердите условия и согласие в /start.", reply_markup=CONSENT)
            return
        total, reserved = service.wallet(user)
        await message.answer(
            "Вам доступны всего 3 бесплатные реальные генерации OpenAI, включая объединение фото.\n"
            f"Осталось: {total - reserved}. В обработке: {reserved}.\n"
            "Каждый новый вариант использует 1 генерацию. Повторная выдача не предусмотрена.",
            reply_markup=MAIN,
        )

    def current(user):
        draft = drafts.get(user)
        if draft and time.time() - draft.created > 1800:
            drafts.pop(user, None)
            return None
        return draft

    async def choose_preset(message, user, key):
        if not store.has_consent(user):
            await message.answer("Сначала подтвердите условия и согласие на фото.", reply_markup=CONSENT)
            return
        if len(drafts) >= 1000:
            oldest = min(drafts, key=lambda owner: drafts[owner].created)
            drafts.pop(oldest)
        drafts[user] = Draft(key)
        choice = PRESETS[key]
        await message.answer(
            f"{ICONS[key]} {choice.label}\nШаг 1 из 3 · Загрузите {choice.inputs} фото.\n"
            "Лицо должно быть хорошо видно. Для лучшего качества отправьте фото файлом: "
            "JPEG/PNG/WebP до 10 MiB. Затем напишите желаемое изменение.",
            reply_markup=BACK,
        )

    async def send_result(message, user, job):
        record = store.result(user, job)
        path = media.path(record["result"])
        if not path.is_file() or time.time() - path.stat().st_mtime > 86400:
            raise DomainError("expired_result")
        mode = (
            "DEMO: тестовая копия, ИИ-правка не выполнялась.\n" if settings.image_provider == "mock" else ""
        )
        await message.answer_document(
            FSInputFile(path),
            caption=mode + "Ваш результат. Локальное хранение — 24 часа.",
            reply_markup=buttons([[("📸 Новая правка", "nav:edit"), ("🖼 Результаты", "nav:results")]]),
        )
        store.delivered(job)

    async def navigate(message, user, action):
        if action in {"home", "edit"}:
            drafts.pop(user, None)
            if not store.has_consent(user):
                await message.answer("Подтвердите условия и согласие на фотографии.", reply_markup=CONSENT)
                return
            if action == "home":
                await message.answer("Выберите действие на нижней панели.", reply_markup=MAIN)
            else:
                await message.answer(
                    "Что хотите изменить? " + pricing(), reply_markup=MENU
                )
        elif action == "merge":
            await choose_preset(message, user, "merge")
        elif action == "balance":
            total, reserved = service.wallet(user)
            markup = (
                buttons([[("🎁 Получить 3 тестовые попытки", "demo:grant")]])
                if (settings.image_provider == "mock" and settings.demo_credits)
                else BACK
            )
            await message.answer(
                f"💎 Доступно попыток: {total - reserved}\nВ обработке: {reserved}\n"
                + pricing() + "\nПродажи пока закрыты.",
                reply_markup=markup,
            )
        elif action == "results":
            rows = []
            for j in sorted(store.jobs(user), key=lambda j: j["created"], reverse=True):
                if j["status"] not in {"generated", "delivered"} or not j["result"]:
                    continue
                path = media.path(j["result"])
                if path.is_file() and time.time() - path.stat().st_mtime <= 86400:
                    rows.append([(f"{PRESETS[j['preset']].label} · {j['id'][:6]}", "result:" + j["id"])])
                if len(rows) == 5:
                    break
            await message.answer(
                "🖼 Ваши последние результаты. Повторное получение не тратит попытки.\nХранение — 24 часа."
                if rows
                else "Здесь появятся ваши результаты. Начните с кнопки «Изменить фото».\n"
                "Готовые файлы хранятся локально 24 часа.",
                reply_markup=buttons(rows) if rows else BACK,
            )
        elif action == "help":
            await message.answer(
                "Как пользоваться\n1. Выберите функцию.\n2. Отправьте своё фото (для объединения — два).\n"
                "3. Опишите желаемое изменение.\n4. Проверьте цену и нажмите «Создать».\n"
                "5. Получите файл и сохраните его.\n\n"
                + pricing() + " Новый вариант — отдельная попытка.\n"
                + (
                    "Сейчас демо: вы получите тестовую копию без ИИ-правки.\n"
                    if settings.image_provider == "mock"
                    else ""
                )
                + "Помощь и баланс сохраняют текущую заявку. Главное меню сбрасывает новую заявку; "
                "уже начатая обработка продолжается.",
                reply_markup=BACK,
            )
        elif action == "support":
            await message.answer(
                "Поддержка: "
                + (settings.support_contact or "контакт владельца ещё не настроен.")
                + "\nУкажите ID задачи или заказа. Ключи и банковские данные не присылайте.",
                reply_markup=BACK,
            )

    @router.message(F.text.in_(list(NAV.values())))
    async def navigation(message: Message):
        action = next(key for key, label in NAV.items() if label == message.text)
        await navigate(message, message.from_user.id, action)

    @router.callback_query(F.data.startswith("nav:"))
    async def navigation_callback(callback: CallbackQuery):
        action = callback.data.split(":", 1)[1]
        await callback.answer()
        if action in {*NAV, "home"}:
            await navigate(callback.message, callback.from_user.id, action)

    @router.message(CommandStart())
    async def start(message: Message):
        drafts.pop(message.from_user.id, None)
        service.wallet(message.from_user.id)
        mode = (
            "\nСейчас демо: результат — тестовая копия с отметкой DEMO."
            if settings.image_provider == "mock"
            else "\nВсего 3 бесплатные реальные генерации OpenAI после согласия. Любая функция — 1 генерация."
            if trial else ""
        )
        await message.answer(
            "Образ — примерка причёсок, одежды, очков и объединение фото.\n"
            "Загрузите собственные фото либо фото людей, давших согласие. Изображения в рабочем режиме "
            "передаются OpenAI; лицо может измениться. /terms /privacy /support" + mode,
            reply_markup=MAIN if store.has_consent(message.from_user.id) else CONSENT,
        )

    @router.callback_query(F.data == "consent")
    async def consent(callback: CallbackQuery):
        store.consent(callback.from_user.id)
        total, reserved = service.wallet(callback.from_user.id)
        await callback.answer("Согласие сохранено")
        await callback.message.answer(
            "Выберите действие на нижней панели. "
            + (
                f"Всего 3 бесплатные реальные генерации OpenAI. Доступно: {total - reserved}. "
                "Объединение и каждый новый вариант — 1 генерация."
                if trial else "Тестовые попытки доступны в «Мои попытки»."
                if settings.image_provider == "mock"
                else "Для пилотного доступа сообщите владельцу свой /id."
            ),
            reply_markup=MAIN,
        )

    @router.message(Command("id"))
    async def identity(message: Message):
        await message.answer(
            f"Ваш Telegram ID: {message.from_user.id}" if trial
            else f"Ваш ID для пилотного доступа: {message.from_user.id}"
        )

    @router.errors()
    async def handle_error(event: ErrorEvent):
        # Exceptions can include private payloads. Log only the safe class name.
        logging.getLogger(__name__).error("telegram_handler_error type=%s", type(event.exception).__name__)
        message = event.update.message or (
            event.update.callback_query.message if event.update.callback_query else None
        )
        if isinstance(message, Message) and message.chat.type == "private":
            with contextlib.suppress(Exception):
                await message.answer("Не удалось выполнить действие. Попробуйте ещё раз или /support.")
        return True

    @router.message(Command("demo"))
    async def demo(message: Message):
        if trial:
            await trial_balance(message, message.from_user.id)
            return
        if settings.image_provider != "mock" or not settings.demo_credits:
            await message.answer("Бесплатные тестовые кредиты здесь недоступны.")
            return
        ok = store.grant_demo(message.from_user.id)
        await message.answer(
            "Начислены 3 тестовых кредита." if ok else "Демо уже использовано или нужно /start."
        )

    @router.callback_query(F.data == "demo:grant")
    async def demo_callback(callback: CallbackQuery):
        if trial:
            await callback.answer()
            await trial_balance(callback.message, callback.from_user.id)
            return
        if settings.image_provider != "mock" or not settings.demo_credits:
            await callback.answer("Тестовые попытки недоступны", show_alert=True)
            return
        ok = store.grant_demo(callback.from_user.id)
        await callback.answer(
            "Начислены 3 попытки" if ok else "Демо уже использовано или нужно согласие", show_alert=True
        )
        await navigate(callback.message, callback.from_user.id, "balance")

    @router.message(Command("balance"))
    async def balance(message: Message):
        await navigate(message, message.from_user.id, "balance")

    @router.message(Command("help"))
    async def help_message(message: Message):
        await navigate(message, message.from_user.id, "help")

    @router.message(Command("terms"))
    async def terms(message: Message):
        await message.answer(
            "Условия пилота: 18+, права и согласие всех изображённых людей, "
            "полностью одетые образы. Не используйте результат для обмана. "
            + pricing() + " Новый вариант использует ещё одну попытку. "
            "При ошибке обработки резерв возвращается. Качество и сходство не гарантированы. "
            "Оплаты в Telegram сейчас нет; условия продажи будут опубликованы перед запуском."
        )

    @router.message(Command("privacy"))
    async def privacy(message: Message):
        await message.answer(
            "Фото и описание хранятся локально до 24 часов после обработки; зависшие задачи "
            "хранятся до разбора поддержкой. В рабочем режиме фото передаются OpenAI. "
            "Храним Telegram ID и записи баланса для учёта. /delete удаляет локальные фото "
            "и сбрасывает согласие; это не удаляет сообщения Telegram и данные у провайдера."
        )

    @router.message(Command("support", "paysupport"))
    async def support(message: Message):
        await navigate(message, message.from_user.id, "support")

    @router.message(Command("buy"))
    async def buy(message: Message):
        await message.answer("Покупки в боте пока закрыты. Сейчас проверяем качество и сценарии MVP.")

    @router.message(Command("cancel"))
    async def cancel(message: Message):
        drafts.pop(message.from_user.id, None)
        await message.answer("Новая заявка сброшена. Уже начатая обработка продолжается.", reply_markup=MAIN)

    @router.message(Command("delete"))
    async def delete(message: Message):
        try:
            service.delete(message.from_user.id)
            drafts.pop(message.from_user.id, None)
            await message.answer(
                "Локальные фото и описание удалены. Для нового использования нажмите /start.",
                reply_markup=ReplyKeyboardRemove(),
            )
        except DomainError:
            await message.answer("Есть незавершённая задача. Дождитесь результата или обратитесь в /support.")

    @router.message(Command("result"))
    async def result(message: Message):
        try:
            job = (message.text or "").split(maxsplit=1)[1].strip()
            await send_result(message, message.from_user.id, job)
        except (DomainError, ValueError, IndexError):
            await message.answer("Результат недоступен. Используйте /result ID из сообщения о задаче.")

    @router.callback_query(F.data.startswith("result:"))
    async def result_callback(callback: CallbackQuery):
        try:
            await callback.answer()
            await send_result(callback.message, callback.from_user.id, callback.data.split(":", 1)[1])
        except (DomainError, ValueError, OSError):
            await callback.message.answer("Результат недоступен или срок хранения истёк.", reply_markup=BACK)

    @router.callback_query(F.data.startswith("preset:"))
    async def choose(callback: CallbackQuery):
        if not store.has_consent(callback.from_user.id):
            await callback.answer("Сначала подтвердите условия в /start", show_alert=True)
            return
        key = callback.data.split(":", 1)[1]
        if key not in PRESETS:
            await callback.answer("Неизвестная функция")
            return
        await callback.answer()
        await choose_preset(callback.message, callback.from_user.id, key)

    @router.message(F.photo | F.document)
    async def photo(message: Message):
        draft = current(message.from_user.id)
        if not draft or not store.has_consent(message.from_user.id):
            await message.answer("Сначала /start и выберите функцию.")
            return
        choice = PRESETS[draft.preset]
        if message.message_id in draft.photo_messages:
            return
        if len(draft.photos) >= choice.inputs:
            await message.answer("Фото уже загружены. Напишите, какой образ хотите, или /cancel.")
            return
        attachment = message.photo[-1] if message.photo else message.document
        if attachment.file_size and attachment.file_size > MAX_BYTES:
            await message.answer("Максимум 10 MiB на фото.")
            return
        buffer = io.BytesIO()
        await message.bot.download(attachment, destination=buffer)
        try:
            data = normalize(buffer.getvalue())
            path = media.save(message.from_user.id, data)
        except (ValueError, OSError):
            await message.answer("Не удалось прочитать фото. Нужен JPEG, PNG или WebP до 10 MiB/24 MP.")
            return
        draft.photos.append(path)
        draft.photo_messages.add(message.message_id)
        if len(draft.photos) < choice.inputs:
            await message.answer("Первое фото принято. Отправьте второе с согласия изображённого человека.")
        else:
            await message.answer(
                "Шаг 2 из 3 · Фото приняты. Опишите желаемое изменение, например «каре до плеч».",
                reply_markup=BACK,
            )

    @router.message(F.text)
    async def description(message: Message):
        if (message.text or "").startswith("/"):
            await message.answer("Неизвестная команда. Откройте /help или нижнюю панель.")
            return
        draft = current(message.from_user.id)
        if not draft or len(draft.photos) != PRESETS[draft.preset].inputs:
            await message.answer("Выберите функцию и загрузите фото через /start.")
            return
        if not 1 <= len(message.text.strip()) <= 1500:
            await message.answer("Описание должно быть от 1 до 1500 символов.")
            return
        draft.description = message.text.strip()
        draft.token = uuid.uuid4().hex
        choice = PRESETS[draft.preset]
        cost = service.cost(draft.preset)
        unit = "бесплатная генерация" if trial else "попытка(и)"
        await message.answer(
            f"Шаг 3 из 3 · {choice.label}\nИзменение: {draft.description}\n"
            f"Стоимость: {cost} {unit}. Один результат.\n"
            "Каждый новый вариант — новая попытка.",
            reply_markup=buttons(
                [
                    [(f"Создать · {cost} {unit}", "confirm:" + draft.token, "success")],
                    [("🏠 Главное меню", "nav:home")],
                ]
            ),
        )

    @router.callback_query(F.data.startswith("confirm:"))
    async def confirm(callback: CallbackQuery):
        user = callback.from_user.id
        token = callback.data.split(":", 1)[1]
        draft = current(user)
        if draft is None or draft.token != token or not draft.description:
            await callback.answer("Эта заявка уже подтверждена или устарела", show_alert=True)
            return
        try:
            job = service.submit(user, token, draft.preset, draft.photos, draft.description)
            drafts.pop(user, None)
            await callback.answer("Задача принята")
            await callback.message.answer(
                f"Обрабатываем. ID: {job}\nПовторная доставка: /result {job}\nОбычно это занимает до 2 минут."
            )
        except DomainError as exc:
            explanation = {
                "insufficient_credits": "Недостаточно кредитов. Проверьте /balance.",
                "already_active": "Уже есть задача в обработке. Проверьте /support.",
                "trial_exhausted": "Все 3 бесплатные генерации использованы. Новые попытки не выдаются.",
                "trial_not_granted": "Сначала подтвердите условия и согласие в /start.",
            }
            await callback.answer(explanation.get(str(exc), "Не удалось принять заявку"), show_alert=True)

    dp.include_router(router)
    return dp


async def run(settings):
    if not settings.bot_token:
        raise ValueError("missing_telegram_token")
    with BotRuntime(RUNTIME_DIR, settings.image_provider) as runtime:
        bot = Bot(settings.bot_token, session=make_telegram_session())
        tasks, provider, studio_server, studio_socket = [], None, None, None
        try:
            identity = await bot.get_me()
            if settings.expected_bot_username and (
                (identity.username or "").casefold() != settings.expected_bot_username.casefold()
            ):
                raise ValueError("unexpected_telegram_bot")
            if (await bot.get_webhook_info()).url:
                raise ValueError("telegram_webhook_already_configured")
            store, media = Store(settings.data_dir / "db.sqlite3"), Media(settings.data_dir)
            provider = (
                MockProvider()
                if settings.image_provider == "mock"
                else OpenAIProvider(settings.openai_key, settings.image_model, settings.quality)
            )

            async def deliver(user, job, path):
                if user == 0:  # Local browser demo has no Telegram destination.
                    return
                caption = f"Готово. ID: {job}. Генеративный результат; проверьте сходство."
                if settings.image_provider == "mock":
                    caption = "DEMO: тестовая копия, ИИ-правка не выполнялась. " + caption
                await bot.send_document(
                    user,
                    FSInputFile(path),
                    caption=caption,
                    reply_markup=buttons(
                        [
                            [("📸 Новая правка", "nav:edit"), ("🖼 Мои результаты", "nav:results")],
                        ]
                    ),
                )

            async def notify(user, job):
                if user == 0:
                    return
                reason = store.job(job)["error"]
                explanations = {
                    "provider_quota": "У сервиса OpenAI закончился доступный баланс или квота.",
                    "provider_rate_limit": "OpenAI временно ограничил частоту запросов.",
                    "provider_authentication": "Подключение OpenAI требует проверки владельцем.",
                }
                await bot.send_message(
                    user,
                    explanations.get(reason, "Обработка не выполнена.")
                    + f" Резерв {'генерации' if settings.trial_access else 'кредитов'} возвращён. ID: {job}",
                )

            service = Service(store, media, provider, deliver, notify, trial_access=settings.trial_access)
            dp = build_dispatcher(settings, store, media, service)
            await bot.set_my_commands([
                command.model_copy(update={"description": {
                    "demo": "Мои бесплатные генерации", "id": "Мой Telegram ID",
                }[command.command]})
                if settings.trial_access and command.command in {"demo", "id"} else command
                for command in COMMANDS
            ])
            await bot.set_chat_menu_button(
                menu_button=(
                    MenuButtonWebApp(text="Студия", web_app=WebAppInfo(url=settings.mini_app_url))
                    if settings.mini_app_url
                    else MenuButtonCommands()
                )
            )
            # Peek without advancing offset; actual polling still receives pending updates.
            await bot.get_updates(timeout=0, limit=1, allowed_updates=dp.resolve_used_update_types())

            async def watch_stop():
                while True:
                    if runtime.stop_requested():
                        try:
                            await dp.stop_polling()
                        except RuntimeError:
                            pass  # Polling startup has not completed yet.
                        else:
                            return
                    await asyncio.sleep(0.5)

            if settings.studio_enabled:
                from .studio import create_studio_app

                class StudioServer(uvicorn.Server):
                    def capture_signals(self):
                        return contextlib.nullcontext()

                # Bind before worker/polling readiness: an occupied port cannot hide a wrong preview.
                studio_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                studio_socket.bind(("127.0.0.1", settings.studio_port))
                studio_server = StudioServer(
                    uvicorn.Config(
                        create_studio_app(settings, store, media, service),
                        host="127.0.0.1",
                        port=settings.studio_port,
                        access_log=False,
                        log_level="warning",
                        lifespan="off",
                        proxy_headers=False,
                    )
                )
                studio_task = asyncio.create_task(studio_server.serve(sockets=[studio_socket]))
                tasks.append(studio_task)
                while not studio_server.started:
                    if studio_task.done():
                        await studio_task
                        raise RuntimeError("studio_start_failed")
                    await asyncio.sleep(0.05)
            tasks.extend([asyncio.create_task(service.worker()), asyncio.create_task(watch_stop())])
            runtime.ready(identity.username)
            await dp.start_polling(bot, close_bot_session=False)
        finally:
            if studio_server:
                studio_server.should_exit = True
                # Stop accepting browser requests before stopping the single image worker.
                if tasks:
                    with contextlib.suppress(TimeoutError, Exception):
                        await asyncio.wait_for(asyncio.shield(tasks[0]), timeout=5)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if provider:
                await provider.close()
            await bot.session.close()
            if studio_socket:
                studio_socket.close()
