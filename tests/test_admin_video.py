import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Document, Message, Update, User, Video
from test_bot_flow import TrackedBot

from image_studio.admin_video import LimitedBuffer, usage_text
from image_studio.bot import build_dispatcher
from image_studio.config import Settings
from image_studio.media import Media
from image_studio.provider import MockProvider
from image_studio.service import Service
from image_studio.store import Store
from image_studio.video_montage import OpenAIPlanner, VideoError


class Engine:
    probe = AsyncMock(return_value={"duration": 2.0, "width": 640, "height": 480, "audio": True})

    async def prepare(self, directory, info, speech):
        frame, audio = directory / "frame.jpg", directory / "audio.wav"
        frame.write_bytes(b"fake-frame")
        audio.write_bytes(b"fake-audio")
        return [{"start": 0, "end": 2, "frame": str(frame), "audio": str(audio), "text": ""}]

    async def render(self, directory, info, segments, plan):
        path = directory / "result.mp4"
        path.write_bytes(b"fake-local-render")
        return path


@pytest.fixture
def video_ui(tmp_path):
    settings = Settings(data_dir=tmp_path, admin_user_id=1, admin_video_enabled=True, openai_key="offline")
    store, media, provider = Store(tmp_path / "main.sqlite3"), Media(tmp_path), MockProvider()
    store.consent(1)
    service = Service(store, media, provider)
    dp = build_dispatcher(settings, store, media, service)
    controller = dp["admin_video"]
    controller.engine = Engine()
    value = {"title": "Тест", "format": "portrait", "transition": "fade", "sound": "original", "subtitles": True, "clips": [0]}
    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(
            status="completed", output_text=json.dumps(value), _request_id="req-plan",
            usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 100, "output_tokens": 50})))),
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(
            text="Привет, тест!", _request_id="req-asr", usage=None)))), close=AsyncMock())
    controller.planner_factory = lambda key, repo: OpenAIPlanner(key, repo, client=client)
    bot = TrackedBot()

    async def download(file, destination, **kwargs):
        destination.write(b"uploaded-video")

    bot.download = download
    return dp, bot, store, controller, client, provider


async def send(ui, text=None, callback=None, *, user=1, video=False, document=False, chat_type="private", size=100):
    dp, bot, *_ = ui
    bot.sequence += 1
    who = User(id=user, is_bot=False, first_name="Test")
    fields = {}
    if video:
        fields["video"] = Video(file_id="source", file_unique_id="source", width=640, height=480, duration=2, file_size=size)
    if document:
        fields["document"] = Document(file_id="document", file_unique_id="document", file_size=size, mime_type="video/mp4")
    message = Message(message_id=bot.sequence, date=datetime.now(timezone.utc), chat=Chat(id=user, type=chat_type),
                      from_user=who, text=text, **fields)
    update = Update(update_id=bot.sequence, **({"callback_query": CallbackQuery(id=str(bot.sequence),
        from_user=who, chat_instance="test", message=message, data=callback)} if callback else {"message": message}))
    bot.sent.clear()
    await dp.feed_update(bot, update)
    return list(bot.sent)


async def drain(controller):
    tasks = list(controller.tasks)
    if tasks:
        await asyncio.gather(*tasks)
    await asyncio.sleep(0)


async def test_owner_only_menu_and_forged_callbacks_do_not_create_repo_or_spend(video_ui):
    *_, controller, client, provider = video_ui
    sent = await send(video_ui, callback="video:new", user=2)
    assert any(getattr(item, "show_alert", False) for item in sent)
    assert controller._repo is None
    await send(video_ui, callback="video:analyze:" + "a" * 32, user=2)
    await send(video_ui, callback="video:new", chat_type="group")
    assert controller._repo is None and client.responses.create.await_count == 0
    menu = await send(video_ui, "/admin")
    assert any(button.callback_data == "video:home" for item in menu
               for row in getattr(getattr(item, "reply_markup", None), "inline_keyboard", []) for button in row)
    assert provider.calls == 0


