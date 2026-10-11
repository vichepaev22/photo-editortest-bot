import io
import os
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import CallbackQuery, Chat, Document, FSInputFile, Message, Update, User
from PIL import Image

from image_studio.bot import StepMessages, build_dispatcher, deliver_result
from image_studio.config import Settings
from image_studio.media import Media
from image_studio.provider import MockProvider
from image_studio.result_delivery import ResultDeliveryDeferred
from image_studio.service import Service
from image_studio.store import DomainError, Store


class DeliveryBot(Bot):
    def __init__(self, bot_id=123456789):
        super().__init__(f"{bot_id}:ABCdefghijklmnopqrstuvwxyz123456789")
        self.documents = []
        self.outcomes = []

    async def __call__(self, method, request_timeout=None):
        if method.__api_method__ == "answerCallbackQuery":
            return True
        if method.__api_method__ == "sendDocument":
            self.documents.append(method)
            if self.outcomes:
                outcome = self.outcomes.pop(0)
                if outcome:
                    raise outcome(method)
            document = Document(file_id=f"file_{self.id}_{len(self.documents)}", file_unique_id="unique")
        else:
            document = None
        return Message(message_id=900 + len(self.documents), date=datetime.now(timezone.utc),
                       chat=Chat(id=int(method.chat_id), type="private"), document=document)


@pytest.fixture
async def ready(tmp_path):
    store, media, provider = Store(tmp_path / "db.sqlite3"), Media(tmp_path), MockProvider()
    store.consent(1)
    store.grant_demo(1)
    data = io.BytesIO()
    Image.new("RGB", (256, 256), "#c8b9a0").save(data, "JPEG")
    source = media.save(1, data.getvalue())
    service = Service(store, media, provider)
    job = service.submit(1, "delivery-test", "hair", [source], "Каре")
    assert await service.process(job)
    return store, media, provider, service, job


async def deliver(ready, bot, *, user=1, steps=None):
    store, media, _, _, job = ready
    path = media.path(store.job(job)["result"])
    await deliver_result(bot, store, steps or StepMessages(store), user, job, path, demo=True)


async def test_first_upload_and_restart_reuses_owned_document_id(ready):
    store, media, provider, service, job = ready
    bot = DeliveryBot()
    wallet = store.wallet(1)
    await deliver(ready, bot)
    assert isinstance(bot.documents[0].document, FSInputFile)
    restarted = Store(store.path)
    await deliver((restarted, media, provider, service, job), bot)
    assert bot.documents[1].document == "file_123456789_1"
    assert store.job(job)["status"] == "delivered"
    assert provider.calls == 1 and store.wallet(1) == wallet
    await bot.session.close()


async def test_other_bot_id_uploads_and_keeps_separate_receipt(ready):
    first, other = DeliveryBot(), DeliveryBot(987654321)
    await deliver(ready, first)
    await deliver(ready, other)
    assert isinstance(other.documents[0].document, FSInputFile)
    await deliver(ready, first)
    await deliver(ready, other)
    assert first.documents[-1].document == "file_123456789_1"
    assert other.documents[-1].document == "file_987654321_1"
    await first.session.close()
    await other.session.close()


@pytest.mark.parametrize("invalid", ["foreign", "revoked", "deleted", "expired"])
async def test_cache_never_bypasses_owner_consent_or_local_retention(ready, invalid):
    store, media, _, _, job = ready
    bot = DeliveryBot()
    await deliver(ready, bot)
    if invalid == "revoked":
        store.revoke_consent(1)
    elif invalid == "deleted":
        media.path(store.job(job)["result"]).unlink()
    elif invalid == "expired":
        old = time.time() - 86401
        os.utime(media.path(store.job(job)["result"]), (old, old))
    with pytest.raises(DomainError):
        await deliver(ready, bot, user=2 if invalid == "foreign" else 1)
    assert len(bot.documents) == 1
    await bot.session.close()


async def test_explicit_invalid_id_falls_back_once_and_refreshes_receipt(ready):
    bot = DeliveryBot()
    await deliver(ready, bot)
    bot.outcomes = [lambda m: TelegramBadRequest(m, "Bad Request: wrong file identifier/HTTP URL specified")]
    await deliver(ready, bot)
    assert bot.documents[1].document == "file_123456789_1"
    assert isinstance(bot.documents[2].document, FSInputFile)
    await deliver(ready, bot)
    assert bot.documents[3].document == "file_123456789_3"
    await bot.session.close()


