"""Живой текст Telegram. Источник чанков независим от SDK конкретной LLM."""

import asyncio
import contextlib
import logging
import re
import time
import uuid

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter

log = logging.getLogger(__name__)


async def animated_welcome(message, text, keyboard, *, pause=asyncio.sleep):
    """Штатная анимация Telegram: временный draft, затем одно полное сообщение.

    Это раскрытие готового приветствия, без вызова LLM. Клавиатура прикрепляется
    к постоянному сообщению; кнопка остановки drafts явно выключена.
    """
    draft_id = (uuid.uuid4().int & 0x7FFFFFFF) or 1
    # Используем только typing; входящее сообщение пользователя не редактируется.
    activity = StreamingAnswer(message.bot, message.chat.id, message.message_id)
    async with activity.typing():
        ends = sorted({min(26, len(text)), len(text) // 3, 2 * len(text) // 3, len(text)})
        for end in ends:
            if not end:
                continue
            try:
                await message.bot.send_message_draft(
                    chat_id=message.chat.id, draft_id=draft_id, text=text[:end],
                    parse_mode=None, can_stop=False, keep_on_stop=False,
                )
            except TelegramRetryAfter as exc:
                await pause(max(1.5, exc.retry_after))
                break  # Не затягиваем приветствие повторными кадрами.
            except (TelegramBadRequest, TelegramNetworkError) as exc:
                log.warning("welcome_draft_unavailable type=%s", type(exc).__name__)
                break
            await pause(0.35 if end == len(text) else 1)
        # Draft исчезает; только sendMessage сохраняет приветствие и MAIN в чате.
        for attempt in range(2):
            try:
                return await message.answer(text, reply_markup=keyboard, parse_mode=None)
            except TelegramRetryAfter as exc:
                if attempt:
                    raise
                await pause(max(1.5, exc.retry_after))


def clean_text(text):
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.rstrip() for line in text.splitlines())).strip()


def telegram_text(text, limit=4096):
    """Лимит Telegram измеряется UTF-16 единицами, включая emoji."""
    encoded = text.encode("utf-16-le")
    if len(encoded) <= limit * 2:
        return text
    note = "\n\n[Текст сокращён]"
    return encoded[: (limit - len(note)) * 2].decode("utf-16-le", errors="ignore").rstrip() + note


class StreamingAnswer:
    def __init__(self, bot, chat, message_id, *, interval=1.2, typing_interval=4):
        self.bot, self.chat, self.message_id = bot, chat, message_id
        self.interval, self.typing_interval = interval, typing_interval
        self.last_edit = 0.0
        self.last_text = None

    async def _typing_loop(self):
        while True:
            delay = self.typing_interval
            try:
                await self.bot.send_chat_action(self.chat, "typing")
            except TelegramRetryAfter as exc:
                delay = max(delay, exc.retry_after, 1.5)
            except Exception as exc:
                # Не выводим текст исключения: он может содержать приватный запрос.
                log.warning("live_typing_failed type=%s", type(exc).__name__)
            await asyncio.sleep(delay)

    async def edit(self, text, *, reply_markup=None):
        text = telegram_text(clean_text(text))
        if text == self.last_text:
            return
        await asyncio.sleep(max(0, self.interval - (time.monotonic() - self.last_edit)))
        for attempt in range(3):
            try:
                await self.bot.edit_message_text(
                    chat_id=self.chat, message_id=self.message_id, text=text,
                    parse_mode=None, reply_markup=reply_markup,
                )
            except TelegramRetryAfter as exc:
                if attempt == 2:
                    raise
                await asyncio.sleep(max(1.5, exc.retry_after))
            except TelegramBadRequest as exc:
                if "message is not modified" not in exc.message:
                    raise
                self.last_text, self.last_edit = text, time.monotonic()
                return
            else:
                self.last_text = text
                self.last_edit = time.monotonic()
                return
        self.last_text, self.last_edit = text, time.monotonic()

    @contextlib.asynccontextmanager
    async def typing(self):
        task = asyncio.create_task(self._typing_loop())
        try:
            yield self
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def stream(self, chunks, *, summary="", reply_markup=None):
        """chunks — реальный AsyncIterable[str], summary — публичный план, не мысли.

        Короткий готовый ответ следует отправлять обычным answer(). Чанки копятся
        до 300 символов; даже при быстром источнике редактирование ограничено.
        Отмена вызывающей задачи прекращает источник, typing и финальную отправку.
        """
        answer, shown = "", 0
        prefix = clean_text(summary) + "\n\n" if summary else ""
        try:
            async with self.typing():
                async for chunk in chunks:
                    answer += chunk
                    if len(answer) - shown >= 300:
                        shown = min(len(answer), shown + 600)
                        await self.edit(prefix + answer[:shown] + " …", reply_markup=reply_markup)
                final = clean_text(answer)
                if final:
                    await self.edit(prefix + final, reply_markup=reply_markup)
                return final
        finally:
            close = getattr(chunks, "aclose", None)
            if close:
                await close()
