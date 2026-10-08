import re
from html import escape

from aiogram import BaseMiddleware, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .store import Store, validated_username

_PAGE_CALLBACK = re.compile(r"admin:(?:page|refresh):(0|[1-9][0-9]{0,8})", re.ASCII)
_DENIED = "Доступ запрещён."


def _private_human(message, user):
    return (
        isinstance(message, Message)
        and user is not None
        and not user.is_bot
        and 0 < user.id < 2**52
        and message.chat.type == "private"
        and message.chat.id == user.id
    )


class PrivateVisitMiddleware(BaseMiddleware):
    def __init__(self, store: Store):
        self.store = store

    async def __call__(self, handler, event, data):
        message = event.message if isinstance(event, CallbackQuery) else event
        user = event.from_user
        if _private_human(message, user):
            self.store.record_visit(user.id, user.username)
        return await handler(event, data)


def _render(stats):
    lines = [
        "<b>Статистика бота</b>",
        f"Пользователей: {stats['total_users']} · Покупали: {stats['paying_users']}",
        f"Успешных генераций: {stats['generated_count']}",
        "",
    ]
    for user in stats["users"]:
        user_id = user["id"]
        username = validated_username(user["username"])
        if username:
            account = (
                f'<a href="https://t.me/{escape(username, quote=True)}">@{escape(username)}</a>'
                f" (<code>{user_id}</code>)"
            )
        else:
            account = f'<a href="tg://user?id={user_id}">{user_id}</a>'
        lines.append(f"{account} · {user['generated_count']} ген. · {user['purchase_count']} покуп.")
    if not stats["users"]:
        lines.append("Пользователей пока нет.")
    lines.extend(["", f"Страница {stats['page'] + 1}/{stats['pages']}"])
    lines.append("Ссылки по ID зависят от настроек Telegram.")
    page = stats["page"]
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(text="←", callback_data=f"admin:page:{page - 1}"))
    if page + 1 < stats["pages"]:
        navigation.append(InlineKeyboardButton(text="→", callback_data=f"admin:page:{page + 1}"))
    keyboard = [navigation] if navigation else []
    keyboard.append([InlineKeyboardButton(text="Обновить", callback_data=f"admin:refresh:{page}")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)


def register_admin(router: Router, store: Store, admin_user_id: int):
    visits = PrivateVisitMiddleware(store)
    router.message.outer_middleware(visits)
    router.callback_query.outer_middleware(visits)

    def owner(message, user):
        return (
            type(admin_user_id) is int
            and 0 < admin_user_id < 2**52
            and _private_human(message, user)
            and user.id == admin_user_id
        )

    @router.message(Command("admin"))
    async def admin(message: Message):
        if not owner(message, message.from_user):
            await message.answer(_DENIED)
            return
        text, keyboard = _render(store.admin_stats())
        await message.answer(
            text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True
        )

    @router.callback_query(F.data.startswith("admin:"))
    async def admin_page(callback: CallbackQuery):
        if not owner(callback.message, callback.from_user):
            await callback.answer(_DENIED, show_alert=True)
            return
        match = _PAGE_CALLBACK.fullmatch(callback.data or "")
        if match is None:
            await callback.answer("Страница недоступна.", show_alert=True)
            return
        page = int(match.group(1))
        stats = store.admin_stats(page=page)
        if stats["page"] != page:
            await callback.answer("Страница недоступна.", show_alert=True)
            return
        text, keyboard = _render(stats)
        try:
            await callback.message.edit_text(
                text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise
        await callback.answer()
