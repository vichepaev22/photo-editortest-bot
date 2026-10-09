import asyncio
import contextlib
import io
import logging
import socket
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from urllib.request import getproxies, proxy_bypass

import uvicorn
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    CopyTextButton,
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

from .admin import register_admin
from .catalog import PRESETS
from .media import MAX_BYTES, Media, normalize
from .provider import MockProvider, OpenAIProvider
from .purchase_demo import PREFIX as PURCHASE_PREFIX
from .purchase_demo import PurchaseDemo
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


ICONS = {"hair": "💇", "clothes": "👕", "glasses": "👓", "background": "🌄", "enhance": "✨", "merge": "🧩",
         "document": "👔", "document_original": "📄"}
EXAMPLES = {
    "hair": "Каре до плеч с мягкими волнами",
    "clothes": "Бежевый тренч поверх белой футболки",
    "glasses": "Тонкая чёрная оправа с прозрачными линзами",
    "background": "Парк с мягким вечерним светом",
    "enhance": "Чётче детали и естественные цвета, без ретуши лица",
    "merge": "Мы вместе в парке, сохрани лица и пропорции",
    "document": "Белый фон, светлая рубашка; сохранить лицо. Фото на документы, 4 фото 35×45 мм",
    "document_original": "Подготовить исходное фото: 4 одинаковых снимка 35×45 мм без ИИ",
}
DOCUMENT_OPTIONS = {
    "original": EXAMPLES["document_original"],
    "keep": "Белый фон, сохранить исходную одежду и лицо. Фото на документы, 4 фото 35×45 мм",
    "suit": "Белый фон, тёмный деловой костюм и светлая рубашка; сохранить лицо. Фото на документы",
    "shirt": EXAMPLES["document"],
}
HAIR_EXAMPLES = {
    9: "Одно изображение: сетка 3×3 из 9 разных причёсок — каре, боб, пикси, каскад, "
       "прямые длинные волосы, волны, кудри, чёлка, собранные волосы. "
       "В каждой ячейке человек с моего фото. Сохрани лицо, возраст, ракурс, одежду и фон; без текста.",
    12: "Одно изображение: сетка 3×4 из 12 разных причёсок — каре, боб, пикси, каскад, "
        "прямые длинные волосы, волны, кудри, чёлка, хвост, пучок, коса, короткая стрижка. "
        "В каждой ячейке человек с моего фото. Сохрани лицо, возраст, ракурс, одежду и фон; без текста.",
}
RESULT_FILENAME = "Образ · результат.jpg"


def result_presentation(record):
    if record["preset"] == "document_original":
        return "Образ · 4 фото 35x45 мм.png", (
            "Образ · 4 фото для печати 📄\n\n35×45 мм · PNG · 300 DPI.\n"
            "Печатайте в масштабе 100%, без подгонки.\n"
            "Проверьте размер после печати и требования вашего документа."
        )
    if record["preset"] == "document":
        return "Образ · фото на документы.png", (
            "Образ · фото на документы 📄\n\n4 фото 35×45 мм · PNG · 300 DPI.\n"
            "Это ИИ-портрет: для паспорта РФ не подходит.\nПечатайте в масштабе 100%, без подгонки."
        )
    return RESULT_FILENAME, "Образ · готово ✨\n\nСохраните фото и проверьте сходство."


_choices = [(ICONS[key] + " " + p.label, "preset:" + key)
            for key, p in PRESETS.items() if key != "document_original"]
