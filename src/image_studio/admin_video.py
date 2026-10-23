"""Приватный Telegram-интерфейс тестового монтажа, отдельный от фотосценария."""

import asyncio
import io
import json
import logging
import time
from contextlib import suppress

from aiogram import BaseMiddleware, F
from aiogram.types import BufferedInputFile, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .video_montage import MAX_BYTES, OpenAIPlanner, VideoEngine, VideoError
from .video_store import VideoRepository

log = logging.getLogger(__name__)
GUIDE = (
    "🎬 Видеомонтаж · тест для владельца\n\n"
    "1. Нажмите «Новое видео» и отправьте MP4/MOV/WebM до 19 MB, 90 секунд и 4K. "
    "На iPhone можно отправить обычным видео — Telegram уменьшит размер.\n"
    "2. При желании прикрепите свою музыку (MP3/M4A/WAV до 5 MB). "
    "Отключите распознавание речи, если в ролике только музыка.\n"
    "3. Напишите пожелания, например:\n"
    "Собери ролик до 30 секунд, вертикальный 9:16. Оставь интересные моменты, "
    "сохрани речь, добавь плавные склейки и русские субтитры.\n"
    "4. «Составить план» запускает платный анализ GPT‑6.1 Sol и до 12 запросов "
    "gpt-transcribe при включённой речи. Затем нажмите «Собрать видео». "
    "Повторная сборка и скачивание не вызывают модель.\n\n"
    "Результат: MP4 до 60 секунд, 720p; полный кадр сохраняется с полями. "
    "Монтаж выбирает целые подготовленные фрагменты; субтитры фразовые с грубой синхронизацией, "
    "без подсветки отдельных слов. Музыку не генерируем, используем только вашу.\n\n"
    "Кадры и речь передаются OpenAI после нажатия запуска. Загружайте материалы с правами и согласием людей. "
    "На нашем сервере файлы и содержимое удаляются через 24 часа; технический журнал расходов сохраняется. "
    "Фотогенерации не расходуются. Каждый API-вызов оплачивается отдельно с OpenAI-баланса. "
    "Если расход не вернулся от провайдера, в отчёте будет «неизвестно», а не ноль."
)


def keyboard(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=data, style="primary" if data.startswith("video:analyze:") else None)
        for label, data in row] for row in rows if row])


BACK = [("← Видеомонтаж", "video:home")]
STATES = {"upload": "ждём видео и пожелания", "ready": "можно запускать анализ", "analyzing": "идёт анализ",
          "planned": "план готов", "rendering": "собираем MP4", "done": "готово", "failed": "анализ остановлен",
          "render_failed": "сборка остановлена", "review": "прервано — проверьте отчёт", "expired": "файлы удалены"}


class LimitedBuffer(io.BytesIO):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit

    def write(self, value):
        if self.tell() + len(value) > self.limit:
            raise VideoError("file_too_large")
        return super().write(value)


def usage_text(calls):
    lines = []
    for index, call in enumerate(calls, 1):
        usage = call["usage"]
        measured = type(usage.get("input_tokens")) is int and type(usage.get("output_tokens")) is int
        text = f"{usage['input_tokens']} вход / {usage['output_tokens']} выход" if measured else "токены: неизвестно"
        if measured:
            cached = (usage.get("input_tokens_details") or {}).get("cached_tokens")
            reasoning = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
            if type(cached) is int:
                text += f"; из входа кэш {cached}"
            if type(reasoning) is int:
                text += f"; из выхода reasoning {reasoning}"
        if call["seconds"]:
            text += f"; отправлено аудио {call['seconds']:.1f} с"
        lines.append(f"{index}. {call['model']} · {call['status']}\n{text}")
    return "\n\n".join(lines) if lines else "API-вызовов пока нет."