async def test_unrelated_bad_request_never_reuploads(ready):
    bot = DeliveryBot()
    await deliver(ready, bot)
    bot.outcomes = [lambda m: TelegramBadRequest(m, "Bad Request: chat not found")]
    with pytest.raises(TelegramBadRequest):
        await deliver(ready, bot)
    assert len(bot.documents) == 2 and bot.documents[-1].document == "file_123456789_1"
    await bot.session.close()


@pytest.mark.parametrize("cached", [False, True])
async def test_retry_after_defers_only_explicit_delivery_and_survives_restart(ready, monkeypatch, cached):
    import image_studio.result_delivery as module

    store, media, provider, service, job = ready
    bot = DeliveryBot()
    if cached:
        await deliver(ready, bot)
    before = len(bot.documents)
    clock = time.time()
    monkeypatch.setattr(module, "now", lambda: clock)
    bot.outcomes = [lambda m: TelegramRetryAfter(m, "Too Many Requests", retry_after=12)]
    with pytest.raises(ResultDeliveryDeferred):
        await deliver(ready, bot)
    assert len(bot.documents) == before + 1
    assert store.job(job)["status"] == ("delivered" if cached else "generated")
    restarted = Store(store.path)
    reloaded = (restarted, media, provider, service, job)
    with pytest.raises(ResultDeliveryDeferred):
        await deliver(reloaded, bot)
    assert len(bot.documents) == before + 1
    assert not await service.process(job)
    monkeypatch.setattr(module, "now", lambda: clock + 13)
    wallet = restarted.wallet(1)
    await deliver(reloaded, bot)
    assert len(bot.documents) == before + 2
    assert isinstance(bot.documents[-1].document, str if cached else FSInputFile)
    assert provider.calls == 1 and restarted.wallet(1) == wallet
    await bot.session.close()


async def test_initial_timeout_leaves_generated_without_regeneration_or_step_cleanup(ready):
    store, _, provider, service, job = ready
    bot = DeliveryBot()
    bot.outcomes = [lambda m: TelegramNetworkError(m, "Request timeout")]
    steps = StepMessages(store)
    steps.delivered = AsyncMock()
    with pytest.raises(TelegramNetworkError):
        await deliver(ready, bot, steps=steps)
    assert len(bot.documents) == 1 and store.job(job)["status"] == "generated"
    assert not await service.process(job) and provider.calls == 1
    steps.delivered.assert_not_awaited()
    await bot.session.close()


@pytest.mark.parametrize("callback", [False, True])
async def test_result_navigation_reuses_first_delivery_receipt(ready, callback):
    store, media, provider, service, job = ready
    bot = DeliveryBot()
    await deliver(ready, bot)
    dp = build_dispatcher(Settings(demo_credits=True), store, media, service)
    message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private"),
                      from_user=User(id=1, is_bot=False, first_name="Тест"), text=f"/result {job}")
    update = Update(update_id=1, callback_query=CallbackQuery(
        id="result", from_user=message.from_user, chat_instance="test", message=message, data=f"result:{job}",
    )) if callback else Update(update_id=1, message=message)
    await dp.feed_update(bot, update)
    assert bot.documents[-1].document == "file_123456789_1"
    assert "Хранение — 24 часа" in bot.documents[-1].caption
    assert provider.calls == 1
    await bot.session.close()


async def test_document_sheet_upload_keeps_png_filename_and_caption(ready):
    store, media, provider, service, job = ready
    sheet = service.submit(1, "document-sheet", "document_original", service.reusable_inputs(1, 1),
                           "Исходное фото")
    assert await service.process(sheet)
    bot = DeliveryBot()
    await deliver((store, media, provider, service, sheet), bot)
    assert bot.documents[0].document.filename == "Образ · 4 фото 35x45 мм.png"
    assert "PNG · 300 DPI" in bot.documents[0].caption
    assert provider.calls == 1
    await bot.session.close()