MENU = buttons([_choices[i : i + 2] for i in range(0, len(_choices), 2)])
NAV = {
    "edit": "📸 Изменить фото",
    "merge": "🧩 Объединить фото",
    "balance": "💎 Мои попытки",
    "results": "🖼 Мои результаты",
    "help": "❓ Как пользоваться",
    "support": "💬 Поддержка",
    "buy": "🛍 Купить / продлить доступ",
}
MAIN = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=NAV[key], **({"style": "success"} if key == "support" else {})) for key in row]
        for row in [("edit", "merge"), ("balance", "results"), ("help", "support"), ("buy",)]
    ],
    resize_keyboard=True,
    is_persistent=False,
    one_time_keyboard=False,
    input_field_placeholder="Выберите действие ниже",
)
BACK = buttons([[("🏠 Главное меню", "nav:home")]])
CONSENT = buttons([[("Для совершеннолетних · принимаю условия", "consent", "success")]])
COMMANDS = [
    BotCommand(command=name, description=description)
    for name, description in [
        ("start", "Начать и выбрать функцию"),
        ("help", "Как пользоваться фотостудией"),
        ("demo", "Получить тестовые попытки"),
        ("balance", "Остаток попыток"),
        ("id", "Мой ID для пилотного доступа"),
        ("cancel", "Сбросить новую заявку"),
        ("result", "Мои готовые результаты"),
        ("delete", "Удалить локальные фото"),
        ("terms", "Условия тестирования"),
        ("privacy", "Обработка фотографий"),
        ("support", "Поддержка"),
        ("buy", "DEMO покупки доступа"),
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
    reusable: list[str] = field(default_factory=list)
    steps_token: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class TrackedSteps:
    user: int
    created: float = field(default_factory=time.time)
    messages: list[int] = field(default_factory=list)
    job: str | None = None
    delivered: bool = False
    cleaning: bool = False


class StepMessages:
    """Only IDs returned by step/progress sends; restart intentionally forgets them.

    Access expires scopes after 24 hours. Caps are 1,000 scopes and 100 IDs each;
    expired/evicted messages stay in Telegram, never get guessed or rediscovered.
    """

    def __init__(self, store):
        self.store = store
        self.scopes = {}
        self.jobs = {}

    def _prune(self):
        for token, scope in list(self.scopes.items()):
            if time.time() - scope.created > 86400:
                self._forget(token)

    def _forget(self, token):
        scope = self.scopes.pop(token)
        if scope.job:
            self.jobs.pop((scope.user, scope.job), None)

    def begin(self, user, token):
        self._prune()
        if len(self.scopes) >= 1000:
            self._forget(min(self.scopes, key=lambda key: self.scopes[key].created))
        self.scopes[token] = TrackedSteps(user)

    def bind(self, user, token, job):
        self._prune()
        scope = self.scopes.get(token)
        if scope and scope.user == user and scope.job is None:
            scope.job = job
            self.jobs[user, job] = token

    async def sent(self, bot, user, token, message):
        self._prune()
        scope = self.scopes.get(token)
        if (scope is None or scope.user != user or not isinstance(message, Message)
                or message.chat.type != "private" or message.chat.id != user
                or type(message.message_id) is not int or message.message_id <= 0):
            return
        if message.message_id not in scope.messages:
            scope.messages.append(message.message_id)
            del scope.messages[:-100]
        # The provider may finish while Telegram is still sending the progress message.
        if scope.delivered:
            await self._delete(bot, scope)

    async def delivered(self, bot, user, job):
        try:
            self._prune()
            record = self.store.job(job)
            if record["user_id"] != user or record["status"] != "delivered":
                return
            scope = self.scopes.get(self.jobs.get((user, job)))
            if scope:
                scope.delivered = True
                await self._delete(bot, scope)
        except Exception as exc:
            logging.getLogger(__name__).warning("step_cleanup_failed type=%s", type(exc).__name__)

    async def _delete(self, bot, scope):
        if scope.cleaning:
            return
        scope.cleaning = True
        try:
            while scope.messages:
                batch = scope.messages[:100]
                await bot.delete_messages(chat_id=scope.user, message_ids=batch)
                scope.messages[:] = [mid for mid in scope.messages if mid not in batch]
        except Exception as exc:
            logging.getLogger(__name__).warning("step_cleanup_failed type=%s", type(exc).__name__)
        finally:
            scope.cleaning = False


async def deliver_result(bot, store, steps, user, job, path, *, demo=False):
    if user == 0:  # Local browser demo has no Telegram destination.
        return
    filename, caption = result_presentation(store.job(job))
    if demo:
        caption = "DEMO: тестовая копия, ИИ-правка не выполнялась.\n\n" + caption
    await bot.send_document(
        user,
        FSInputFile(path, filename=filename),
        caption=caption,
        reply_markup=buttons([[("📸 Новая правка", "nav:edit"), ("🖼 Мои результаты", "nav:results")]]),
    )
    store.delivered(job)
    await steps.delivered(bot, user, job)


async def processing_activity(bot, store, interval=4):
    while True:
        try:
            users = {
                job["user_id"] for job in store.jobs()
                if type(job["user_id"]) is int and job["user_id"] > 0
                and job["status"] in {"queued", "running"}
            }
            for user in sorted(users):
                try:
                    await bot.send_chat_action(user, "upload_photo")
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "processing_activity_failed type=%s", type(exc).__name__
                    )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "processing_activity_scan_failed type=%s", type(exc).__name__
            )
        await asyncio.sleep(interval)


