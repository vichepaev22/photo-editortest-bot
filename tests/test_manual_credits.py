import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from image_studio.media import Media
from image_studio.provider import MockProvider
from image_studio.service import Service
from image_studio.store import DomainError, Store

GRANT = "a" * 32


def setup(tmp_path):
    store, media = Store(tmp_path / "manual.sqlite3"), Media(tmp_path)
    provider = MockProvider()
    service = Service(store, media, provider, trial_access=True)
    image = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(image, "JPEG")
    photos = [media.save(10, image.getvalue()), media.save(10, image.getvalue())]
    return store, service, photos, provider


def finish(store, job):
    assert store.claim(job)
    store.finish(job, f"10/{job}.jpg", {}, "offline")


def test_future_user_gets_base_plus_ten_finite_results_and_no_free_renewal(tmp_path):
    store, service, photos, provider = setup(tmp_path)
    assert store.grant_manual(10, 10, GRANT)
    assert service.wallet(10) == (11, 0)
    with pytest.raises(DomainError, match="^consent_required$"):
        service.submit(10, "before-consent", "hair", photos[:1], "test")
    store.consent(10)
    assert not store.grant_trial(10) and not store.grant_demo(10)
    for index in range(11):
        preset, inputs = ("hair", photos[:1]) if index % 2 else ("merge", photos)
        job = service.submit(10, str(index), preset, inputs, "test")
        assert store.job(job)["cost"] == 1 and store.job(job)["trial"] == 0
        assert not store.job(job)["quota_exempt"]
        finish(store, job)
        assert service.submit(10, str(index), preset, inputs, "test") == job
        assert service.wallet(10) == (10 - index, 0)
    store.revoke_consent(10)
    store.consent(10)
    restarted = Service(Store(store.path), service.media, provider, trial_access=True)
    assert restarted.wallet(10) == (0, 0)
    assert not store.grant_trial(10) and not store.grant_demo(10)
    assert not store.grant_manual(10, 10, GRANT)
    with pytest.raises(DomainError, match="^insufficient_credits$"):
        restarted.submit(10, "twelfth", "hair", photos[:1], "test")
    store.consent(11)
    assert restarted.wallet(11) == (1, 0) and not store.has_manual_access(11)
    assert provider.calls == 0


def test_manual_grant_keeps_trial_history_replay_and_failure_release(tmp_path):
    store, service, photos, _ = setup(tmp_path)
    store.consent(10)
    first = service.submit(10, "trial-first", "hair", photos[:1], "test")
    finish(store, first)
    assert store.grant_manual(10, 10, GRANT)
    assert service.submit(10, "trial-first", "hair", photos[:1], "test") == first
    assert service.wallet(10) == (10, 0)
    new = service.submit(10, "manual-fail", "merge", photos, "test")
    assert service.wallet(10) == (10, 1)
    store.fail(new, "provider_rejected")
    store.fail(new, "provider_rejected")
    assert service.wallet(10) == (10, 0)
    with store.tx() as connection:
        assert connection.execute("SELECT trial_used FROM users WHERE id=10").fetchone()[0] == 1


def test_manual_grant_is_idempotent_and_changed_replay_rejected(tmp_path):
    store, _, _, _ = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: store.grant_manual(10, 10, GRANT), range(2)))
    assert sorted(result) == [False, True] and store.wallet(10) == (11, 0)
    for user, amount in ((11, 10), (10, 9)):
        with pytest.raises(DomainError, match="^manual_grant_mismatch$"):
            store.grant_manual(user, amount, GRANT)
    with store.tx() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events WHERE kind='manual_grant'").fetchone()[0] == 1


def test_manual_grant_does_not_change_active_trial(tmp_path):
    store, service, photos, _ = setup(tmp_path)
    store.consent(10)
    service.submit(10, "active", "hair", photos[:1], "test")
    with pytest.raises(DomainError, match="^already_active$"):
        store.grant_manual(10, 10, GRANT)
    assert not store.has_manual_access(10) and store.wallet(10) == (0, 0)


@pytest.mark.parametrize("grant_during_reserve", [False, True])
def test_trial_conversion_between_mode_check_and_reserve_never_duplicates_base(tmp_path, monkeypatch,
                                                                            grant_during_reserve):
    store, service, photos, provider = setup(tmp_path)
    store.consent(10)
    original = store.reserve
    switched = False

    def converting_reserve(*args, **kwargs):
        nonlocal switched
        if not switched:
            switched = True
            if grant_during_reserve:
                store.grant_trial(10)
            assert store.grant_channel_bonus(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "reserve", converting_reserve)
    job = service.submit(10, "conversion-race", "hair", photos[:1], "test")
    assert store.job(job)["trial"] == 0
    finish(store, job)
    assert service.wallet(10) == (1, 0)
    assert store.wallet(10, trial=True) == (1, 0)
    assert service.submit(10, "conversion-race", "hair", photos[:1], "test") == job
    assert provider.calls == 0
