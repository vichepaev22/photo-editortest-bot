import io
import sqlite3

import pytest
from PIL import Image

from image_studio.config import Settings
from image_studio.media import Media, normalize
from image_studio.provider import MockProvider, ProviderError
from image_studio.service import Service
from image_studio.store import DomainError, Store


def user_row(store, user):
    with sqlite3.connect(store.path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute("SELECT * FROM users WHERE id=?", (user,)).fetchone()


@pytest.fixture
def trial(tmp_path):
    store, media = Store(tmp_path / "db.sqlite3"), Media(tmp_path)
    store.consent(1)
    store.grant_pilot(1, 3)
    photos = []
    for color in ("white", "blue"):
        data = io.BytesIO()
        Image.new("RGB", (256, 256), color).save(data, "JPEG")
        photos.append(media.save(1, normalize(data.getvalue())))
    provider = MockProvider()
    return store, media, provider, photos


@pytest.mark.parametrize("preset", ["hair", "clothes", "glasses", "background", "enhance", "merge", "document"])
async def test_one_result_exhausts_trial_and_blocks_regeneration_without_spending_paid_wallet(trial, preset):
    store, media, provider, photos = trial
    service = Service(store, media, provider, trial_access=True)
    assert service.wallet(1) == (1, 0)
    inputs = photos if preset == "merge" else photos[:1]
    job = service.submit(1, "first", preset, inputs, "test")
    assert service.submit(1, "first", preset, inputs, "test") == job
    assert store.job(job)["cost"] == 1
    assert await service.process(job)
    assert not await service.process(job)
    assert media.path(store.result(1, job)["result"]).is_file()
    with pytest.raises(DomainError, match="^trial_exhausted$"):
        service.submit(1, "variation", preset, inputs, "test")
    assert provider.calls == 1
    assert service.wallet(1) == (0, 0)
    assert store.wallet(1) == (3, 0)
    assert user_row(store, 1)["spent"] == 0


async def test_failure_restores_trial_and_delivery_failure_never_generates_again(trial):
    store, media, provider, photos = trial

    class Broken:
        async def edit(self, images, prompt):
            raise ProviderError("provider_rejected")

    failed = Service(store, media, Broken(), trial_access=True)
    job = failed.submit(1, "fail", "hair", photos[:1], "test")
    assert failed.wallet(1) == (1, 1)
    assert not await failed.process(job)
    assert failed.wallet(1) == (1, 0)

    async def no_delivery(*args):
        raise OSError("network")

    service = Service(store, media, provider, no_delivery, trial_access=True)
    job = service.submit(1, "success", "glasses", photos[:1], "test")
    assert await service.process(job)
    assert not await service.process(job)
    assert provider.calls == 1
    assert service.wallet(1) == (0, 0)
    assert store.wallet(1) == (3, 0)
    assert store.result(1, job)["result"]


def test_interruption_and_restart_preserve_trial_reservation(trial):
    store, media, provider, photos = trial
    service = Service(store, media, provider, trial_access=True)
    job = service.submit(1, "interrupted", "hair", photos[:1], "test")
    assert store.claim(job)
    store.recover()
    restarted = Service(Store(store.path), media, provider, trial_access=True)
    assert restarted.wallet(1) == (1, 1)
    assert restarted.store.job(job)["status"] == "review"
    with pytest.raises(DomainError, match="^already_active$"):
        restarted.submit(1, "next", "hair", photos[:1], "test")
    restarted.store.fail(job, "released_by_support")
    assert restarted.wallet(1) == (1, 0)
    assert provider.calls == 0


async def test_delete_reconsent_and_new_service_do_not_reset_used_trial(trial):
    store, media, provider, photos = trial
    service = Service(store, media, provider, trial_access=True)
    assert await service.process(service.submit(1, "first", "hair", photos[:1], "test"))
    service.delete(1)
    assert not media.path(photos[0]).exists()
    store.consent(1)
    restarted = Service(Store(store.path), media, provider, trial_access=True)
    assert restarted.wallet(1) == (0, 0)
    assert store.wallet(1) == (3, 0)


def test_trial_claim_requires_consent_and_is_per_user(trial):
    store, media, provider, _ = trial
    service = Service(store, media, provider, trial_access=True)
    assert service.wallet(2) == (0, 0)
    assert not user_row(store, 2)["trial_granted"]
    store.consent(2)
    assert service.wallet(2) == (1, 0)
    assert service.wallet(1) == (1, 0)
    assert store.wallet(2) == (0, 0)


async def test_disabled_trial_retains_two_credit_merge(trial):
    store, media, provider, photos = trial
    service = Service(store, media, provider)
    assert service.cost("merge") == 2
    job = service.submit(1, "paid", "merge", photos, "test")
    assert await service.process(job)
    assert store.wallet(1) == (1, 0)
    assert not user_row(store, 1)["trial_granted"]


def test_trial_settings_defaults_and_valid_openai_mode():
    assert Settings().validate().trial_access is False
    assert Settings(image_provider="openai", openai_key="test", trial_access=True).validate().trial_access


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"image_provider": "mock"}, "trial_requires_openai"),
        ({"image_provider": "openai", "openai_key": "test", "billing_enabled": True}, "trial_requires_billing_disabled"),
    ],
)
def test_trial_rejects_mock_and_enabled_billing(kwargs, code):
    with pytest.raises(ValueError, match="^" + code + "$"):
        Settings(trial_access=True, **kwargs).validate()


def test_trial_settings_flag_loaded_from_environment(monkeypatch):
    import image_studio.config as module

    monkeypatch.setattr(module, "load_dotenv", lambda: None)
    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("ENABLE_TRIAL_ACCESS", "true")
    monkeypatch.setenv("BILLING_ENABLED", "false")
    assert Settings.from_env().trial_access
