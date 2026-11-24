import asyncio
import errno
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Document, Message, Update, User, Video
from test_bot_flow import TrackedBot

from image_studio.bot import build_dispatcher
from image_studio.config import Settings
from image_studio.filmcraft_engine import FilmcraftError, media_path
from image_studio.filmcraft_video import OWNER, FilmcraftPlanner, LimitedDownload, WorkerClient, action
from image_studio.media import Media
from image_studio.provider import MockProvider
from image_studio.service import Service
from image_studio.store import Store


class Worker:
    def __init__(self):
        self.calls, self.fail = [], None

    def ready(self):
        return True

    async def run(self, directory, operation, **data):
        self.calls.append(operation)
        if self.fail:
            raise FilmcraftError(self.fail)
        if operation == "probe":
            return {"duration": 2, "width": 640, "height": 480, "audio": True}
        if operation == "prepare":
            segments = []
            for i, _ in enumerate(data["files"]):
                (directory / f"frame-{i}.jpg").write_bytes(b"frame")
                (directory / f"audio-{i}.wav").write_bytes(b"audio")
                segments.append({"source": i, "start": 0, "end": 2, "frame": f"frame-{i}.jpg",
                                 **({"audio": f"audio-{i}.wav"} if data["speech"] else {})})
            return {"segments": segments}
        (directory / "result.mp4").write_bytes(b"synthetic-filmcraft")
        return {"output": "result.mp4", "verified": True}


@pytest.fixture
def ui(tmp_path):
    settings = Settings(data_dir=tmp_path, admin_user_id=OWNER, filmcraft_enabled=True,
                        admin_video_enabled=True, openai_key="offline")
    store, media, provider = Store(tmp_path / "main.sqlite3"), Media(tmp_path), MockProvider()
    store.consent(OWNER)
    dp = build_dispatcher(settings, store, media, Service(store, media, provider))
    controller = dp["filmcraft_video"]
    controller.worker = Worker()
    value = dict(title="Образ", format="portrait", transition="cut", sound="original", subtitles=True, clips=[0])
    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(
            status="completed", output_text=json.dumps(dict(decision="render", note="", plan=value)), _request_id="plan-request",
            usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 100, "output_tokens": 40})))),
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(
            text="Реальная речь", usage=None, _request_id="audio-request")))), close=AsyncMock())
    controller.planner_factory = lambda key, repo: FilmcraftPlanner(key, repo, client=client)
    bot = TrackedBot()

    async def download(file, destination, **kwargs):
        destination.write(b"uploaded" * 10)
    bot.download = download
    return dp, bot, store, controller, client, provider


async def send(ui, text=None, callback=None, *, user=OWNER, file=None, size=100, group=False, caption=None, album=None):
    dp, bot, *_ = ui
    bot.sequence += 1
    who = User(id=user, is_bot=False, first_name="Tester")
    fields = {}
    if file:
        fields["video"] = Video(file_id=file, file_unique_id=file, file_size=size, width=640, height=480, duration=2)
    message = Message(message_id=bot.sequence, date=datetime.now(timezone.utc), from_user=who,
                      chat=Chat(id=user, type="group" if group else "private"), text=text, caption=caption,
                      media_group_id=album, **fields)
    update = Update(update_id=bot.sequence, **({"callback_query": CallbackQuery(id=str(bot.sequence),
        from_user=who, chat_instance="test", message=message, data=callback)} if callback else {"message": message}))
    bot.sent.clear()
    await dp.feed_update(bot, update)
    return list(bot.sent)


async def drain(controller):
    await asyncio.gather(*list(controller.tasks))
    await asyncio.sleep(0)


async def ready(ui):
    await send(ui, "Хочу сделать видео")
    await send(ui, file="first")
    return ui[3].current()


