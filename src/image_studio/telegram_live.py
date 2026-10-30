"""Прогресс обработки фото только в личном диалоге Telegram."""

import asyncio
import logging
from dataclasses import dataclass

from .catalog import PRESETS
from .streaming_answer import StreamingAnswer, clean_text
from .telegram_processing import ProcessingStatus

log = logging.getLogger(__name__)
STAGES = {
    "queued": "Ожидаем свободное место в студии",
    "preparing": "Подготавливаем исходное фото",
    "reference": "Изучаем образ на фото-примере",
    "generating": "Создаём изображение по вашему описанию",
    "formatting": "Подготавливаем файл результата",
}


def progress_text(preset, description, stage="queued", frame=0):
    # План основан на явном запросе, не имитирует внутреннее мышление модели.
    title = "📄 Подготавливаем лист с четырьмя фото" if preset == "document_original" else "✨ Создаём ваш образ"
    request = clean_text(description.split("\n\nИтоговый выбор:", 1)[0])
    request = " ".join(request.split())
    if len(request) > 220:
        request = request[:217] + "…"
    timing = "Без ИИ. Обычно несколько секунд." if preset == "document_original" else "Обычно до 2 минут."
    return (f"{title}\n\n🎯 {PRESETS[preset].label}\n→ {request}\n"
            f"📎 Результат — файл в хорошем качестве.\n\n"
            f"{'◦' * (frame % 3 + 1)} {STAGES[stage]}…\n{timing}\n\n"
            "Готовое фото появится здесь и в «Мои результаты».")


@dataclass
class LiveReply:
    bot: object
    user: int
    message_id: int
    preset: str
    description: str
    stage: str = "queued"
    task: asyncio.Task | None = None
    answer: StreamingAnswer | None = None
    processing: ProcessingStatus | None = None
    reply_parameters: object = None
    ui_muted: bool = False


class TelegramLive:
    def __init__(self, store):
        self.store = store
        self.replies = {}

    def attach(self, bot, user, job, message_id, preset, description, *, reply_parameters=None):
        record = self.store.job(job)
        if record["status"] in {"queued", "running"} and not record["telegram_silent"]:
            self.replies[job] = LiveReply(bot, user, message_id, preset, description,
                                          reply_parameters=reply_parameters)

    def stage(self, user, job, stage):
        reply = self.replies.get(job)
        if reply and reply.user == user and stage in STAGES:
            reply.stage = stage
            if reply.processing:
                reply.processing.step(STAGES[stage])

    async def interrupt(self, user):
        # Новый запрос прекращает только старый UI, платная Image-задача продолжается.
        for reply in list(self.replies.values()):
            if reply.user == user:
                reply.ui_muted = True
                if reply.task:
                    reply.task.cancel()
                    await asyncio.gather(reply.task, return_exceptions=True)

    async def detach(self, job, *, text=None):
        reply = self.replies.pop(job, None)
        if reply and reply.task:
            reply.task.cancel()
            await asyncio.gather(reply.task, return_exceptions=True)
        if reply and text:
            try:
                # Новое постоянное сообщение завершает временный native draft.
                await reply.bot.send_message(reply.user, text, parse_mode=None,
                                             reply_parameters=reply.reply_parameters)
            except Exception as exc:
                log.warning("live_final_failed type=%s", type(exc).__name__)
        return reply

    async def _run(self, job, reply):
        answer = StreamingAnswer(reply.bot, reply.user, reply.message_id)
        reply.answer = answer
        try:
            def valid():
                record = self.store.job(job)
                return (not reply.ui_muted and not record["telegram_silent"]
                        and record["status"] in {"queued", "running"})

            async with ProcessingStatus(reply.bot, reply.user, STAGES[reply.stage],
                                        valid=valid, fallback=answer, grace=0) as processing:
                reply.processing = processing
                while True:
                    if not valid():
                        return
                    await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Анимация не влияет на платный запрос, учёт и доставку результата.
            log.warning("live_response_failed type=%s", type(exc).__name__)

    async def worker(self):
        try:
            while True:
                for job, reply in list(self.replies.items()):
                    if reply.task is None and not reply.ui_muted:
                        reply.task = asyncio.create_task(self._run(job, reply))
                await asyncio.sleep(0.2)
        finally:
            await self.close()

    async def close(self):
        for job in list(self.replies):
            await self.detach(job)