def build_dispatcher(settings, store, media, service, *, step_messages=None, purchase_demo=None):
    trial = settings.trial_access
    if trial != service.trial_access:
        raise ValueError("trial_access_service_mismatch")
    router = Router()
    router.message.filter(F.chat.type == "private")
    router.callback_query.filter(F.message.chat.type == "private")
    register_admin(router, store, settings.admin_user_id)
    dp = Dispatcher(events_isolation=SimpleEventIsolation())
    drafts = {}
    steps = step_messages if step_messages is not None else StepMessages(store)
    dp["step_messages"] = steps
    purchases = purchase_demo if purchase_demo is not None else PurchaseDemo()
    dp["purchase_demo"] = purchases

    async def step_answer(message, user, draft, text, *, reply_markup=BACK):
        sent = await message.answer(text, reply_markup=reply_markup)
        await steps.sent(message.bot, user, draft.steps_token, sent)

    def pricing(user=None):
        if service.is_unlimited(user):
            return "Для вашего аккаунта включено безлимитное тестирование."
        if trial:
            return "Любая функция, включая объединение — 1 генерация. Всего 1 бесплатная успешная генерация."
        return f"Правка — {service.cost('hair')} попытка. Объединение двух фото — {service.cost('merge')}."

    async def trial_balance(message, user):
        if not store.has_consent(user):
            await message.answer("Сначала подтвердите условия и согласие в /start.", reply_markup=CONSENT)
            return
        if service.is_unlimited(user):
            await message.answer("♾ Безлимитное тестирование\n\nКоличество генераций для вашего аккаунта не ограничено.",
                                 reply_markup=MAIN)
            return
        total, reserved = service.wallet(user)
        await message.answer(
            "Вам доступна 1 бесплатная успешная генерация OpenAI, включая объединение фото.\n"
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
        draft = drafts[user] = Draft(key)
        steps.begin(user, draft.steps_token)
        choice = PRESETS[key]
        draft.reusable = service.reusable_inputs(user, choice.inputs)
        markup = BACK
        if draft.reusable:
            plural = choice.inputs == 2
            markup = buttons([
                [("Использовать загруженные фото" if plural else "Использовать загруженное фото",
                  "reuse:" + draft.token, "success")],
                [("Загрузить новые фото" if plural else "Загрузить новое фото", "fresh:" + draft.token)],
                [("🏠 Главное меню", "nav:home")],
            ])
        await step_answer(
            message, user, draft,
            f"{ICONS[key]} Шаг 1 из 3 · {choice.label}\n\nЗагрузите {choice.inputs} фото.\n"
            "Для лучшего качества отправьте фото файлом. JPEG/PNG/WebP до 10 MB.\n"
            "Или сфотографируйте себя сейчас — желательно на нейтральном фоне, например у стены."
            + ("\n\n♾ Безлимитное тестирование." if service.is_unlimited(user) else
               "\n\nОтправка описания — 1 бесплатная генерация."
               if trial else "")
            + ("\n\nДля документов: анфас, глаза открыты, рот закрыт, вся голова в кадре. "
               "Исходное фото — на ровном белом фоне, в подходящей одежде; фон и одежда не меняются."
               if key == "document_original" else
               "\n\nИИ-фото на документы или пропуска. Для паспорта РФ не подходит."
               if key == "document" else ""),
            reply_markup=markup,
        )

    async def step_two(message, draft):
        if len(draft.photos) < PRESETS[draft.preset].inputs:
            await step_answer(message, message.chat.id, draft,
                              "📷 Шаг 1 из 3\n\nПервое фото принято. Отправьте второе с согласия человека.")
            return
        example = EXAMPLES[draft.preset]
        if draft.preset in {"document", "document_original"}:
            options = [("📄 Подготовить 4 фото без ИИ", "original")] if draft.preset == "document_original" else [
                ("Сохранить одежду", "keep"), ("Деловой костюм", "suit"), ("Светлая рубашка", "shirt"),
                ("Подготовить исходник без ИИ", "original"),
            ]
            await step_answer(
                message, message.chat.id, draft,
                "📄 Шаг 2 из 3 · 4 фото 35×45 мм\n\n"
                + ("Исходный снимок: только кадрирование и подготовка листа, без ИИ. "
                   "Фон и одежда сохранятся. Проверьте, что голова и плечи по центру кадра."
                   if draft.preset == "document_original" else
                   "Выберите одежду или напишите пожелание. Белый фон, лицо сохраняем. "
                   "ИИ-портрет не подходит для паспорта РФ.")
                + ("\n\n♾ Безлимитное тестирование · один лист с четырьмя копиями."
                   if service.is_unlimited(message.chat.id) else
                   f"\n\n{'Выбор варианта расходует' if trial else 'После выбора подтвердите'} "
                   "1 попытку · один лист с четырьмя копиями."),
                reply_markup=buttons([
                    [(label, f"docopt:{draft.token}:{option}")] for label, option in options
                ] + [[("🏠 Главное меню", "nav:home")]]),
            )
            return
        hair_hint = (
            "\n\n💇 Рекомендуем запрашивать от 1 до 12 причёсок на одном изображении: "
            "один образ или коллаж из 9/12 вариантов. Коллаж — 1 попытка.\n"
            "В коллаже портреты мельче; модель может отклониться от числа вариантов."
            if draft.preset == "hair" else ""
        )
        collage_buttons = [
            [InlineKeyboardButton(text=f"📋 Пример · {count} причёсок", copy_text=CopyTextButton(text=text))]
            for count, text in HAIR_EXAMPLES.items()
        ] if draft.preset == "hair" else []
        await step_answer(
            message, message.chat.id, draft,
            f"✍️ Шаг 2 из 3 · Опишите изменение\n\nНапример: «{example}»."
            + hair_hint
            + ("\n\n♾ Безлимитное тестирование." if service.is_unlimited(message.chat.id) else
               "\n\nОтправка описания — 1 бесплатная генерация." if trial else ""),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Скопировать пример", copy_text=CopyTextButton(text=example))],
                *collage_buttons,
                *BACK.inline_keyboard,
            ]),
        )

    def submission_error(code):
        return {
            "insufficient_credits": "Недостаточно попыток. Откройте «Мои попытки».",
            "already_active": "Ваш образ уже создаётся. Подождите немного.",
            "trial_exhausted": "Бесплатная генерация уже использована. Новые бесплатные попытки не выдаются.",
            "trial_not_granted": "Сначала подтвердите условия и согласие в /start.",
            "consent_required": "Сначала подтвердите условия и согласие в /start.",
            "invalid_inputs": "Фото больше недоступно. Выберите функцию и загрузите новое фото.",
            "request_mismatch": "Это сообщение уже обработано. Новый вариант отправьте новым сообщением.",
        }.get(code, "Не получилось начать создание. Попробуйте ещё раз чуть позже.")

    async def submit_draft(message, user, draft, key):
        try:
            job = service.submit(user, key, draft.preset, draft.photos, draft.description)
        except DomainError as exc:
            await message.answer(submission_error(str(exc)), reply_markup=BACK)
            return
        drafts.pop(user, None)
        steps.bind(user, draft.steps_token, job)
        await step_answer(
            message, user, draft,
            ("📄 Подготавливаем лист с четырьмя фото\n\nБез ИИ. Обычно несколько секунд.\n"
             if draft.preset == "document_original" else "✨ Создаём ваш образ\n\nОбычно до 2 минут.\n")
            + "Результат появится здесь и в «Мои результаты».",
            reply_markup=MAIN,
        )

    async def send_result(message, user, job):
        record = store.result(user, job)
        path = media.path(record["result"])
        if not path.is_file() or time.time() - path.stat().st_mtime > 86400:
            raise DomainError("expired_result")
        mode = (
            "DEMO: тестовая копия, ИИ-правка не выполнялась.\n" if settings.image_provider == "mock" else ""
        )
        filename, caption = result_presentation(record)
        await message.answer_document(
            FSInputFile(path, filename=filename),
            caption=mode + caption + "\n\nХранение — 24 часа.",
            reply_markup=buttons([[("📸 Новая правка", "nav:edit"), ("🖼 Результаты", "nav:results")]]),
        )
        store.delivered(job)
        await steps.delivered(message.bot, user, job)

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
                    "Что хотите изменить? " + pricing(user), reply_markup=MENU
                )
        elif action == "merge":
            await choose_preset(message, user, "merge")
        elif action == "balance":
            if service.is_unlimited(user):
                await trial_balance(message, user)
                return
            total, reserved = service.wallet(user)
            markup = (
                buttons([[("🎁 Получить 3 тестовые попытки", "demo:grant")]])
                if (settings.image_provider == "mock" and settings.demo_credits)
                else BACK
            )
            await message.answer(
                f"💎 Доступно попыток: {total - reserved}\nВ обработке: {reserved}\n"
                + pricing(user) + "\nПродажи пока закрыты.",
                reply_markup=markup,
            )
        elif action == "results":
            rows = []
            for j in sorted(store.jobs(user), key=lambda j: j["created"], reverse=True):
                if j["status"] not in {"generated", "delivered"} or not j["result"]:
                    continue
                path = media.path(j["result"])
                if path.is_file() and time.time() - path.stat().st_mtime <= 86400:
                    date = datetime.fromtimestamp(j["created"]).strftime("%d.%m · %H:%M")
                    rows.append([(f"{PRESETS[j['preset']].label} · {date}", "result:" + j["id"])])
                if len(rows) == 5:
                    break
            await message.answer(
                "🖼 Ваши последние результаты. Скачать ещё раз можно без расхода попыток.\nХранение — 24 часа."
                if rows
                else "Здесь появятся ваши результаты. Начните с кнопки «Изменить фото».\n"
                "Готовые файлы хранятся локально 24 часа.",
                reply_markup=buttons(rows) if rows else BACK,
            )
        elif action == "help":
            await message.answer(
                "Как пользоваться\n1. Выберите функцию.\n2. Отправьте своё фото (для объединения — два).\n"
                "3. Опишите желаемое изменение.\n"
                + ("4. Создание начнётся сразу после описания.\n" if trial else
                   "4. Проверьте цену и нажмите «Создать».\n")
                +
                "5. Получите файл и сохраните его.\n\n"
                + pricing(user) + " Новый вариант — отдельное задание.\n"
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
                "Нужна помощь? Напишите в поддержку и опишите, что произошло.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="💬 Написать в поддержку", url="https://t.me/nedelsky",
                                         style="success"),
                ]]),
            )
        elif action == "buy":
            await purchases.open(message, user)

    @router.message(F.text.in_(list(NAV.values())))
    async def navigation(message: Message):
        action = next(key for key, label in NAV.items() if label == message.text)
        await navigate(message, message.from_user.id, action)

    @router.callback_query(F.data.startswith("nav:"))
    async def navigation_callback(callback: CallbackQuery):
        action = callback.data.split(":", 1)[1]
        if action == "buy":
            await purchases.open_callback(callback)
            return
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
            else "\n♾ Для вашего аккаунта включено безлимитное тестирование."
            if service.is_unlimited(message.from_user.id)
            else "\nВсего 1 бесплатная успешная генерация OpenAI после согласия. Любая функция — 1 генерация."
            if trial else ""
        )
        await message.answer(
            "Образ · фотостудия\n\nПримерка причёсок, одежды, очков и объединение фото.\n\n"
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
                "♾ Для вашего аккаунта включено безлимитное тестирование."
                if service.is_unlimited(callback.from_user.id)
                else
                f"Всего 1 бесплатная успешная генерация OpenAI. Доступно: {total - reserved}. "
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
            "Условия пилота: сервис для совершеннолетних, права и согласие всех изображённых людей, "
            "полностью одетые образы. Не используйте результат для обмана. "
            + pricing(message.from_user.id) + " Новый вариант — отдельное задание. "
            "При ошибке обработки резерв возвращается. Качество и сходство не гарантированы. "
            "Оплаты в Telegram сейчас нет; условия продажи будут опубликованы перед запуском."
        )

    @router.message(Command("privacy"))
    async def privacy(message: Message):
        await message.answer(
            "Фото и описание хранятся локально до 24 часов после обработки; зависшие задачи "
            "хранятся до разбора поддержкой. В рабочем режиме фото передаются OpenAI. "
            "Для учёта постоянно храним Telegram ID, публичный username при наличии, даты посещений, "
            "количество готовых обработок и подтверждённые покупки. Статистика доступна только владельцу. "
            "Имена, телефоны и адреса в статистику не записываем. /delete удаляет локальные фото "
            "и сбрасывает согласие; это не удаляет сообщения Telegram и данные у провайдера."
        )

    @router.message(Command("support", "paysupport"))
    async def support(message: Message):
        await navigate(message, message.from_user.id, "support")

    @router.message(Command("buy"))
    async def buy(message: Message):
        await purchases.open(message, message.from_user.id)

    @router.callback_query(F.data.startswith(PURCHASE_PREFIX))
    async def purchase_callback(callback: CallbackQuery):
        await purchases.handle(callback)

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
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            await navigate(message, message.from_user.id, "results")
            return
        try:
            job = parts[1].strip()
            await send_result(message, message.from_user.id, job)
        except (DomainError, ValueError, IndexError):
            await message.answer("Результат недоступен. Откройте «Мои результаты».")

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

    @router.callback_query(F.data.startswith("reuse:") | F.data.startswith("fresh:"))
    async def source_choice(callback: CallbackQuery):
        user = callback.from_user.id
        action, token = callback.data.split(":", 1)
        draft = current(user)
        if not draft or draft.token != token or not store.has_consent(user):
            await callback.answer("Выбор фото устарел. Выберите функцию снова.", show_alert=True)
            return
        await callback.answer()
        if action == "reuse":
            inputs = service.reusable_inputs(user, PRESETS[draft.preset].inputs)
            if not inputs or inputs != draft.reusable:
                await callback.message.answer("Это фото уже недоступно. Загрузите новое фото.", reply_markup=BACK)
                return
            draft.photos = inputs
        else:
            draft.photos.clear()
        draft.photo_messages.clear()
        draft.description = ""
        draft.reusable.clear()
        draft.token = uuid.uuid4().hex
        if action == "reuse":
            await step_two(callback.message, draft)
        else:
            await step_answer(
                callback.message, user, draft,
                ("📷 Шаг 1 из 3\n\nЗагрузите два новых фото.\n" if PRESETS[draft.preset].inputs == 2
                 else "📷 Шаг 1 из 3\n\nЗагрузите новое фото.\n")
                +
                "Для лучшего качества отправьте фото файлом. JPEG/PNG/WebP до 10 MB.\n"
                "Или сфотографируйте себя сейчас — желательно на нейтральном фоне, например у стены.",
                reply_markup=BACK,
            )

    async def accept_photo(message, draft, source):
        choice = PRESETS[draft.preset]
        if source.message_id in draft.photo_messages:
            return
        if len(draft.photos) >= choice.inputs:
            await message.answer("Фото уже загружены. Напишите, какой образ хотите, или /cancel.")
            return
        attachment = source.photo[-1] if source.photo else source.document
        if attachment.file_size and attachment.file_size > MAX_BYTES:
            await message.answer("Максимум 10 MB на фото.")
            return
        buffer = io.BytesIO()
        await message.bot.download(attachment, destination=buffer)
        try:
            data = normalize(buffer.getvalue())
            path = media.save(message.from_user.id, data)
            service.remember_inputs(message.from_user.id, draft.photos + [path])
        except (ValueError, OSError, DomainError) as exc:
            error = (
                "Разрешение фото слишком большое. Максимум — 64 MP и 10 MB на файл. "
                "Уменьшите фото или выберите другое."
                if isinstance(exc, ValueError) and str(exc) == "image_resolution_limit" else
                "Не удалось принять фото. Нужен JPEG, PNG или WebP до 10 MB и 64 MP. "
                "Если это HEIC на iPhone, сохраните копию в JPEG."
            )
            await message.answer(error)
            return
        draft.photos.append(path)
        draft.photo_messages.add(source.message_id)
        await step_two(message, draft)

    @router.message(F.photo | F.document)
    async def photo(message: Message):
        draft = current(message.from_user.id)
        if not draft or not store.has_consent(message.from_user.id):
            await message.answer("Сначала /start и выберите функцию.")
            return
        await accept_photo(message, draft, message)

    @router.message(F.text)
    async def description(message: Message):
        if (message.text or "").startswith("/"):
            await message.answer("Неизвестная команда. Откройте /help или нижнюю панель.")
            return
        user = message.from_user.id
        if message.external_reply:
            await message.answer("Ответьте на своё фото в этом чате или загрузите новое фото.")
            return
        request_key = f"telegram-description:{message.bot.id}:{message.message_id}"
        if trial and any(job["request_key"] == request_key for job in store.jobs(user)):
            await message.answer(
                "Это сообщение уже обработано. Готовый образ можно скачать в «Мои результаты».",
                reply_markup=MAIN,
            )
            return
        draft = current(user)
        if not draft or not store.has_consent(user):
            await message.answer("Выберите функцию и загрузите фото через /start.")
            return
        source = message.reply_to_message
        if source and (source.photo or source.document):
            if (source.chat.type != "private" or source.chat.id != message.chat.id
                    or source.from_user is None or source.from_user.id != user or source.from_user.is_bot):
                await message.answer("Выберите своё фото из этого чата или загрузите новое фото.")
                return
            if len(draft.photos) == PRESETS[draft.preset].inputs:
                draft.photos.clear()
                draft.photo_messages.clear()
                draft.description = ""
                draft.token = uuid.uuid4().hex
            # Replying selects the attachment. It never spends a generation by itself.
            await accept_photo(message, draft, source)
            return
        if not draft or len(draft.photos) != PRESETS[draft.preset].inputs:
            await message.answer("Выберите функцию и загрузите фото через /start.")
            return
        selection = (message.text or "").strip().casefold().strip(" .!?,")
        if selection in {"возьми это фото", "возьми фото", "используй это фото", "используй фото",
                         "использовать это фото", "использовать фото", "это фото",
                         "возьми эту фотографию", "используй эту фотографию"}:
            await step_two(message, draft)
            return
        if not 1 <= len(message.text.strip()) <= 1500:
            await message.answer("Описание должно быть от 1 до 1500 символов.")
            return
        await describe_and_submit(message, user, draft, message.text.strip(), request_key)

    async def describe_and_submit(message, user, draft, description, request_key):
        draft.description = description
        if not trial:
            draft.token = uuid.uuid4().hex
        choice = PRESETS[draft.preset]
        cost = service.cost(draft.preset)
        unit = "бесплатная генерация" if trial else "попытка(и)"
        spend = "♾ Безлимитное тестирование · один результат." if service.is_unlimited(user) else f"{cost} {unit} · один результат."
        await step_answer(
            message, user, draft,
            f"✨ Шаг 3 из 3 · {choice.label}\n\n{draft.description}\n\n"
            + spend,
            reply_markup=BACK if trial else buttons(
                [
                    [("Создать · безлимит" if service.is_unlimited(user) else f"Создать · {cost} {unit}", "confirm:" + draft.token, "success")],
                    [("🏠 Главное меню", "nav:home")],
                ]
            ),
        )
        if trial:
            await submit_draft(message, user, draft, request_key)

    @router.callback_query(F.data.startswith("docopt:"))
    async def document_option(callback: CallbackQuery):
        user = callback.from_user.id
        if not callback.message or callback.message.chat.id != user or not store.has_consent(user):
            await callback.answer("Сначала откройте /start в личном чате.", show_alert=True)
            return
        parts = callback.data.split(":")
        draft = current(user)
        if (len(parts) != 3 or draft is None or draft.token != parts[1]
                or len(draft.photos) != 1 or draft.preset not in {"document", "document_original"}
                or parts[2] not in DOCUMENT_OPTIONS
                or (draft.preset == "document_original" and parts[2] != "original")):
            await callback.answer("Выбор устарел. Откройте функцию заново.", show_alert=True)
            return
        await callback.answer()
        if parts[2] == "original":
            draft.preset = "document_original"
        await describe_and_submit(callback.message, user, draft, DOCUMENT_OPTIONS[parts[2]],
                                  "telegram-document:" + parts[1] + ":" + parts[2])

    @router.callback_query(F.data.startswith("confirm:"))
    async def confirm(callback: CallbackQuery):
        if trial:
            await callback.answer("Создание запускается вашим описанием. Напишите желаемое изменение.",
                                  show_alert=True)
            return
        user = callback.from_user.id
        token = callback.data.split(":", 1)[1]
        draft = current(user)
        if draft is None or draft.token != token or not draft.description:
            await callback.answer("Эта заявка уже подтверждена или устарела", show_alert=True)
            return
        await callback.answer()
        await submit_draft(callback.message, user, draft, token)

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
            steps = StepMessages(store)
            provider = (
                MockProvider()
                if settings.image_provider == "mock"
                else OpenAIProvider(settings.openai_key, settings.image_model, settings.quality)
            )

            async def deliver(user, job, path):
                await deliver_result(bot, store, steps, user, job, path,
                                     demo=settings.image_provider == "mock")

            async def notify(user, job):
                if user == 0:
                    return
                reason = store.job(job)["error"]
                explanations = {
                    "provider_quota": "Студия временно недоступна. Попробуйте немного позже.",
                    "provider_rate_limit": "Студии нужно немного времени. Попробуйте позже.",
                    "provider_authentication": "Студия временно недоступна. Попробуйте позже.",
                }
                await bot.send_message(
                    user,
                    explanations.get(reason, "Обработка не выполнена.")
                    + f" Резерв {'генерации' if settings.trial_access else 'кредитов'} возвращён.",
                )

            service = Service(store, media, provider, deliver, notify, trial_access=settings.trial_access,
                              unlimited_user_id=settings.admin_user_id if settings.owner_unlimited_testing else 0)
            dp = build_dispatcher(settings, store, media, service, step_messages=steps)
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
            tasks.extend([
                asyncio.create_task(service.worker()),
                asyncio.create_task(processing_activity(bot, store)),
                asyncio.create_task(watch_stop()),
            ])
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