class AdminVideo:
    def __init__(self, settings, store, *, engine=None, planner_factory=None):
        self.settings, self.store = settings, store
        self.owner_id = settings.admin_user_id
        self.engine = engine or VideoEngine()
        self.planner_factory = planner_factory or OpenAIPlanner
        self.focus, self.tasks, self._repo = None, set(), None
        self.maintenance = None

    def owner(self, message, user):
        return bool(self.settings.admin_video_enabled and self.owner_id > 0 and user and not user.is_bot
                    and user.id == self.owner_id and isinstance(message, Message) and message.chat.type == "private"
                    and message.chat.id == user.id)

    @property
    def repo(self):
        if self._repo is None:
            self._repo = VideoRepository(self.settings.data_dir / "admin-video")
            self._repo.recover()
        return self._repo

    async def home(self, message):
        self.focus = None
        self.store.admin_session_clear(self.owner_id)
        self.repo.cleanup()
        await message.answer("🎬 Видеомонтаж · приватный тест\n\nGPT‑6.1 Sol + gpt-transcribe + FFmpeg. "
                             "Здесь можно проверить монтаж и посмотреть реальные расходы API.", parse_mode=None,
                             reply_markup=keyboard([("＋ Новое видео", "video:new")],
                                [("📖 Инструкция", "video:guide"), ("📊 Расходы и работы", "video:usage")],
                                [("← Админ-панель", "admin:home")]))

    async def panel(self, message, identifier):
        job = self.repo.get(self.owner_id, identifier)
        if not job:
            raise VideoError("not_found")
        data, state = job["data"], job["state"]
        lines, rows = ["🎬 Тестовый монтаж", "Состояние: " + STATES.get(state, state)], []
        if data.get("info"):
            lines.append(f"Исходник: {data['info']['duration']:.1f} с")
        if data.get("prompt"):
            lines.append("Пожелания: " + data["prompt"])
        if state in {"upload", "ready"}:
            rows.append([("Речь: " + ("вкл" if data.get("speech", True) else "выкл"), "video:speech:" + identifier)])
            if state == "ready":
                rows.append([("✨ Составить план · платный API", "video:analyze:" + identifier)])
            lines.append("Пришлите видео, затем пожелания. Дополнительно можно загрузить свою музыку.")
        if data.get("plan"):
            value = data["plan"]
            segments = data["segments"]
            lines += ["", "План: " + value["title"], f"Формат: {value['format']} · {value['transition']} · звук: {value['sound']}"]
            for index in value["clips"]:
                segment = segments[index]
                lines.append(f"• {segment['start']:.1f}–{segment['end']:.1f} с исходника")
            if state in {"planned", "done", "render_failed", "review"}:
                rows.append([("🎞 Собрать видео · без API", "video:render:" + identifier)])
        if state == "done" and (self.repo.directory(identifier) / "result.mp4").is_file():
            rows.append([("📥 Скачать MP4", "video:download:" + identifier)])
        if state in {"failed", "review"}:
            lines.append("Обработка остановлена. Автоматического повтора платных вызовов нет. "
                         "Изучите отчёт; новый анализ запускается только в новой работе.")
        lines += ["", usage_text(self.repo.calls(self.owner_id, identifier))]
        rows += [[("📄 Отчёт JSON", "video:report:" + identifier)], BACK]
        await message.answer("\n".join(lines)[:4000], parse_mode=None, reply_markup=keyboard(*rows))

    async def callback(self, callback):
        if not self.owner(callback.message, callback.from_user):
            await callback.answer("Доступ только владельцу.", show_alert=True)
            return
        await callback.answer()
        self.repo.cleanup()
        message = callback.message
        try:
            parts = (callback.data or "").split(":")
            if len(parts) == 2:
                action = parts[1]
                if action == "home":
                    return await self.home(message)
                if action == "guide":
                    return await message.answer(GUIDE, parse_mode=None, reply_markup=keyboard(BACK))
                if action == "usage":
                    self.focus = None
                    jobs = self.repo.recent(self.owner_id)
                    calls = [call for job in jobs for call in self.repo.calls(self.owner_id, job["id"])]
                    known = [c["usage"] for c in calls if type(c["usage"].get("input_tokens")) is int
                             and type(c["usage"].get("output_tokens")) is int]
                    rows = [[(f"{time.strftime('%d.%m %H:%M', time.gmtime(j['created'] + 5 * 3600))} · {STATES.get(j['state'], j['state'])}", "video:job:" + j["id"])] for j in jobs]
                    summary = (f"📊 Последние {len(jobs)} работ\nПодтверждённые токены: "
                               f"{sum(u['input_tokens'] for u in known)} вход / {sum(u['output_tokens'] for u in known)} выход.\n"
                               f"Вызовы без точного token usage: {len(calls) - len(known)}. "
                               f"Отправлено аудио: {sum(c['seconds'] for c in calls):.1f} с.\n\n"
                               "Время — Екатеринбург. Откройте работу для подробностей и JSON. Отсутствующий usage не считается нулём.")
                    return await message.answer(summary, parse_mode=None, reply_markup=keyboard(*rows, BACK))
                if action == "new":
                    if self.tasks:
                        raise VideoError("busy")
                    self.store.admin_session_clear(self.owner_id)
                    job = self.repo.create(self.owner_id)
                    self.focus = job["id"]
                    return await message.answer("Пришлите видео MP4/MOV/WebM до 19 MB и 90 секунд. "
                        "Затем напишите, как его смонтировать. Для запуска API потребуется отдельное нажатие.",
                        parse_mode=None, reply_markup=keyboard(BACK))
            if len(parts) != 3:
                raise VideoError("not_found")
            action, identifier = parts[1:]
            job = self.repo.get(self.owner_id, identifier)
            if not job or (job["state"] == "expired" and action not in {"job", "report"}):
                raise VideoError("expired")
            if action == "job":
                self.focus = identifier if job["state"] in {"upload", "ready"} else None
                return await self.panel(message, identifier)
            if action == "speech" and job["state"] in {"upload", "ready"}:
                self.repo.save(self.owner_id, identifier, speech=not job["data"].get("speech", True))
                self.focus = identifier
                return await self.panel(message, identifier)
            if action in {"analyze", "render"}:
                return await self.launch(message, job, action)
            if action == "download":
                return await self.deliver(message, identifier)
            if action == "report":
                return await self.report(message, identifier)
            raise VideoError("stale")
        except (VideoError, ValueError) as error:
            await self.error(message, str(error))

    async def accept(self, message):
        identifier = self.focus
        if not identifier or not self.owner(message, message.from_user):
            return
        job = self.repo.get(self.owner_id, identifier)
        if not job or job["state"] not in {"upload", "ready"}:
            return await self.error(message, "stale")
        try:
            directory = self.repo.directory(identifier)
            if message.text:
                text = message.text.strip()
                if not 3 <= len(text) <= 1500 or not job["data"].get("info"):
                    raise VideoError("prompt_required")
                self.repo.save(self.owner_id, identifier, state="ready", prompt=text)
            else:
                item = message.video or message.audio or message.document
                music = bool(message.audio or (message.document and (
                    (message.document.mime_type or "").startswith("audio/")
                    or (message.document.file_name or "").lower().endswith((".mp3", ".m4a", ".wav", ".flac", ".ogg")))))
                if not item:
                    raise VideoError("video_required")
                limit = 5_000_000 if music else MAX_BYTES
                if not item.file_size or not 0 < item.file_size <= limit:
                    raise VideoError("file_too_large")
                if music and not job["data"].get("info"):
                    raise VideoError("video_required")
                buffer = LimitedBuffer(limit)
                await message.bot.download(item, destination=buffer, timeout=90)
                incoming = directory / "incoming.bin"
                incoming.write_bytes(buffer.getvalue())
                try:
                    info = await self.engine.probe(incoming, music=music)
                except BaseException:
                    incoming.unlink(missing_ok=True)
                    raise
                path = directory / ("music.bin" if music else "source.mp4")
                incoming.replace(path)
                if music:
                    self.repo.save(self.owner_id, identifier, music=True)
                else:
                    self.repo.save(self.owner_id, identifier, state="upload", info=info, prompt="", speech=info["audio"])
                    if message.caption and 3 <= len(message.caption.strip()) <= 1500:
                        self.repo.save(self.owner_id, identifier, state="ready", prompt=message.caption.strip())
            await self.panel(message, identifier)
        except (VideoError, OSError, ValueError, TimeoutError) as error:
            await self.error(message, str(error) if isinstance(error, VideoError) else "media_processing_failed")

    async def launch(self, message, job, action):
        if self.tasks:
            return await self.error(message, "busy")
        expected = "ready" if action == "analyze" else job["state"]
        if action == "render" and (expected not in {"planned", "done", "render_failed", "review"} or not job["data"].get("plan")):
            return await self.error(message, "stale")
        if not self.repo.claim(self.owner_id, job["id"], expected, "analyzing" if action == "analyze" else "rendering"):
            return await self.error(message, "stale")
        self.focus = None
        try:
            await message.answer("🧠 Анализируем кадры и речь…" if action == "analyze" else "🎞 Собираем видео…",
                                 parse_mode=None)
        except BaseException:
            self.repo.save(self.owner_id, job["id"], state=expected)
            raise

        async def work():
            planner = None
            async def activity():
                while True:
                    with suppress(Exception):
                        await message.bot.send_chat_action(self.owner_id, "upload_video")
                    await asyncio.sleep(4)
            animation = asyncio.create_task(activity())
            try:
                async with asyncio.timeout(900):
                    directory = self.repo.directory(job["id"])
                    if action == "analyze":
                        if not self.settings.openai_key:
                            raise VideoError("missing_api_key")
                        data = self.repo.get(self.owner_id, job["id"])["data"]
                        segments = await self.engine.prepare(directory, data["info"], data.get("speech", True))
                        planner = self.planner_factory(self.settings.openai_key, self.repo)
                        for segment in segments:
                            if segment.get("audio"):
                                segment["text"] = await planner.transcribe(self.owner_id, job["id"], segment)
                        self.repo.save(self.owner_id, job["id"], segments=segments)
                        value = await planner.plan(self.owner_id, job["id"], segments, (directory / "music.bin").is_file())
                        self.repo.save(self.owner_id, job["id"], state="planned", plan=value)
                    else:
                        data = self.repo.get(self.owner_id, job["id"])["data"]
                        await self.engine.render(directory, data["info"], data["segments"], data["plan"])
                        self.repo.save(self.owner_id, job["id"], state="done")
                        try:
                            await self.deliver(message, job["id"])
                        except Exception as error:
                            log.warning("admin_video_delivery_unknown type=%s", type(error).__name__)
                    with suppress(Exception):
                        await self.panel(message, job["id"])
            except asyncio.CancelledError:
                self.repo.save(self.owner_id, job["id"], state="review", error="interrupted")
                raise
            except Exception as error:
                # Сырые ответы API, названия файлов и содержимое не попадают в лог.
                code = str(error) if isinstance(error, VideoError) else "processing_failed"
                self.repo.save(self.owner_id, job["id"], state="failed" if action == "analyze" else "render_failed", error=code)
                log.warning("admin_video_failed stage=%s type=%s", action, type(error).__name__)
                try:
                    await self.error(message, code)
                    await self.panel(message, job["id"])
                except Exception:
                    pass
            finally:
                animation.cancel()
                await asyncio.gather(animation, return_exceptions=True)
                if planner:
                    with suppress(Exception):
                        await planner.close()

        task = asyncio.create_task(work())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def deliver(self, message, identifier):
        job = self.repo.get(self.owner_id, identifier)
        if not job or job["state"] != "done":
            raise VideoError("stale")
        path = self.repo.directory(identifier) / "result.mp4"
        if not path.is_file():
            raise VideoError("expired")
        await message.answer_video(FSInputFile(path, filename="Образ-монтаж.mp4"), supports_streaming=True,
                                   caption="🎬 Тестовый монтаж · Образ", parse_mode=None, request_timeout=120)

    async def report(self, message, identifier):
        job = self.repo.get(self.owner_id, identifier)
        data = json.dumps({"job": job, "calls": self.repo.calls(self.owner_id, identifier),
                          "billing_note": "Usage absent means unknown; cached/reasoning are already included."},
                         ensure_ascii=False, indent=2).encode("utf-8")
        await message.answer_document(BufferedInputFile(data, filename="video-usage.json"),
                                      caption="Фактический usage каждого вызова, без ключей API.", parse_mode=None)

    async def error(self, message, code):
        texts = {"busy": "Уже обрабатывается одно видео. Дождитесь результата.",
                 "file_too_large": "Видео — до 19 MB, музыка — до 5 MB. Сожмите файл и повторите загрузку.",
                 "video_limits": "Нужен ролик до 90 секунд и 4K. Поддерживаются MP4/MOV/WebM.",
                 "video_required": "Сначала пришлите видео.",
                 "prompt_required": "После загрузки видео напишите пожелания: 3–1500 символов.",
                 "ffmpeg_missing": "FFmpeg пока недоступен на сервере.",
                 "provider_rejected": "OpenAI отклонил запрос. Проверьте доступ к модели и баланс; расход уже сохранён в отчёте, если он получен.",
                 "invalid_plan": "Модель не вернула допустимый план. Расход сохранён; автоматического повтора нет.",
                 "output_too_long": "Модель выбрала больше 60 секунд. Расход сохранён, автоматического повтора нет.",
                 "expired": "Файлы уже удалены по сроку хранения. Создайте новую работу.",
                 "stale": "Эта кнопка уже обработана или относится к другому шагу."}
        await message.answer(texts.get(code, "Обработка не завершена. Расходы сохранены; повторного платного запроса не было."),
                             parse_mode=None, reply_markup=keyboard(BACK))

    async def start(self, **kwargs):
        if not self.settings.admin_video_enabled:
            return
        self.repo.cleanup()
        async def maintain():
            while True:
                await asyncio.sleep(60)
                with suppress(Exception):
                    self.repo.cleanup()
        self.maintenance = asyncio.create_task(maintain())

    async def close(self, **kwargs):
        if self.maintenance:
            self.maintenance.cancel()
            await asyncio.gather(self.maintenance, return_exceptions=True)
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


def register_admin_video(router, settings, store, navigation=()):
    controller = AdminVideo(settings, store)

    class FocusMiddleware(BaseMiddleware):
        async def __call__(self, handler, event, data):
            if controller.owner(event.message, event.from_user) and not (event.data or "").startswith("video:"):
                controller.focus = None
            return await handler(event, data)

    router.callback_query.outer_middleware(FocusMiddleware())
    router.callback_query.register(controller.callback, F.data.startswith("video:"))

    def active(message):
        if not controller.owner(message, message.from_user) or not controller.focus:
            return False
        if message.text and (message.text.startswith("/") or message.text in navigation):
            controller.focus = None
            return False
        return True

    router.message.register(controller.accept, active)
    return controller
