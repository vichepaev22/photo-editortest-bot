"""Owner-only test preference; each queued job freezes its selected quality."""

from .store import DomainError

QUALITIES = {"medium": "Medium", "high": "High", "xhigh": "Xhigh", "max": "Max"}


class OwnerQuality:
    def __init__(self, store, owner):
        self.store, self.owner = store, owner
        with store.tx() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS owner_image_quality(
                actor INTEGER PRIMARY KEY,quality TEXT NOT NULL
                CHECK(quality IN ('medium','high','xhigh','max')))""")

    def current(self, user):
        if type(user) is not int or user != self.owner:
            return None
        with self.store.tx() as connection:
            row = connection.execute("SELECT quality FROM owner_image_quality WHERE actor=?", (user,)).fetchone()
        return row[0] if row else None

    def select(self, user, quality):
        if type(user) is not int or user != self.owner or quality not in QUALITIES:
            raise DomainError("owner_quality_denied")
        with self.store.tx() as connection:
            connection.execute("INSERT INTO owner_image_quality VALUES(?,?) "
                               "ON CONFLICT(actor) DO UPDATE SET quality=excluded.quality", (user, quality))


def register_owner_quality(router, service):
    from aiogram import F
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.filters import Command
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    def allowed(user):
        return service.is_unlimited(user) and service.owner_quality is not None

    def content(user):
        selected = service.owner_quality.current(user) or "medium"
        text = ("🧪 Тест качества · только для владельца\n\n"
                f"Выбрано: {QUALITIES[selected]} · 1024×1024\n"
                "Модель: GPT Image 2.5 Sunburst\n\n"
                "Режим применяется к вашим следующим заданиям. Уже созданные задания сохраняют свой режим. "
                "У пользователей остаётся Medium. Ваш безлимит сохраняется; запросы оплачиваются из API-баланса.")
        rows = [[InlineKeyboardButton(text=("✓ " if key == selected else "") + title,
                    callback_data="owner_quality:" + key, style="success" if key == selected else None)]
                for key, title in QUALITIES.items()]
        rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    @router.message(Command("quality"))
    async def quality_message(message):
        if not allowed(message.from_user.id):
            await message.answer("Этот тест доступен только владельцу.")
            return
        service.store.admin_session_clear(message.from_user.id)
        text, keyboard = content(message.from_user.id)
        await message.answer(text, reply_markup=keyboard)

    @router.callback_query(F.data.startswith("owner_quality:"))
    async def quality_callback(callback):
        if not allowed(callback.from_user.id) or callback.message.chat.id != callback.from_user.id:
            await callback.answer("Доступно только владельцу.", show_alert=True)
            return
        quality = callback.data.removeprefix("owner_quality:")
        if quality not in QUALITIES:
            await callback.answer("Режим недоступен.", show_alert=True)
            return
        service.owner_quality.select(callback.from_user.id, quality)
        text, keyboard = content(callback.from_user.id)
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise
        await callback.answer("Для следующих заданий: " + QUALITIES[quality])
