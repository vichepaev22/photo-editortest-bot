"""Private-chat checkout in one durable Telegram message."""

import asyncio
import contextlib
import time
from datetime import datetime, timedelta, timezone

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from .platega import PlategaError
from .purchase_demo import METHODS, _legal_footer
from .store import DomainError


class PlategaCheckout:
    def __init__(self, store, gateway):
        self.store, self.gateway = store, gateway
        self.locks = [asyncio.Lock() for _ in range(64)]

    @staticmethod
    def private(message, user):
        return isinstance(message, Message) and message.chat.type == "private" and message.chat.id == user

    @staticmethod
    async def answer(callback, text=None):
        with contextlib.suppress(Exception):
            await callback.answer(text, show_alert=bool(text))

    def render(self, s):
        def button(text, action, style=None):
            return InlineKeyboardButton(
                text=text, callback_data=f"purchase:{s['token']}:{s['revision']}:{action}", style=style
            )

        text = "Образ · Покупка генераций\n\n"
        rows = []
        plans = self.store.platega_plans(s["user_id"])
        order = self.store.platega_get(s["order_id"]) if s["order_id"] else None
        if order and order["state"] == "paid":
            text += f"✅ Оплата подтверждена\nНачислено генераций: {order['credits']}.\nСпасибо за покупку!"
            return text, InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💎 Мои попытки", callback_data="nav:balance")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
                ]
            )
        if s["stage"] == "cancelled":
            return (
                text
                + "Экран покупки закрыт. Если вы уже оплатили, проверка и начисление продолжатся автоматически.",
                None,
            )
        if s["stage"] == "plans":
            text += "Выберите пакет. Любая правка или новый вариант — одна генерация."
            rows = [
                [
                    button(
                        f"{name} · {price} ₽ ({count} {'генерация' if count == 1 else 'генераций'})",
                        "plan:" + key,
                    )
                ]
                for key, (name, price, count) in plans.items()
            ]
        else:
            name, price, count = plans[s["pack"]]
            text += f"Пакет: {name}\nСтоимость: {price} ₽\nГенераций: {count}\n\n"
            if s["stage"] == "methods":
                text += "Выберите способ оплаты через Platega."
                rows = [
                    [button("СБП", "method:sbp", "success"), button("Крипта", "method:crypto", "primary")]
                ]
            else:
                text += f"Способ: {METHODS[s['method']]} · Platega\n\n"
                if (
                    order["url"]
                    and (order["expires"] is None or order["expires"] > time.time())
                    and order["state"] == "pending"
                ):
                    text += "Перейдите к оплате. Генерации начислятся после подтверждения платежа."
                    if order["expires"]:
                        text += (
                            "\nСсылка действует до "
                            + datetime.fromtimestamp(order["expires"], timezone(timedelta(hours=5))).strftime(
                                "%d.%m %H:%M (МСК+2)"
                            )
                            + "."
                        )
                    else:
                        text += "\nСрок действия указан на странице оплаты Platega."
                    rows.append(
                        [InlineKeyboardButton(text="🔗 Перейти к оплате", url=order["url"], style="success")]
                    )
                elif order["state"] == "canceled":
                    text += "Платёж отменён. Вы можете выбрать способ оплаты снова."
                elif order["expires"] and order["expires"] <= time.time():
                    text += "Срок ссылки истёк. Если вы оплатили, проверьте статус. Для новой покупки выберите способ заново."
                else:
                    text += "Ссылка пока недоступна. Проверьте статус или обратитесь в поддержку. Если уже оплатили, не повторяйте перевод."
                rows.append([button("✅ Проверить оплату", "check")])
                rows.append([InlineKeyboardButton(text="Поддержка", url="https://t.me/nedelsky")])
            rows.append([button("⬅️ Назад", "back")])
            text += "\n\nОплачивая услугу, вы соглашаетесь с Пользовательским соглашением и Политикой конфиденциальности."
        rows.append([button("❌ Отменить покупку", "cancel")])
        if s["stage"] != "plans":
            rows.extend(_legal_footer())
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    async def edit(self, bot, s):
        text, markup = self.render(s)
        try:
            await bot.edit_message_text(
                chat_id=s["user_id"], message_id=s["message_id"], text=text, reply_markup=markup
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in error.message.casefold():
                raise

    async def open(self, message, user):
        if not self.private(message, user):
            return False
        if not self.store.has_consent(user):
            await message.answer("Перед покупкой подтвердите условия и согласие в /start.")
            return False
        async with self.locks[user % len(self.locks)]:
            s = self.store.platega_session(user)
            if s["stage"] == "cancelled" or (
                s["order_id"] and self.store.platega_get(s["order_id"])["state"] == "paid"
            ):
                s = self.store.platega_session(user, reset=True)
            if s["message_id"]:
                try:
                    await self.edit(message.bot, s)
                    return True
                except TelegramBadRequest as error:
                    if "message to edit not found" not in error.message.casefold():
                        raise
            text, markup = self.render(s)
            sent = await message.answer(text, reply_markup=markup)
            s["message_id"] = sent.message_id
            self.store.platega_save_session(s)
            return True

    async def open_callback(self, callback):
        await self.open(callback.message, callback.from_user.id)
        await self.answer(callback)

    async def handle(self, callback):
        user = callback.from_user.id
        if not self.private(callback.message, user):
            return
        async with self.locks[user % len(self.locks)]:
            s = self.store.platega_session(user)
            parts = (callback.data or "").split(":")
            if (
                len(parts) not in {4, 5}
                or parts[0] != "purchase"
                or parts[1] != s["token"]
                or parts[2] != str(s["revision"])
                or callback.message.message_id != s["message_id"]
                or s["stage"] == "cancelled"
                or not self.store.has_consent(user)
            ):
                await self.answer(callback, "Этот шаг устарел. Откройте покупку заново.")
                return
            action, value = parts[3], parts[4] if len(parts) == 5 else None
            create = check = False
            if action == "plan" and s["stage"] == "plans" and value in self.store.platega_plans(user):
                s.update(pack=value, stage="methods")
            elif action == "method" and s["stage"] == "methods" and value in METHODS:
                try:
                    order = self.store.platega_order(user, s["pack"], value)
                except DomainError:
                    await self.answer(callback, "Покупка пока недоступна. Обратитесь в поддержку.")
                    return
                s.update(method=value, stage="link", order_id=order["id"])
                create = True
            elif action == "check" and s["order_id"]:
                check = True
            elif action == "back" and s["stage"] in {"methods", "link"}:
                s.update(stage="plans" if s["stage"] == "methods" else "methods", order_id=None)
            elif action == "cancel":
                s["stage"] = "cancelled"
            else:
                await self.answer(callback, "Откройте текущий экран покупки.")
                return
            s["revision"] += 1
            self.store.platega_save_session(s)
            await self.answer(callback)
            try:
                if create:
                    await self.gateway.create(s["order_id"])
                elif check:
                    p = self.store.platega_get(s["order_id"])
                    if p["transaction_id"]:
                        await self.gateway.reconcile(p["transaction_id"], p["id"])
            except (PlategaError, DomainError):
                pass
            await self.edit(callback.bot, s)

    async def notify(self, bot, order):
        s = self.store.platega_session(order["user_id"])
        if s["message_id"] and s["order_id"] == order["id"] and s["stage"] != "cancelled":
            try:
                await self.edit(bot, s)
                return
            except TelegramBadRequest as error:
                if "message to edit not found" not in error.message.casefold():
                    raise
        await bot.send_message(
            order["user_id"],
            f"✅ Оплата подтверждена. Начислено генераций: {order['credits']}.\n"
            "Откройте «Мои попытки» или выберите новую правку.",
        )
