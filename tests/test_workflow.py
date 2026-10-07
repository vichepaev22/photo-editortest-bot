import base64
import io
import json
from datetime import datetime, timezone

import httpx
import pytest
from openai import AsyncOpenAI
from PIL import Image

from image_studio.bot import build_dispatcher
from image_studio.config import Settings
from image_studio.media import Media, normalize
from image_studio.provider import MockProvider, OpenAIProvider, ProviderError
from image_studio.service import Service
from image_studio.store import DomainError, Store


async def test_duplicate_telegram_photo_is_not_second_merge_input(workflow):
    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User

    store, media, provider, _ = workflow

    class OfflineBot(Bot):
        downloads = 0

        async def __call__(self, method, request_timeout=None):
            if method.__api_method__ == "answerCallbackQuery":
                return True
            return Message(message_id=500, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private"))

        async def download(self, file, destination=None, **kwargs):
            self.downloads += 1
            destination.write(fixture_image())
            return destination

    bot = OfflineBot("123456789:ABCdefghijklmnopqrstuvwxyz123456789")
    dp = build_dispatcher(Settings(), store, media, Service(store, media, provider))
    user = User(id=1, is_bot=False, first_name="Test")
    message = Message(
        message_id=123,
        date=datetime.now(timezone.utc),
        chat=Chat(id=1, type="private"),
        from_user=user,
        photo=[PhotoSize(file_id="test", file_unique_id="test", width=256, height=256, file_size=1000)],
    )
    await dp.feed_update(
        bot,
        Update(
            update_id=100,
            callback_query=CallbackQuery(
                id="choose", from_user=user, chat_instance="test", message=message, data="preset:merge"
            ),
        ),
    )
    await dp.feed_update(bot, Update(update_id=101, message=message))
    await dp.feed_update(bot, Update(update_id=101, message=message))
    assert bot.downloads == 1
    await bot.session.close()


def fixture_image():
    buffer = io.BytesIO()
    image = Image.new("RGB", (256, 256), "white")
    image.save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.mark.parametrize("bypass", [False, True])
async def test_telegram_uses_existing_proxy_and_respects_bypass(monkeypatch, bypass):
    import image_studio.bot as module

    monkeypatch.setattr(module, "getproxies", lambda: {"https": "http://proxy.example:8080"}, raising=False)
    monkeypatch.setattr(module, "proxy_bypass", lambda host: bypass, raising=False)
    session = module.make_telegram_session()
    assert session.proxy == (None if bypass else "http://proxy.example:8080")
    await session.close()


@pytest.fixture
def workflow(tmp_path):
    store, media = Store(tmp_path / "db.sqlite3"), Media(tmp_path)
    store.consent(1)
    store.grant_demo(1)
    photo = media.save(1, normalize(fixture_image()))
    provider = MockProvider()
    provider.calls = 0
    return store, media, provider, photo


async def test_duplicate_and_delivery_failure_never_regenerate(workflow):
    store, media, provider, photo = workflow

    async def broken_delivery(*args):
        raise OSError("network")

    service = Service(store, media, provider, broken_delivery)
    job = service.submit(1, "key", "hair", [photo], "short hair")
    assert service.submit(1, "key", "hair", [photo], "short hair") == job
    assert await service.process(job)
    assert not await service.process(job)
    assert provider.calls == 1
    assert store.wallet(1) == (2, 0)
    assert media.path(store.result(1, job)["result"]).is_file()


async def test_provider_failure_returns_reservation(workflow):
    store, media, _, photo = workflow

    class Broken:
        async def edit(self, images, prompt):
            raise ProviderError("provider_rejected")

    service = Service(store, media, Broken())
    job = service.submit(1, "key", "hair", [photo], "short hair")
    assert not await service.process(job)
    assert store.wallet(1) == (3, 0)
    assert not await service.process(job)


def test_merge_and_cross_user_inputs(workflow):
    store, media, provider, photo = workflow
    service = Service(store, media, provider)
    with pytest.raises(DomainError):
        service.submit(1, "key", "merge", [photo], "together")
    store.consent(2)
    store.grant_demo(2)
    with pytest.raises(DomainError):
        service.submit(2, "key", "hair", [photo], "short")
    assert store.wallet(2) == (3, 0)


def test_normalize_strips_exif_and_rejects_nonimage():
    buffer = io.BytesIO()
    image = Image.new("RGB", (2500, 2500), "white")
    exif = image.getexif()
    exif[0x010E] = "private"
    image.save(buffer, "JPEG", exif=exif)
    output = normalize(buffer.getvalue())
    with Image.open(io.BytesIO(output)) as result:
        assert not result.getexif()
        assert max(result.size) == 2048
    with pytest.raises(ValueError):
        normalize(b"invalid")


def test_delete_and_path_containment(workflow):
    store, media, provider, photo = workflow
    service = Service(store, media, provider)
    with pytest.raises(ValueError):
        media.path("../../outside")
    service.delete(1)
    assert not media.path(photo).exists()
    assert not store.has_consent(1)
    assert store.wallet(1) == (3, 0)


async def test_real_sdk_multipart_contract_and_usage(workflow):
    _, media, _, photo = workflow
    requests = []

    async def handle(request):
        body = await request.aread()
        requests.append(body)
        assert request.url.path == "/v1/images/edits"
        assert b"gpt-image-2.5-flare-2026-09-08" in body
        assert b'filename="' in body
        assert b"input_fidelity" not in body
        usage = {
            "input_tokens": 2200,
            "output_tokens": 1500,
            "total_tokens": 3700,
            "input_tokens_details": {"text_tokens": 200, "image_tokens": 2000},
        }
        return httpx.Response(
            200,
            headers={"x-request-id": "req_contract"},
            json={
                "created": 1,
                "data": [{"b64_json": base64.b64encode(fixture_image()).decode()}],
                "usage": usage,
            },
        )

    client = AsyncOpenAI(
        api_key="test-not-real",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    provider = OpenAIProvider("", "gpt-image-2.5-flare-2026-09-08", client=client)
    result = await provider.edit([media.path(photo)], "change hair")
    assert result.usage["input_tokens_details"]["image_tokens"] == 2000
    assert result.request_id == "req_contract"
    assert len(requests) == 1
    await provider.close()


async def test_sdk_refusal_one_attempt_and_safe_error(workflow):
    _, media, _, photo = workflow
    count = 0

    async def handle(request):
        nonlocal count
        count += 1
        return httpx.Response(
            400, json={"error": {"message": "private prompt", "code": "moderation_blocked"}}
        )

    client = AsyncOpenAI(
        api_key="test", max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    provider = OpenAIProvider("", "gpt-image-2.5-flare", client=client)
    with pytest.raises(ProviderError, match="^provider_rejected$"):
        await provider.edit([media.path(photo)], "secret")
    assert count == 1
    await provider.close()


@pytest.mark.parametrize(
    "http_status,code,expected",
    [
        (401, "invalid_api_key", "provider_authentication"),
        (429, "insufficient_quota", "provider_quota"),
        (429, "rate_limit_exceeded", "provider_rate_limit"),
    ],
)
async def test_sdk_account_errors_are_safe_and_not_retried(workflow, http_status, code, expected):
    _, media, _, photo = workflow
    calls = []

    async def handle(request):
        calls.append(request)
        return httpx.Response(http_status, json={"error": {"message": "secret account detail", "code": code}})

    client = AsyncOpenAI(
        api_key="test", max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    provider = OpenAIProvider("", "gpt-image-2.5-flare", client=client)
    with pytest.raises(ProviderError, match="^" + expected + "$"):
        await provider.edit([media.path(photo)], "private prompt")
    assert len(calls) == 1
    await provider.close()


def test_dispatcher_and_durable_payload(workflow):
    store, media, provider, photo = workflow
    service = Service(store, media, provider)
    dp = build_dispatcher(Settings(), store, media, service)
    assert "message" in dp.resolve_used_update_types()
    assert "callback_query" in dp.resolve_used_update_types()
    job = service.submit(1, "key", "hair", [photo], "short")
    payload = json.loads(media.path(f"1/{job}.json").read_text())
    assert payload["inputs"] == [photo]
    store.recover()
    assert store.job(job)["status"] == "queued"
