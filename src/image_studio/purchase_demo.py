"""One-message payment demo. No payment provider, balance or database access."""

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, replace

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

PLANS = {"express": ("Express", 180, 4), "base": ("Базовый", 380, 10), "premium": ("Premium", 790, 25)}
METHODS = {"sbp": "СБП", "crypto": "Крипта"}
TEST_URL = "https://example.com/?payment-demo=1"
PRIVACY_URL = "https://telegra.ph/Politika-konfidencialnosti-08-01-83"
TERMS_URL = "https://telegra.ph/Polzovatelskoe-soglashenie-08-01-39"
TTL_SECONDS = 3600
MAX_SESSIONS = 1000
PREFIX = "purchase:"
_NOT_MODIFIED = {
    "message is not modified",
    "message is not modified: specified new message content and reply markup are exactly "
    "the same as a current content and reply markup of the message",
}


@dataclass
class Checkout:
    user: int
    token: str
    created: float
    message_id: int | None = None
    stage: str = "plans"
    plan: str | None = None
    method: str | None = None
    revision: int = 0
    last_data: str | None = None


def render(checkout):
    def action(text, name):
        return InlineKeyboardButton(
            text=text, callback_data=f"{PREFIX}{checkout.token}:{checkout.revision}:{name}"
        )

    cancel = [action("❌ Отменить покупку", "cancel")]
    notice = (
        "Образ · Покупка доступа (демо)\n\n"
        "DEMO: платежи не подключены, деньги не списываются, доступ и генерации не начисляются.\n"
    )
    if checkout.stage == "cancelled":
        return (
            "Образ · Покупка доступа (демо)\n\n"
            "DEMO · Покупка отменена. Деньги не списаны, доступ и генерации не начислены.", None
        )
    if checkout.stage == "plans":
        text = (
            notice + "\nВыберите пакет генераций.\n"
            "Во всех пакетах — текущее качество студии. Любая правка, объединение фото "
            "или новый вариант — 1 генерация.\n"
            "Демонстрационная сессия действует 60 минут с открытия."
        )
        rows = [[action(f"{name} · {price} ₽ ({count} {'генерации' if count == 4 else 'генераций'})", "plan:" + key)]
                for key, (name, price, count) in PLANS.items()]
    else:
        name, price, count = PLANS[checkout.plan]
        text = notice + f"\nТариф: {name}\nСтоимость: {price} ₽\nГенераций: {count}\n"
        if checkout.stage == "methods":
            text += "Выберите способ для демонстрации."
            rows = [[action(name, "method:" + key) for key, name in METHODS.items()]]
        else:
            text += (
                f"Сервис: Platega · демо\nСпособ: {METHODS[checkout.method]}\n\n"
                "Тестовая ссылка открывает страницу-заглушку, без оплаты. "
                "60 минут — срок DEMO-сессии с открытия, а не срок действия ссылки.\n\n"
                "Оплачивая услугу после подключения платежей, вы соглашаетесь с "
                "Пользовательским соглашением и Политикой конфиденциальности по ссылкам ниже.\n\n"
                "Продажи генераций в Telegram будут доступны через Telegram Stars."
            )
            rows = [
                [InlineKeyboardButton(text="🔗 Тестовая ссылка", url=TEST_URL)],
                [InlineKeyboardButton(text="Политика конфиденциальности", url=PRIVACY_URL)],
                [InlineKeyboardButton(text="Пользовательское соглашение", url=TERMS_URL)],
            ]
        rows.append([action("⬅️ Назад", "back")])
    rows.append(cancel)
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


