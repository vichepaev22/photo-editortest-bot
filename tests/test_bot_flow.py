import asyncio
import json
import os
from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Document, Message, PhotoSize, Update, User
from PIL import Image
from test_bot_ui import OfflineBot, confirmation

import image_studio.bot as module
from image_studio.bot import build_dispatcher
from image_studio.config import Settings
from image_studio.media import Media
from image_studio.provider import MockProvider
from image_studio.service import Service
from image_studio.store import Store


class TrackedBot(OfflineBot):
    """Telegram-like distinct outbound IDs, including a delayed progress acknowledgement."""

    def __init__(self, *, bot_id=123456789):
        Bot.__init__(self, f"{bot_id}:ABCdefghijklmnopqrstuvwxyz123456789")
        self.sent = []
        self.sequence = 0
        self.message_sequence = 5000
        self.history = []
        self.before_progress = None
        self.fail_delete = False
        self.fail_document = False
        self.on_delete = None

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        api = method.__api_method__
        if api == "deleteMessages":
            if self.on_delete:
                self.on_delete()
            self.history.append((method, None))
            if self.fail_delete:
                raise OSError("private fake payload")
            return True
        if api in {"answerCallbackQuery", "sendChatAction"}:
            return True
        if api == "sendDocument" and self.fail_document:
            raise OSError("private fake payload")
        if api == "sendMessage" and "Создаём ваш образ" in method.text and self.before_progress:
            hook, self.before_progress = self.before_progress, None
            await hook()
        self.message_sequence += 1
        response = Message(message_id=self.message_sequence, date=datetime.now(timezone.utc),
                           chat=Chat(id=method.chat_id, type="private"))
        self.history.append((method, response))
        return response


def build_ui(tmp_path, *, trial=True, admin_user_id=0, owner_unlimited_testing=False, bot_id=123456789):
    store, media, provider = Store(tmp_path / "db.sqlite3"), Media(tmp_path), MockProvider()
    store.consent(1)
    if not trial:
        store.grant_demo(1)
    service = Service(store, media, provider, trial_access=trial,
                      unlimited_user_id=admin_user_id if owner_unlimited_testing else 0)
    service.wallet(1)
    settings = Settings(image_provider="openai" if trial else "mock", trial_access=trial,
                        admin_user_id=admin_user_id, owner_unlimited_testing=owner_unlimited_testing)
    return build_dispatcher(settings, store, media, service), TrackedBot(bot_id=bot_id), store, media, service, provider


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


async def test_owner_unlimited_native_balance_and_submission_only_for_owner(tmp_path):
    ui = build_ui(tmp_path, admin_user_id=1, owner_unlimited_testing=True)
    assert "безлимитное" in texts(await feed(ui, "/start")).lower()
    assert "Безлимитное" in texts(await feed(ui, callback="nav:balance"))
    await feed(ui, callback="preset:hair")
    assert "Безлимитное" in texts(await feed(ui, photo=True))
    for index in range(4):
        if index:
            await feed(ui, callback="preset:hair")
            await feed(ui, photo=True)
        sent = await feed(ui, "Каре", message_id=300+index)
        assert "Безлимитное тестирование" in texts(sent)
        job = ui[2].jobs(1)[-1]
        assert job["quota_exempt"] == 1 and await ui[4].process(job["id"])
    ui[2].consent(2)
    non_owner = texts(await feed(ui, callback="nav:balance", user=2))
    assert "безлимит" not in non_owner.lower() and "1 бесплатная успешная генерация" in non_owner
    assert ui[2].wallet(1, trial=True) == (0, 0) and ui[5].calls == 4


def button_data(sent, prefix):
    for method in sent:
        markup = getattr(method, "reply_markup", None)
        for row in getattr(markup, "inline_keyboard", []):
            for button in row:
                if (button.callback_data or "").startswith(prefix):
                    return button.callback_data
    return None


