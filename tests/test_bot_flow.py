import asyncio
import json
import os
from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Document, Message, PhotoSize, Update, User
from test_bot_ui import OfflineBot, confirmation

import image_studio.bot as module
from image_studio.bot import build_dispatcher
from image_studio.config import Settings
from image_studio.media import Media
from image_studio.provider import MockProvider
from image_studio.service import Service
from image_studio.store import Store


def build_ui(tmp_path, *, trial=True):
    store, media, provider = Store(tmp_path / "db.sqlite3"), Media(tmp_path), MockProvider()
    store.consent(1)
    if not trial:
        store.grant_demo(1)
    service = Service(store, media, provider, trial_access=trial)
    service.wallet(1)
    settings = Settings(image_provider="openai" if trial else "mock", trial_access=trial)
    return build_dispatcher(settings, store, media, service), OfflineBot(), store, media, service, provider


@pytest.fixture
def flow(tmp_path):
    return build_ui(tmp_path)


def own_photo(user=1, chat=None, *, document=False, message_id=50):
    fields = {"document": Document(file_id="own-doc", file_unique_id="own-doc", file_name="own.jpg",
                                   mime_type="image/jpeg", file_size=100)} if document else {
        "photo": [PhotoSize(file_id="own-photo", file_unique_id="own-photo", width=256, height=256)]}
    return Message(message_id=message_id, date=datetime.now(timezone.utc),
                   chat=Chat(id=chat or user, type="private"),
                   from_user=User(id=user, first_name="Тест", is_bot=False), **fields)


async def feed(ui, text=None, *, callback=None, photo=False, reply=None, message_id=None, user=1):
    dp, bot, *_ = ui
    bot.sequence += 1
    mid = message_id or 1000 + bot.sequence
    message = Message(message_id=mid, date=datetime.now(timezone.utc),
                      chat=Chat(id=user, type="private"),
                      from_user=User(id=user, first_name="Тест", is_bot=False), text=text,
                      photo=own_photo(user).photo if photo else None, reply_to_message=reply)
    update = Update(update_id=bot.sequence, message=message) if callback is None else Update(
        update_id=bot.sequence, callback_query=CallbackQuery(id=str(bot.sequence),
        from_user=message.from_user, chat_instance="offline", message=message, data=callback))
    bot.sent.clear()
    await dp.feed_update(bot, update)
    return list(bot.sent)


def texts(sent):
    return "\n".join((getattr(method, "text", "") or getattr(method, "caption", "") or "")
                     for method in sent)


def button_data(sent, prefix):
    for method in sent:
        markup = getattr(method, "reply_markup", None)
        for row in getattr(markup, "inline_keyboard", []):
            for button in row:
                if (button.callback_data or "").startswith(prefix):
                    return button.callback_data
    return None


async def test_trial_description_auto_queue_once_replay_after_new_draft_and_retry(flow):
    _, _, store, _, service, provider = flow
    prompt = await feed(flow, callback="preset:hair")
    assert "JPEG/PNG/WebP до 10 MB.\nИли сфотографируйте" in texts(prompt)
    await feed(flow, photo=True)
    sent = await feed(flow, "Каре", message_id=200)
    assert confirmation(sent) is None and "Шаг 3" in texts(sent) and "Создаём" in texts(sent)
    assert len(store.jobs(1)) == 1 and service.wallet(1) == (3, 1) and provider.calls == 0
    job = store.jobs(1)[0]
    assert job["request_key"] == "telegram-description:200" and job["id"] not in texts(sent)
    await feed(flow, "Каре", message_id=200)
    assert len(store.jobs(1)) == 1
    assert await service.process(job["id"])
    reuse = button_data(await feed(flow, callback="preset:hair"), "reuse:")
    assert reuse
    await feed(flow, callback=reuse)
    await feed(flow, "Каре", message_id=200)
    assert len(store.jobs(1)) == 1 and service.wallet(1) == (2, 0)
    await feed(flow, "Ещё один вариант", message_id=201)
    assert len(store.jobs(1)) == 2 and service.wallet(1) == (2, 1) and provider.calls == 1


async def test_reuse_sources_survive_restart_and_ignore_generated_output(flow, tmp_path):
    await feed(flow, callback="preset:hair")
    await feed(flow, photo=True)
    await feed(flow, "Каре")
    store, media, service = flow[2:5]
    job = store.jobs(1)[0]
    assert await service.process(job["id"])
    source = json.loads(media.path(f"1/{job['id']}.json").read_text())["inputs"][0]
    restarted = build_ui(tmp_path)
    sent = await feed(restarted, callback="preset:glasses")
    callback = button_data(sent, "reuse:")
    assert callback and any(
        button.callback_data == callback and button.text == "Использовать загруженное фото"
        for method in sent
        for row in getattr(getattr(method, "reply_markup", None), "inline_keyboard", [])
        for button in row
    )
    assert "Шаг 2" in texts(await feed(restarted, callback=callback))
    await feed(restarted, "Тонкая оправа", message_id=220)
    new = next(record for record in store.jobs(1) if record["status"] == "queued")
    payload = json.loads(media.path(f"1/{new['id']}.json").read_text())
    assert payload["inputs"] == [source] and payload["inputs"] != [store.job(job["id"])["result"]]