async def test_owner_only_flag_group_and_admin_entry(ui, tmp_path):
    dp, _, store, controller, client, provider = ui
    await send(ui, "Хочу сделать видео", user=42)
    await send(ui, callback="fc:new", user=42)
    await send(ui, "Хочу сделать видео", group=True)
    assert controller._repo is None and not controller.worker.calls
    sent = await send(ui, "/admin")
    buttons = {b.callback_data for m in sent for row in
               getattr(getattr(m, "reply_markup", None), "inline_keyboard", []) for b in row}
    assert "fc:home" in buttons and "video:home" not in buttons
    controller.settings.filmcraft_enabled = False
    await send(ui, "/filmcraft")
    await send(ui, callback="fc:new")
    assert controller._repo is None and client.responses.create.await_count == 0 and provider.calls == 0
    with pytest.raises(ValueError, match="filmcraft_owner"):
        Settings(admin_user_id=42, filmcraft_enabled=True).validate()


async def test_open_button_explains_storage_error_before_any_analysis(ui, monkeypatch):
    *_, controller, client, _ = ui

    def unavailable(owner):
        raise OSError(errno.EROFS, "storage unavailable")

    monkeypatch.setattr(controller.repo, "create", unavailable)
    sent = await send(ui, callback="fc:home")
    assert "не запускал анализ OpenAI" in sent[-1].text
    assert controller.focus is None and not controller.worker.calls
    client.responses.create.assert_not_called()


async def test_multiple_upload_caption_dedup_limits_and_stale_confirmation(ui):
    _, _, _, c, client, _ = ui
    # Album updates are received independently. The caption starts one job after the album settles.
    await send(ui, callback="video:home")
    assert c.legacy.focus is None and c.focus
    await send(ui, file="first", caption="Собери короткий вертикальный ролик", album="test-album")
    job = c.current()
    old = action(job, "analyze")
    await send(ui, file="first", album="test-album")
    await send(ui, file="second", album="test-album")
    assert len(c.current()["data"]["files"]) == 2
    await send(ui, callback=old)
    assert client.responses.create.await_count == 0
    await c.album_task
    await drain(c)
    await send(ui, file="large", size=20_000_000)
    assert c.repo.get(OWNER, job["id"])["state"] == "done" and client.responses.create.await_count == 1
    payload = client.responses.create.call_args.kwargs
    assert '"source_video": 2' in payload["input"][0]["content"][0]["text"]


async def test_plan_paid_once_render_repeat_report_and_photo_balance(ui):
    _, bot, store, c, client, provider = ui
    wallet = store.wallet(OWNER)
    # The file itself activates Filmcraft even when no menu was opened.
    sent = await send(ui, file="first", caption="Учти пожелания человека в кадре и добавь субтитры")
    assert any("Загружаю" in getattr(m, "text", "") for m in sent)
    await drain(c)
    sent = await send(ui, "задача понятна?")
    assert any("План" in getattr(m, "text", "") for m in sent)
    assert c.current()["state"] == "done" and c.worker.calls.count("render") == 1
    assert client.responses.create.await_count == client.audio.transcriptions.create.await_count == 1
    payload = client.responses.create.call_args.kwargs
    assert payload["model"] == "gpt-6.2-sol"
    assert "Реальная речь" in payload["input"][0]["content"][0]["text"] and payload["store"] is False
    for _ in range(2):
        await send(ui, callback=action(c.current(), "render"))
        await drain(c)
    assert c.current()["state"] == "done" and c.worker.calls.count("render") == 3
    assert any(m.__api_method__ == "sendVideo" for m, _ in bot.history)
    await send(ui, callback=action(c.current(), "download"))
    sent = await send(ui, callback=action(c.current(), "report"))
    report = json.loads(next(m.document.data for m in sent if m.__api_method__ == "sendDocument"))
    assert report["calls"][-1]["usage"]["input_tokens"] == 100
    assert client.responses.create.await_count == 1 and store.wallet(OWNER) == wallet and provider.calls == 0
    await send(ui, callback=action(c.current(), "leave"))
    assert c.focus is None
    await send(ui, "/filmcraft")
    assert c.current()["state"] == "done"


