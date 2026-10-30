"""Нативный временный статус Telegram: только реальные этапы, без мыслей модели."""

import asyncio
import logging
import time
import uuid
from html import escape

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InputRichMessage, ReplyParameters

from .streaming_answer import StreamingAnswer, telegram_text

log = logging.getLogger(__name__)


def request_reply(message, user):
    """Привязка только к сообщению этого пользователя в его личном чате."""
    for source in (message, getattr(message, "reply_to_message", None)):
        actor = getattr(source, "from_user", None)
        chat = getattr(source, "chat", None)
        identifier = getattr(source, "message_id", None)
        if (actor and actor.id == user and not actor.is_bot and chat and chat.id == user
                and chat.type == "private" and type(identifier) is int and identifier > 0):
            return ReplyParameters(message_id=identifier, allow_sending_without_reply=True)
    return None


class ProcessingStatus:
    def __init__(self, bot, user, phase, *, valid=lambda: True, fallback=None,
                 grace=0.5, interval=4, clock=time.monotonic):
        self.bot, self.user, self.valid, self.fallback = bot, user, valid, fallback
        self.grace, self.interval, self.clock = grace, interval, clock
        self.draft_id = (uuid.uuid4().int & 0x7fffffff) or 1
        self.started = self.phase_started = clock()
        self.phase, self.completed = phase, []
        self.mode, self.closed = "rich", False
        self.not_before = 0
        self.wake = asyncio.Event()
        self.task = self.activity = None

    def step(self, label):
        if self.closed or self.phase == label:
            return
        elapsed = max(0, int(self.clock() - self.phase_started))
        self.completed = [*self.completed, (self.phase, elapsed)][-3:]
        self.phase, self.phase_started = label, self.clock()
        self.wake.set()

    def text(self):
        elapsed = max(0, int(self.clock() - self.started))
        lines = [f"✨ Обрабатываю запрос · {elapsed} с", self.phase]
        lines.extend(f"✓ {label} · {seconds} с" for label, seconds in self.completed)
        return "\n".join(lines)

    async def publish(self):
        if self.closed or not self.valid():
            return self.interval
        if self.clock() < self.not_before:
            return self.not_before - self.clock()
        payload = dict(chat_id=self.user, draft_id=self.draft_id, can_stop=False, keep_on_stop=False)
        try:
            if self.mode == "rich":
                await self.bot.send_rich_message_draft(
                    **payload, rich_message=InputRichMessage(html="<tg-thinking>" + escape(self.text())
                                                           + "</tg-thinking>"))
            elif self.mode == "plain":
                await self.bot.send_message_draft(**payload, text=self.text(), parse_mode=None)
            elif self.mode == "legacy" and self.fallback:
                await self.fallback.edit(self.text())
        except TelegramRetryAfter as exc:
            delay = max(self.interval, exc.retry_after)
            self.not_before = self.clock() + delay
            return delay
        except TelegramBadRequest:
            # Обе draft-операции временные: отказ разрешает безопасный fallback.
            self.mode = {"rich": "plain", "plain": "legacy", "legacy": "typing"}.get(self.mode, "typing")
            return 1.2
        except Exception as exc:
            log.warning("processing_preview_failed type=%s", type(exc).__name__)
            return max(8, self.interval)
        return self.interval

    async def loop(self):
        await asyncio.sleep(self.grace)
        while not self.closed and self.valid():
            self.wake.clear()
            delay = await self.publish()
            # Даже частые смены этапа не вызывают поток API-запросов.
            await asyncio.sleep(1.2)
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=max(0.01, delay - 1.2))
            except TimeoutError:
                pass

    async def __aenter__(self):
        self.activity = StreamingAnswer(self.bot, self.user, 0).typing()
        await self.activity.__aenter__()
        self.task = asyncio.create_task(self.loop())
        return self

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.activity:
            await self.activity.__aexit__(None, None, None)

    async def __aexit__(self, *args):
        await self.close()

    async def reveal(self, text):
        """Два превью уже готового ответа; затем вызывающий код сохраняет ответ."""
        await self.close()
        if len(text) < 200 or not self.valid() or self.clock() < self.not_before:
            return
        text = telegram_text(text)
        for end in (len(text) // 2, len(text)):
            try:
                await self.bot.send_message_draft(
                    chat_id=self.user, draft_id=self.draft_id, text=text[:end],
                    parse_mode=None, can_stop=False, keep_on_stop=False)
            except TelegramRetryAfter as exc:
                await asyncio.sleep(max(1.5, exc.retry_after))
                return
            except Exception as exc:
                log.warning("dialog_preview_failed type=%s", type(exc).__name__)
                return
            await asyncio.sleep(0.2)