class PurchaseDemo:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.sessions: dict[int, Checkout] = {}
        # A send/edit and its state commit must be atomic, including repeated lower-button presses.
        self._lock = asyncio.Lock()

    def _prune(self):
        now = self.clock()
        for user, session in list(self.sessions.items()):
            if now - session.created >= TTL_SECONDS:
                self.sessions.pop(user)

    @staticmethod
    def _private(message, user):
        return (
            isinstance(message, Message) and message.chat.type == "private" and message.chat.id == user
        )

    @staticmethod
    async def _answer(callback, text=None, *, alert=False):
        try:
            await callback.answer(text, show_alert=alert)
        except Exception as exc:
            logging.getLogger(__name__).warning("purchase_callback_answer_failed type=%s", type(exc).__name__)

    @staticmethod
    async def _edit(bot, checkout):
        text, markup = render(checkout)
        try:
            await bot.edit_message_text(
                chat_id=checkout.user, message_id=checkout.message_id, text=text, reply_markup=markup
            )
        except TelegramBadRequest as exc:
            if exc.message.removeprefix("Bad Request: ").casefold() not in _NOT_MODIFIED:
                raise

    async def open(self, message, user):
        if not self._private(message, user):
            return False
        async with self._lock:
            self._prune()
            session = self.sessions.get(user)
            try:
                if session and session.stage != "cancelled":
                    await self._edit(message.bot, session)
                    return True
                session = Checkout(user=user, token=secrets.token_hex(12), created=self.clock())
                text, markup = render(session)
                sent = await message.answer(text, reply_markup=markup)
                if (not self._private(sent, user) or type(sent.message_id) is not int
                        or sent.message_id <= 0):
                    return False
                session.message_id = sent.message_id
                if user not in self.sessions and len(self.sessions) >= MAX_SESSIONS:
                    oldest = min(self.sessions, key=lambda key: self.sessions[key].created)
                    self.sessions.pop(oldest)
                self.sessions[user] = session
                return True
            except Exception as exc:
                # Never create a new checkout message as a fallback for an edit failure.
                logging.getLogger(__name__).warning("purchase_open_failed type=%s", type(exc).__name__)
                return False

    async def open_callback(self, callback: CallbackQuery):
        ok = await self.open(callback.message, callback.from_user.id)
        await self._answer(
            callback, None if ok else "Не удалось открыть DEMO. Нажмите кнопку покупки в своём чате.",
            alert=not ok,
        )

    async def handle(self, callback: CallbackQuery):
        async with self._lock:
            self._prune()
            user = callback.from_user.id
            session = self.sessions.get(user)
            parts = (callback.data or "").split(":")
            if (not self._private(callback.message, user) or session is None
                    or session.stage == "cancelled" or callback.message.message_id != session.message_id
                    or len(parts) not in {4, 5} or parts[0] != "purchase" or parts[1] != session.token):
                await self._answer(callback, "Эта DEMO-сессия недоступна или завершена.", alert=True)
                return
            if callback.data == session.last_data:
                await self._answer(callback, "Уже обработано.")
                return
            if parts[2] != str(session.revision):
                await self._answer(callback, "Этот шаг устарел. Используйте текущие кнопки DEMO.", alert=True)
                return
            action, value = parts[3], parts[4] if len(parts) == 5 else None
            candidate = replace(session, revision=session.revision + 1, last_data=callback.data)
            if action == "plan" and value in PLANS and session.stage == "plans":
                candidate.plan, candidate.stage = value, "methods"
            elif action == "method" and value in METHODS and session.stage == "methods":
                candidate.method, candidate.stage = value, "link"
            elif action == "cancel" and value is None:
                candidate.stage = "cancelled"
            elif action == "back" and value is None and session.stage in {"methods", "link"}:
                candidate.method = None
                if session.stage == "methods":
                    candidate.plan, candidate.stage = None, "plans"
                else:
                    candidate.stage = "methods"
            else:
                await self._answer(callback, "Недопустимый шаг DEMO. Используйте текущие кнопки.", alert=True)
                return
            try:
                await self._edit(callback.bot, candidate)
            except Exception as exc:
                logging.getLogger(__name__).warning("purchase_edit_failed type=%s", type(exc).__name__)
                await self._answer(callback, "Не удалось обновить DEMO. Попробуйте ещё раз.", alert=True)
                return
            self.sessions[user] = candidate
            await self._answer(callback)