async def test_worker_failure_prevents_spend_and_render_retry_does_not_reanalyze(ui):
    *_, c, client, _ = ui
    await ready(ui)
    c.worker.fail = "server_busy"
    await send(ui, "Смонтируй вертикальный ролик с субтитрами")
    await drain(c)
    assert c.current()["state"] == "failed" and client.responses.create.await_count == 0
    c.worker.fail = None
    run = c.worker.run

    async def failed_render(directory, operation, **data):
        if operation == "render":
            raise FilmcraftError("engine_failed")
        return await run(directory, operation, **data)

    c.worker.run = failed_render
    await send(ui, callback=action(c.current(), "analyze"))
    await drain(c)
    assert c.current()["state"] == "render_failed"
    c.worker.run = run
    await send(ui, callback=action(c.current(), "render"))
    await drain(c)
    assert c.current()["state"] == "done" and client.responses.create.await_count == 1


async def test_shutdown_records_unknown_and_never_retries_paid_call(ui):
    *_, c, client, _ = ui
    await ready(ui)
    entered = asyncio.Event()

    async def stall(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    client.audio.transcriptions.create.side_effect = stall
    await send(ui, "Собери с субтитрами")
    await entered.wait()
    await c.close()
    assert c.current()["state"] == "review" and c.repo.calls(OWNER, c.focus)[0]["status"] == "unknown"
    await c.start()
    assert c.current()["state"] == "review" and client.audio.transcriptions.create.await_count == 1
    await c.close()


async def test_unsupported_effect_offers_alternative_and_waits_without_losing_context(ui):
    *_, c, client, _ = ui
    value = client.responses.create.return_value
    result = json.loads(value.output_text)
    result.update(decision="offer", note="3D-взрыв недоступен. Могу добавить затемнение и субтитры.")
    value.output_text = json.dumps(result)
    await ready(ui)
    await send(ui, "Задача понятна?")
    assert not c.current()["data"].get("prompt") and not client.responses.create.await_count
    await send(ui, "Добавь 3D-взрыв и субтитры")
    await drain(c)
    assert c.current()["state"] == "planned" and "render" not in c.worker.calls
    await send(ui, "сделай так")
    await drain(c)
    assert c.current()["state"] == "done" and client.responses.create.await_count == 1


async def test_navigation_does_not_capture_admin_wizard_and_media_expire(ui):
    _, _, _, c, client, _ = ui
    await ready(ui)
    identifier = c.focus
    await send(ui, "/admin")
    assert c.focus is None
    await send(ui, "/filmcraft")
    assert c.focus == identifier
    with c.repo.connect() as db:
        db.execute("UPDATE video_jobs SET created=created-90000 WHERE id=?", (identifier,))
    c.repo.cleanup()
    assert c.current()["state"] == "expired" and not c.repo.directory(identifier).exists()
    assert client.responses.create.await_count == 0


async def test_document_audio_and_size_limiter(ui, tmp_path):
    _, bot, _, c, _, _ = ui
    await ready(ui)
    who = User(id=OWNER, first_name="Owner", is_bot=False)
    document = Document(file_id="music", file_unique_id="music", file_size=100,
                        file_name="song.m4a", mime_type="audio/mp4")
    message = Message(message_id=888, date=datetime.now(timezone.utc), from_user=who,
                      chat=Chat(id=OWNER, type="private"), document=document).as_(bot)
    await c.accept(message)
    assert c.current()["data"]["music"] is True
    with LimitedDownload(tmp_path / "limited", 3) as stream:
        with pytest.raises(FilmcraftError):
            stream.write(b"1234")
    with pytest.raises(FilmcraftError):
        media_path(tmp_path, "../../outside")


async def test_worker_client_cancel_does_not_leave_executable_request(tmp_path):
    import time

    from image_studio.filmcraft_engine import atomic_json
    directory = tmp_path / ("a" * 32)
    directory.mkdir()
    atomic_json(tmp_path / "worker-health.json", {"time": time.time(), "version": "0.20.1"})
    client = WorkerClient(tmp_path)
    task = asyncio.create_task(client.run(directory, "prepare", files=[], speech=False))
    await asyncio.sleep(.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not list(directory.glob("request-*.json"))
    assert list(directory.glob("request-*.cancel"))