async def test_trial_description_auto_queue_once_replay_after_new_draft_and_exhausted_variation(flow):
    _, _, store, _, service, provider = flow
    prompt = await feed(flow, callback="preset:hair")
    assert "JPEG/PNG/WebP до 10 MB.\nИли сфотографируйте" in texts(prompt)
    await feed(flow, photo=True)
    sent = await feed(flow, "Каре", message_id=200)
    assert confirmation(sent) is None and "Шаг 3" in texts(sent) and "Создаём" in texts(sent)
    assert len(store.jobs(1)) == 1 and service.wallet(1) == (1, 1) and provider.calls == 0
    job = store.jobs(1)[0]
    assert job["request_key"] == f"telegram-description:{flow[1].id}:200" and job["id"] not in texts(sent)
    await feed(flow, "Каре", message_id=200)
    assert len(store.jobs(1)) == 1
    assert await service.process(job["id"])
    reuse = button_data(await feed(flow, callback="preset:hair"), "reuse:")
    assert reuse
    await feed(flow, callback=reuse)
    await feed(flow, "Каре", message_id=200)
    assert len(store.jobs(1)) == 1 and service.wallet(1) == (0, 0)
    rejected = await feed(flow, "Ещё один вариант", message_id=201)
    assert "Бесплатная генерация уже использована" in texts(rejected)
    assert len(store.jobs(1)) == 1 and service.wallet(1) == (0, 0) and provider.calls == 1


async def test_description_keys_separate_bot_ids_and_preserve_legacy_history_and_duplicate_jobs(tmp_path):
    # The owner gate lets this request-identity scenario exercise multiple successful results.
    old = build_ui(tmp_path, bot_id=123456789, admin_user_id=1, owner_unlimited_testing=True)
    store = old[2]
    store.grant_trial(1)
    legacy = store.reserve(1, "telegram-description:200", "hair", 1, trial=True)
    assert store.claim(legacy)
    store.finish(legacy, "legacy-result.jpg", {}, None)
    legacy_history = store.job(legacy)
    await feed(old, callback="preset:hair")
    await feed(old, photo=True)
    await feed(old, "Каре", message_id=200)
    first = next(job for job in store.jobs(1) if job["status"] == "queued")
    wallet = old[4].wallet(1)
    await feed(old, "Каре", message_id=200)
    assert len(store.jobs(1)) == 2 and old[4].wallet(1) == wallet and old[5].calls == 0
    assert await old[4].process(first["id"])
    first_history = store.job(first["id"])

    new = build_ui(tmp_path, bot_id=987654321, admin_user_id=1, owner_unlimited_testing=True)
    assert new[2].path == store.path and new[2].jobs(1) == store.jobs(1)
    await feed(new, callback="preset:hair")
    await feed(new, photo=True)
    await feed(new, "Каре", message_id=200)
    second = next(job for job in store.jobs(1) if job["status"] == "queued")
    assert first["id"] != second["id"]
    assert {job["request_key"] for job in store.jobs(1)} == {
        "telegram-description:200", f"telegram-description:{old[1].id}:200",
        f"telegram-description:{new[1].id}:200",
    }
    wallet = new[4].wallet(1)
    await feed(new, "Каре", message_id=200)
    assert len(store.jobs(1)) == 3 and new[4].wallet(1) == wallet and new[5].calls == 0
    assert await new[4].process(second["id"])
    await feed(new, "Каре", message_id=200)
    assert len(store.jobs(1)) == 3 and new[4].wallet(1) == (0, 0)
    assert old[5].calls == new[5].calls == 1
    assert store.job(legacy) == legacy_history and store.job(first["id"]) == first_history