@pytest.mark.parametrize("document", [False, True])
async def test_reply_to_own_attachment_only_selects_then_description_submits(flow, document):
    await feed(flow, callback="preset:hair")
    selected = await feed(flow, "возьми это фото", reply=own_photo(document=document))
    assert "Шаг 2" in texts(selected) and flow[2].jobs(1) == []
    await feed(flow, "Каре", message_id=230)
    assert len(flow[2].jobs(1)) == 1


@pytest.mark.parametrize("source", [own_photo(2), own_photo(1, chat=2)])
async def test_reply_foreign_or_cross_chat_attachment_cannot_submit(flow, source):
    await feed(flow, callback="preset:hair")
    await feed(flow, photo=True)
    await feed(flow, "Каре", reply=source)
    assert flow[2].jobs(1) == [] and flow[5].calls == 0


async def test_reuse_callback_rechecks_expiry_consent_and_draft(flow):
    await feed(flow, callback="preset:hair")
    await feed(flow, photo=True)
    path = flow[4].reusable_inputs(1, 1)[0]
    reuse = button_data(await feed(flow, callback="preset:hair"), "reuse:")
    os.utime(flow[3].path(path), (1, 1))
    assert "Шаг 2" not in texts(await feed(flow, callback=reuse))
    assert flow[2].jobs(1) == []
    await feed(flow, photo=True)
    reuse = button_data(await feed(flow, callback="preset:hair"), "reuse:")
    await feed(flow, callback="nav:home")
    assert "Шаг 2" not in texts(await feed(flow, callback=reuse))
    await feed(flow, callback="preset:hair")
    reuse = button_data(await feed(flow, callback="preset:hair"), "reuse:")
    flow[4].delete(1)
    assert "Шаг 2" not in texts(await feed(flow, callback=reuse))
    assert flow[2].jobs(1) == []


@pytest.mark.parametrize("preset,count", [("glasses", 1), ("merge", 2)])
async def test_choose_new_sources_clears_reuse_and_requires_all_new_uploads(flow, preset, count):
    await feed(flow, callback="preset:" + preset)
    for _ in range(count):
        await feed(flow, photo=True)
    previous = flow[4].reusable_inputs(1, count)
    selected = await feed(flow, callback="preset:" + preset)
    fresh, reuse = button_data(selected, "fresh:"), button_data(selected, "reuse:")
    assert fresh and reuse
    assert "Шаг 1" in texts(await feed(flow, callback=fresh))
    assert "Шаг 2" not in texts(await feed(flow, callback=reuse))
    await feed(flow, "Новый образ")
    assert flow[2].jobs(1) == []
    for _ in range(count):
        await feed(flow, photo=True)
    await feed(flow, "Новый образ")
    job = flow[2].jobs(1)[0]
    inputs = json.loads(flow[3].path(f"1/{job['id']}.json").read_text())["inputs"]
    assert len(inputs) == count and all(path not in previous for path in inputs)
    assert job["cost"] == 1 and flow[4].wallet(1) == (3, 1) and flow[5].calls == 0


async def test_result_list_download_and_support_are_friendly_without_ids(flow):
    await feed(flow, callback="preset:hair")
    await feed(flow, photo=True)
    await feed(flow, "Каре")
    job = flow[2].jobs(1)[0]
    assert await flow[4].process(job["id"])
    listed = await feed(flow, "/result")
    assert button_data(listed, "result:") and "Скачать ещё раз" in texts(listed)
    assert job["id"] not in texts(listed)
    for method in listed:
        for row in getattr(getattr(method, "reply_markup", None), "inline_keyboard", []):
            assert all(job["id"][:6] not in button.text for button in row)
    calls, wallet = flow[5].calls, flow[4].wallet(1)
    downloaded = await feed(flow, callback="result:" + job["id"])
    documents = [method.document for method in downloaded if method.__api_method__ == "sendDocument"]
    assert documents and all(document.filename == "Ваш образ.jpg" for document in documents)
    assert flow[5].calls == calls and flow[4].wallet(1) == wallet
    assert "ID" not in texts(await feed(flow, "/support"))


async def test_nontrial_still_requires_green_confirmation(tmp_path):
    ui = build_ui(tmp_path, trial=False)
    await feed(ui, callback="preset:hair")
    await feed(ui, photo=True)
    sent = await feed(ui, "Каре")
    button = confirmation(sent)
    assert button and button.style == "success" and ui[2].jobs(1) == []
    accepted = await feed(ui, callback=button.callback_data)
    assert len(ui[2].jobs(1)) == 1 and ui[2].jobs(1)[0]["id"] not in texts(accepted)


async def test_processing_activity_terminal_filter_failures_and_cancellation():
    records = [{"user_id": 0, "status": "queued"}, {"user_id": 1, "status": "queued"},
               {"user_id": 2, "status": "running"}, {"user_id": 3, "status": "generated"}]

    class ActivityStore:
        def jobs(self):
            return records

    class ActivityBot:
        calls = []

        async def send_chat_action(self, user, action):
            self.calls.append((user, action))
            if user == 1:
                raise OSError("private fake payload")

    bot = ActivityBot()
    task = asyncio.create_task(module.processing_activity(bot, ActivityStore(), interval=0.005))
    await asyncio.sleep(0.02)
    assert (1, "upload_photo") in bot.calls and (2, "upload_photo") in bot.calls
    assert all(user in {1, 2} for user, _ in bot.calls)
    for record in records:
        record["status"] = "delivered"
    await asyncio.sleep(0.01)
    count = len(bot.calls)
    await asyncio.sleep(0.01)
    assert len(bot.calls) == count
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