async def test_disabled_feature_cannot_be_started_by_owner(video_ui, tmp_path):
    _, bot, store, _, client, provider = video_ui
    dp = build_dispatcher(Settings(data_dir=tmp_path, admin_user_id=1), store, Media(tmp_path),
                          Service(store, Media(tmp_path), provider))
    disabled = (dp, bot, store, dp["admin_video"], client, provider)
    await send(disabled, callback="video:new")
    assert disabled[3]._repo is None and client.responses.create.await_count == 0
    sent = await send(disabled, "/admin")
    assert not any(button.callback_data == "video:home" for item in sent
                   for row in getattr(getattr(item, "reply_markup", None), "inline_keyboard", []) for button in row)


@pytest.mark.parametrize("document", [False, True])
async def test_upload_plan_render_repeat_and_durable_usage_without_photo_credits(video_ui, document):
    dp, bot, store, controller, client, provider = video_ui
    wallet = store.wallet(1)
    await send(video_ui, callback="video:new")
    identifier = controller.focus
    await send(video_ui, video=not document, document=document)
    await send(video_ui, "Сделай вертикальное видео с субтитрами")
    assert controller.repo.get(1, identifier)["state"] == "ready"
    assert client.responses.create.await_count == 0
    await send(video_ui, callback="video:analyze:" + identifier)
    await send(video_ui, callback="video:analyze:" + identifier)
    await drain(controller)
    assert controller.repo.get(1, identifier)["state"] == "planned"
    assert client.responses.create.await_count == client.audio.transcriptions.create.await_count == 1
    calls = controller.repo.calls(1, identifier)
    assert calls[0]["usage"] == {} and "неизвестно" in usage_text(calls)
    assert calls[1]["usage"]["input_tokens"] == 100
    for _ in range(2):
        await send(video_ui, callback="video:render:" + identifier)
        await drain(controller)
    assert controller.repo.get(1, identifier)["state"] == "done"
    assert any(item.__api_method__ == "sendVideo" for item, _ in bot.history)
    sent = await send(video_ui, callback="video:report:" + identifier)
    report = json.loads(next(item.document.data for item in sent if item.__api_method__ == "sendDocument"))
    assert report["calls"][1]["usage"]["output_tokens"] == 50
    assert client.responses.create.await_count == 1 and store.wallet(1) == wallet and provider.calls == 0
    await controller.close()


async def test_size_limit_and_admin_navigation_release_video_capture(video_ui):
    _, _, _, controller, client, _ = video_ui
    await send(video_ui, callback="video:new")
    identifier = controller.focus
    await send(video_ui, video=True, size=20_000_000)
    assert not (controller.repo.directory(identifier) / "source.mp4").exists()
    await send(video_ui, callback="admin:home")
    assert controller.focus is None and client.responses.create.await_count == 0
    bounded = LimitedBuffer(3)
    with pytest.raises(VideoError):
        bounded.write(b"1234")


async def test_usage_report_remains_accessible_after_media_expiry(video_ui):
    _, _, _, controller, client, _ = video_ui
    await send(video_ui, callback="video:new")
    identifier = controller.focus
    call = controller.repo.call_start(1, identifier, "plan", "gpt-6.1-sol")
    controller.repo.call_finish(call, "completed", usage={"input_tokens": 20, "output_tokens": 10})
    with controller.repo.connect() as db:
        db.execute("UPDATE video_jobs SET created=created-90000 WHERE id=?", (identifier,))
    await send(video_ui, callback="video:job:" + identifier)
    assert controller.repo.get(1, identifier)["state"] == "expired"
    sent = await send(video_ui, callback="video:report:" + identifier)
    report = json.loads(next(item.document.data for item in sent if item.__api_method__ == "sendDocument"))
    assert report["calls"][0]["usage"]["input_tokens"] == 20 and report["job"]["data"] == {}
    assert client.responses.create.await_count == 0


async def test_shutdown_cancels_pending_api_without_retry_and_records_unknown(video_ui):
    _, _, _, controller, client, _ = video_ui
    entered = asyncio.Event()

    async def stall(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    client.audio.transcriptions.create.side_effect = stall
    await send(video_ui, callback="video:new")
    identifier = controller.focus
    await send(video_ui, video=True)
    await send(video_ui, "Сделай короткий клип")
    await send(video_ui, callback="video:analyze:" + identifier)
    await entered.wait()
    await controller.close()
    assert controller.repo.get(1, identifier)["state"] == "review"
    assert controller.repo.calls(1, identifier)[0]["status"] == "unknown"
    assert client.audio.transcriptions.create.await_count == 1 and client.responses.create.await_count == 0