async def test_reuse_sources_survive_restart_and_ignore_generated_output(flow, tmp_path):
    await feed(flow, callback="preset:hair")
    await feed(flow, photo=True)
    await feed(flow, "Каре")
    store, media, service = flow[2:5]
    job = store.jobs(1)[0]
    assert await service.process(job["id"])
    source = json.loads(media.path(f"1/{job['id']}.json").read_text())["inputs"][0]
    restarted = build_ui(tmp_path, admin_user_id=1, owner_unlimited_testing=True)
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
    assert job["cost"] == 1 and flow[4].wallet(1) == (1, 1) and flow[5].calls == 0


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
    assert documents and all(document.filename == "Образ · результат.jpg" for document in documents)
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


def deleted_ids(bot):
    return {mid for method, _ in bot.history if method.__api_method__ == "deleteMessages"
            for mid in method.message_ids}


def step_ids(bot):
    return {response.message_id for method, response in bot.history
            if method.__api_method__ == "sendMessage"
            and any(text in method.text for text in ["Шаг 1", "Шаг 2", "Шаг 3", "Создаём ваш образ"])}


async def test_worker_delivery_cleans_only_steps_even_before_progress_ack(flow):
    dp, bot, store, _, service, provider = flow
    await feed(flow, "/start", message_id=1200)
    await feed(flow, callback="preset:glasses")
    await feed(flow, photo=True, message_id=1201)
    await feed(flow, "/help", message_id=1202)
    await feed(flow, "x" * 1501, message_id=1203)
    assert deleted_ids(bot) == set()

    async def deliver(user, job, path):
        await module.deliver_result(bot, store, dp["step_messages"], user, job, path)

    async def finish_before_progress():
        job = store.jobs(1)[0]["id"]
        assert await service.process(job)

    def check_delivery_before_delete():
        assert store.jobs(1)[0]["status"] == "delivered"

    service.deliver = deliver
    bot.before_progress = finish_before_progress
    bot.on_delete = check_delivery_before_delete
    await feed(flow, "Тонкая оправа", message_id=1204)
    assert store.jobs(1)[0]["status"] == "delivered" and service.wallet(1) == (2, 0)
    assert provider.calls == 1 and deleted_ids(bot) == step_ids(bot)
    kept = {response.message_id for method, response in bot.history if response
            and (method.__api_method__ == "sendDocument" or "фотостудия" in getattr(method, "text", "")
                 or "Как пользоваться" in getattr(method, "text", "")
                 or "Описание должно" in getattr(method, "text", ""))}
    assert kept and kept.isdisjoint(deleted_ids(bot))
    assert deleted_ids(bot).isdisjoint({1200, 1201, 1202, 1203, 1204})
    first_delete = next(i for i, (method, _) in enumerate(bot.history)
                        if method.__api_method__ == "deleteMessages")
    progress_ack = next(i for i, (method, _) in enumerate(bot.history)
                        if "Создаём ваш образ" in getattr(method, "text", ""))
    assert first_delete < progress_ack


@pytest.mark.parametrize("failure", ["document", "delete"])
async def test_result_cleanup_failures_do_not_regenerate_or_change_quota(flow, failure):
    _, bot, store, media, service, provider = flow
    await feed(flow, callback="preset:hair")
    await feed(flow, photo=True)
    await feed(flow, "Каре")
    job = store.jobs(1)[0]
    assert await service.process(job["id"])
    assert store.job(job["id"])["status"] == "generated" and deleted_ids(bot) == set()
    old_steps = step_ids(bot)
    await feed(flow, callback="preset:glasses")
    newer_steps = step_ids(bot) - old_steps
    assert newer_steps
    calls, wallet = provider.calls, service.wallet(1)
    bot.fail_document, bot.fail_delete = failure == "document", failure == "delete"
    await feed(flow, callback="result:" + job["id"])
    assert store.job(job["id"])["status"] == ("generated" if failure == "document" else "delivered")
    assert provider.calls == calls and service.wallet(1) == wallet
    assert media.path(store.job(job["id"])["result"]).is_file()
    assert service.reusable_inputs(1, 1)
    if failure == "document":
        assert deleted_ids(bot) == set()
    else:
        assert deleted_ids(bot) == old_steps
    bot.fail_document = bot.fail_delete = False
    await feed(flow, callback="result:" + job["id"])
    assert store.job(job["id"])["status"] == "delivered" and deleted_ids(bot) == old_steps
    assert newer_steps.isdisjoint(deleted_ids(bot))
    assert provider.calls == calls and service.wallet(1) == wallet


