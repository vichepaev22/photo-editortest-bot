"""Личный фотодиалог Telegram. Подтверждение и учёт остаются на сервере."""

import asyncio
import html
import io
import time

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputRichMessage, WebAppInfo

from .media import MAX_BYTES, normalize
from .photo_dialog import BACK, GROUPS, LABELS, PhotoDialog
from .store import DomainError
from .telegram_attention import EntryAttention
from .telegram_instructions import PANEL_TEXT
from .telegram_processing import ProcessingStatus, request_reply

ERRORS = {
    "consent_required": "Сначала подтвердите условия и согласие в /start.",
    "stale_dialog": "Этот шаг уже изменился. Пришли пожелание ещё раз или начни с кнопки ниже.",
    "invalid_inputs": "Фото больше недоступно. Загрузите новое фото до 10 MB.",
    "invalid_description": "Опишите пожелание сообщением до 1200 символов.",
    "trial_exhausted": "Сейчас нет доступных генераций. Остаток и пакеты — в «Мои попытки».",
    "insufficient_credits": "Недостаточно генераций. Откройте «Мои попытки» или купите пакет.",
    "already_active": "Ваш образ уже создаётся. Дождитесь результата.",
}


class TelegramDialog:
    def __init__(self, settings, store, media, service, live, steps):
        self.settings, self.store, self.media, self.service = settings, store, media, service
        self.core = PhotoDialog(store, media, service)
        self.live, self.steps = live, steps
        self.decode_lock = asyncio.Semaphore(1)
        self.attention = EntryAttention(self)

    def keyboard(self, card, *, highlight=None):
        rows = [
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=data,
                    style="success" if data == highlight or data.startswith("dlg:confirm:") else None,
                )
                for text, data in row
            ]
            for row in card.rows
        ]
        if card.kind in {"entry", "more"}:
            if self.settings.mini_app_url:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text="🎨 Открыть студию", web_app=WebAppInfo(url=self.settings.mini_app_url),
                            style="success" if highlight == "studio" else None
                        )
                    ]
                )
            if card.kind == "entry":
                rows.append([InlineKeyboardButton(text="📖 Инструкция", callback_data="guide:open",
                            style="success" if highlight == "guide:open" else None)])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def rich(self, card, *, highlight=None):
        # Only our own labels/URLs enter rich markup; free-form user text uses plain messages.
        def row(items):
            return (
                "<tg-button-row>"
                + "".join(
                    '<tg-button type="callback_data" data="'
                    + html.escape(data, quote=True)
                    + ('" style="success">' if data == highlight else '">')
                    + html.escape(text)
                    + "</tg-button>"
                    for text, data in items
                )
                + "</tg-button-row>"
            )

        if card.kind == "more":
            result = "<h2>Что попробуем? ✨</h2><p>Выбери кнопку или опиши пожелание словами.</p>"
            for title, keys in GROUPS:
                result += (
                    "<h3>" + html.escape(title) + "</h3>" + row([(LABELS[k], "dlg:pick:" + k) for k in keys])
                )
            result += row(BACK[0])
        else:
            result = (
                "<h2>Создадим твой образ? ✨</h2><p>"
                + html.escape(self.entry_text(card).split("\n\n", 1)[1]).replace("\n", "<br>")
                + "</p>"
            )
            for items in card.rows:
                result += row(items)
        if self.settings.mini_app_url:
            result += (
                '<tg-button-row><tg-button type="web_app" url="'
                + html.escape(self.settings.mini_app_url, quote=True)
                + ('" style="success">' if highlight == 'studio' else '">')
                + '🎨 Открыть студию</tg-button></tg-button-row>'
            )
        if card.kind == "entry":
            result += row([("📖 Инструкция", "guide:open")])
        return InputRichMessage(html=result)

    @staticmethod
    def entry_text(card):
        return card.text.replace("Ничего выбирать не обязательно — можно сразу написать сообщение.", PANEL_TEXT)

    def sent(self, message, card, *, rich, edit):
        if card.kind == "entry" and not edit:
            self.attention.start(message, card, rich=rich)
        return message

    async def show(self, message, user, card, *, edit=False):
        if card.kind in {"entry", "more"}:
            try:
                if edit:
                    return await message.bot.edit_message_text(
                        chat_id=user, message_id=message.message_id, rich_message=self.rich(card)
                    )
                sent = await message.bot.send_rich_message(
                    user, rich_message=self.rich(card), reply_parameters=request_reply(message, user))
                return self.sent(sent, card, rich=True, edit=edit)
            except TelegramBadRequest as exc:
                # Definite rejection only; a network timeout must never duplicate a send.
                if not any(
                    s in exc.message.lower() for s in ("method", "rich", "unsupported", "parse", "tag")
                ):
                    raise
        if edit:
            try:
                return await message.bot.edit_message_text(
                    chat_id=user,
                    message_id=message.message_id,
                    text=self.entry_text(card) if card.kind == "entry" else card.text,
                    reply_markup=self.keyboard(card),
                    parse_mode=None,
                )
            except TelegramBadRequest as exc:
                if "message is not modified" in exc.message.lower():
                    return message
                if not any(s in exc.message.lower() for s in ("not found", "can't be edited")):
                    raise
        sent = await message.bot.send_message(
            user, self.entry_text(card) if card.kind == "entry" else card.text,
            reply_markup=self.keyboard(card), parse_mode=None, reply_parameters=request_reply(message, user)
        )
        return self.sent(sent, card, rich=False, edit=edit)

    async def error(self, message, user, exc):
        from .photo_dialog import Card

        await self.show(
            message,
            user,
            Card(ERRORS.get(str(exc), "Не получилось продолжить. Пришли фото или пожелание ещё раз."), BACK),
        )

    async def receive(self, message):
        source = message if message.photo or message.document else message.reply_to_message
        phase = "Получаю фотографию" if source and (source.photo or source.document) else "Разбираю пожелание"
        async with ProcessingStatus(message.bot, message.from_user.id, phase,
                                    valid=lambda: self.store.has_consent(message.from_user.id)) as progress:
            return await self._receive(message, progress)

    async def _receive(self, message, progress):
        user = message.from_user.id
        if not self.store.has_consent(user):
            raise ValueError("consent_required")
        if self.core.seen(user, str(message.message_id)):
            return
        images = []
        source = message if message.photo or message.document else message.reply_to_message
        if message.external_reply:
            raise ValueError("invalid_inputs")
        if source and (source.photo or source.document):
            if (
                source.chat.id != user
                or not source.from_user
                or source.from_user.id != user
                or source.from_user.is_bot
            ):
                raise ValueError("invalid_inputs")
            attachment = source.photo[-1] if source.photo else source.document
            if attachment.file_size and attachment.file_size > MAX_BYTES:
                raise ValueError("invalid_inputs")
            buffer = io.BytesIO()
            progress.step("Получаю фотографию")
            await message.bot.download(attachment, destination=buffer)
            progress.step("Подготавливаю фотографию")
            async with self.decode_lock:
                clean = await asyncio.to_thread(normalize, buffer.getvalue())
            if not self.store.has_consent(user):
                raise ValueError("consent_required")
            images = [self.media.save(user, clean)]
        progress.step("Разбираю пожелание")
        card = await self.core.message(user, message.text or message.caption or "", images=images)
        session = self.core.current(user)
        if images and session:
            originals = session.photos[:1] if session.preset == "reference" else session.photos
            if originals:
                self.service.remember_inputs(user, originals)
        await progress.reveal(card.text)
        return await self.show(message, user, card)

    def register(self, router, drafts, admin, bonus, navigation):
        def eligible(message):
            user = message.from_user.id
            draft = drafts.get(user)
            if draft and time.time() - draft.created > 1800:
                drafts.pop(user, None)
            return (
                user not in drafts
                and not admin.active(message)
                and not (message.text or "").startswith("/")
                and message.text not in navigation
                and not (bonus.allowed(user) and self.store.bonus_session(user))
            )

        @router.callback_query(F.data.startswith("dlg:"))
        async def action(callback):
            user = callback.from_user.id
            if callback.message.chat.id != user:
                return await callback.answer()
            await callback.answer()
            drafts.pop(user, None)
            bonus.leave(user)
            try:
                card = await self.core.action(user, callback.data)
                # Track the old confirmation separately from a live plain-text status.
                if card.job:
                    record = self.store.job(card.job)
                    token = "dialog:" + card.job
                    self.steps.begin(user, token)
                    self.steps.bind(user, token, card.job)
                    await self.steps.sent(callback.bot, user, token, callback.message)
                    sent = await self.show(callback.message, user, card)
                    await self.steps.sent(callback.bot, user, token, sent)
                    self.live.attach(
                        callback.bot, user, card.job, sent.message_id, record["preset"], card.request,
                        reply_parameters=request_reply(callback.message, user)
                    )
                    return
                await self.show(callback.message, user, card, edit=True)
            except (ValueError, DomainError) as exc:
                await self.error(callback.message, user, exc)

        @router.message(F.photo | F.document | F.text, eligible)
        async def message_handler(message):
            try:
                await self.receive(message)
            except (ValueError, OSError, DomainError) as exc:
                await self.error(message, message.from_user.id, exc)