@pytest.mark.parametrize("preset,example", [
    ("hair", "Каре до плеч"), ("clothes", "Бежевый тренч"),
    ("glasses", "Тонкая чёрная оправа"), ("background", "Парк"),
    ("enhance", "без ретуши лица"), ("merge", "Мы вместе"),
])
async def test_step_two_example_is_specific_and_copy_does_not_submit(flow, preset, example):
    await feed(flow, callback="preset:" + preset)
    sent = await feed(flow, photo=True)
    if preset == "merge":
        sent = await feed(flow, photo=True)
    copy = [button for method in sent
            for row in getattr(getattr(method, "reply_markup", None), "inline_keyboard", [])
            for button in row if button.copy_text]
    assert len(copy) == (3 if preset == "hair" else 1) and example in copy[0].copy_text.text
    assert copy[0].copy_text.text in texts(sent) and copy[0].callback_data is None
    assert copy[0].style is None and flow[2].jobs(1) == [] and flow[5].calls == 0
    assert flow[4].wallet(1) == (1, 0)
    if preset == "hair":
        assert "от 1 до 12" in texts(sent) and "Коллаж — 1 попытка" in texts(sent)
        assert "может отклониться" in texts(sent)
        assert "сетка 3×3 из 9" in copy[1].copy_text.text
        assert "сетка 3×4 из 12" in copy[2].copy_text.text
        assert all(button.callback_data is None and button.style is None for button in copy)


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


async def test_admin_command_is_registered_in_actual_bot_dispatcher(tmp_path):
    ui = build_ui(tmp_path, admin_user_id=1)
    ui[2].record_visit(99, "private_stats_handle")
    sent = await feed(ui, "/admin")
    assert "private_stats_handle" in texts(sent)
    assert "Неизвестная команда" not in texts(sent)
    denied = await feed(ui, "/admin", user=2)
    assert "private_stats_handle" not in texts(denied)


@pytest.mark.parametrize("preset,provider_calls,option", [
    ("document_original", 0, "original"), ("document", 1, "suit"), ("document", 0, "original"),
])
async def test_document_options_create_one_png_sheet_and_stale_button_cannot_reuse_trial(
    flow, preset, provider_calls, option,
):
    _, _, store, media, service, provider = flow
    await feed(flow, callback="preset:" + preset)
    uploaded = await feed(flow, photo=True)
    callback = button_data(uploaded, "docopt:")
    assert callback is not None and provider.calls == 0 and not store.jobs(1)
    callback = callback.rsplit(":", 1)[0] + ":" + option
    sent = await feed(flow, callback=callback)
    assert "Шаг 3" in texts(sent) and len(store.jobs(1)) == 1
    job = store.jobs(1)[0]
    assert await service.process(job["id"])
    assert provider.calls == provider_calls and service.wallet(1) == (0, 0)
    with Image.open(media.path(store.job(job["id"])["result"])) as sheet:
        assert sheet.format == "PNG" and sheet.size == (826, 1062)
    downloaded = await feed(flow, "/result " + job["id"])
    documents = [method for method in downloaded if method.__api_method__ == "sendDocument"]
    assert documents and documents[0].document.filename.endswith(".png")
    assert "35×45" in texts(downloaded)
    await feed(flow, callback=callback)
    assert len(store.jobs(1)) == 1 and service.wallet(1) == (0, 0)
    assert store.admin_stats()["generated_count"] == 1
