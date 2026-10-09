import io
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from image_studio.config import Settings
from image_studio.media import Media
from image_studio.provider import MockProvider, ProviderError
from image_studio.service import INPUT_TTL, Service
from image_studio.store import DomainError, Store


@pytest.fixture
def setup(tmp_path):
    store, media, provider = Store(tmp_path / "db.sqlite3"), Media(tmp_path), MockProvider()
    photos = {}
    for user in (1, 2):
        store.consent(user)
        photos[user] = []
        for color in ("white", "blue"):
            data = io.BytesIO()
            Image.new("RGB", (256, 256), color).save(data, "JPEG")
            photos[user].append(media.save(user, data.getvalue()))
    return store, media, provider, photos


def counters(store, user=1):
    with store.tx() as connection:
        row = connection.execute(
            "SELECT balance,reserved,spent,demo_used,trial_granted,trial_used,trial_reserved "
            "FROM users WHERE id=?", (user,),
        ).fetchone()
        return dict(row)


@pytest.mark.parametrize("already_exhausted", [False, True])
async def test_owner_can_complete_five_without_grant_or_debit_others_keep_one(setup, already_exhausted):
    store, media, provider, photos = setup
    store.grant_pilot(1, 2)
    if already_exhausted:
        with store.tx() as connection:
            connection.execute("UPDATE users SET trial_granted=1,trial_used=3 WHERE id=1")
    service = Service(store, media, provider, trial_access=True, unlimited_user_id=1)
    before = counters(store)
    assert service.wallet(1) == (0, 0)
    for index in range(5):
        preset = "merge" if index == 0 else "hair"
        job = service.submit(1, f"owner-{index}", preset, photos[1][:2 if index == 0 else 1], "test")
        assert store.job(job)["quota_exempt"] == 1 and store.job(job)["cost"] == 1
        assert store.job(job)["trial"] == 1
        assert await service.process(job)
        assert counters(store) == before
    assert service.wallet(2) == (1, 0)
    job = service.submit(2, "other-first", "hair", photos[2][:1], "test")
    assert store.job(job)["quota_exempt"] == 0
    assert await service.process(job)
    with pytest.raises(DomainError, match="^trial_exhausted$"):
        service.submit(2, "other-second", "hair", photos[2][:1], "test")
    assert service.wallet(2) == (0, 0) and counters(store, 2)["trial_used"] == 1
    assert counters(store) == before and provider.calls == 6
    assert store.admin_stats()["generated_count"] == 6
    assert [row["generated_count"] for row in store.admin_stats()["users"]] == [5, 1]
    with store.tx() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events WHERE kind='trial_grant' AND ref='1'").fetchone()[0] == 0


async def test_owner_gate_independent_of_trial_preserves_two_credit_merge(setup):
    store, media, provider, photos = setup
    service = Service(store, media, provider, unlimited_user_id=1)
    before = counters(store)
    assert service.wallet(1) == (0, 0) and service.cost("merge") == 2
    job = service.submit(1, "merge", "merge", photos[1], "test")
    assert (store.job(job)["trial"], store.job(job)["cost"], store.job(job)["quota_exempt"]) == (0, 2, 1)
    assert await service.process(job)
    assert counters(store) == before
    with pytest.raises(DomainError, match="^insufficient_credits$"):
        service.submit(2, "other", "hair", photos[2][:1], "test")


def test_unlimited_matching_rejects_coercion_other_actor_and_disabled_configuration(setup):
    store, media, provider, _ = setup
    service = Service(store, media, provider, unlimited_user_id=1)
    assert service.is_unlimited(1)
    for user in (0, -1, True, False, "1", 1.0, None, 2**52, 2):
        assert not service.is_unlimited(user)
    for configured in (0, -1, True, False, "1", 1.0, None, 2**52):
        assert not Service(store, media, provider, unlimited_user_id=configured).is_unlimited(1)


def test_exempt_reserve_requires_real_actor_boolean_flag_and_positive_integer_cost(setup):
    store, _, _, _ = setup
    before = counters(store)
    for user in (0, -1, True, "1", 1.0, None, 2**52):
        with pytest.raises(DomainError, match="^invalid_trial_user$"):
            store.reserve(user, "bad-user", "hair", 1, quota_exempt=True)
    for flag in (0, 1, "true", None):
        with pytest.raises(DomainError, match="^invalid_job$"):
            store.reserve(1, "bad-flag", "hair", 1, quota_exempt=flag)
    for cost in (0, -1, True, 1.0):
        with pytest.raises(DomainError, match="^invalid_job$"):
            store.reserve(1, "bad-cost", "hair", cost, quota_exempt=True)
    assert not store.jobs() and counters(store) == before


def test_settings_default_disabled_and_owner_gate_requires_valid_admin():
    assert Settings().validate().owner_unlimited_testing is False
    assert Settings(owner_unlimited_testing=True, admin_user_id=1).validate().owner_unlimited_testing
    with pytest.raises(ValueError, match="^owner_unlimited_requires_admin$"):
        Settings(owner_unlimited_testing=True).validate()
    for user in (-1, True, "1", 1.0, 2**52):
        with pytest.raises(ValueError, match="^invalid_admin_user_id$"):
            Settings(owner_unlimited_testing=True, admin_user_id=user).validate()
    with pytest.raises(ValueError, match="^invalid_owner_unlimited_testing$"):
        Settings(owner_unlimited_testing="true", admin_user_id=1).validate()


@pytest.mark.parametrize("enabled", [False, True])
def test_owner_setting_loaded_from_environment_without_enabling_trial(monkeypatch, enabled):
    monkeypatch.setattr("image_studio.config.load_dotenv", lambda: None)
    for key, value in {
        "IMAGE_PROVIDER": "mock", "ENABLE_TRIAL_ACCESS": "false", "BILLING_ENABLED": "false",
        "ADMIN_TELEGRAM_ID": "1", "ENABLE_OWNER_UNLIMITED_TESTING": str(enabled).lower(),
    }.items():
        monkeypatch.setenv(key, value)
    settings = Settings.from_env()
    assert settings.owner_unlimited_testing is enabled and settings.trial_access is False


def test_exemption_keeps_consent_refund_lock_and_active_guards_without_grant(setup):
    store, media, provider, photos = setup
    service = Service(store, media, provider, trial_access=True, unlimited_user_id=1)
    before = counters(store)
    store.revoke_consent(1)
    assert service.wallet(1) == (0, 0)
    with pytest.raises(DomainError, match="^consent_required$"):
        service.submit(1, "no-consent", "hair", photos[1][:1], "test")
    store.consent(1)
    with store.tx() as connection:
        connection.execute("UPDATE users SET refund_lock=1 WHERE id=1")
    with pytest.raises(DomainError, match="^already_active$"):
        service.submit(1, "refund-lock", "hair", photos[1][:1], "test")
    with store.tx() as connection:
        connection.execute("UPDATE users SET refund_lock=0 WHERE id=1")
    job = service.submit(1, "active", "hair", photos[1][:1], "test")
    assert service.submit(1, "active", "hair", photos[1][:1], "test") == job
    for state in ("queued", "running", "review"):
        with pytest.raises(DomainError, match="^already_active$"):
            service.submit(1, "next", "hair", photos[1][:1], "test")
        with pytest.raises(DomainError, match="^already_active$"):
            store.revoke_consent(1)
        if state == "queued":
            assert store.claim(job)
        elif state == "running":
            store.recover()
    assert counters(store) == before and provider.calls == 0


def test_exempt_concurrent_new_requests_still_reserve_only_one_active_job(setup):
    store, media, provider, photos = setup
    service = Service(store, media, provider, trial_access=True, unlimited_user_id=1)
    before = counters(store)

    def submit(index):
        try:
            return service.submit(1, f"concurrent-{index}", "hair", photos[1][:1], "test")
        except DomainError as error:
            assert str(error) == "already_active"
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(submit, range(8)))
    assert sum(job is not None for job in jobs) == 1 and len(store.jobs()) == 1
    assert counters(store) == before and provider.calls == 0


async def test_exempt_failure_delivery_and_repeated_finish_keep_counters_and_counts(setup):
    store, media, provider, photos = setup

    class Broken:
        async def edit(self, images, prompt):
            raise ProviderError("offline_rejected")

    before = counters(store)
    broken = Service(store, media, Broken(), trial_access=True, unlimited_user_id=1)
    failed = broken.submit(1, "failure", "hair", photos[1][:1], "test")
    assert not await broken.process(failed)
    store.fail(failed, "repeat")
    assert counters(store) == before and store.admin_stats()["generated_count"] == 0

    async def no_delivery(*args):
        raise OSError("offline")

    service = Service(store, media, provider, no_delivery, trial_access=True, unlimited_user_id=1)
    job = service.submit(1, "success", "hair", photos[1][:1], "test")
    assert await service.process(job) and not await service.process(job)
    assert store.job(job)["status"] == "generated"
    with pytest.raises(DomainError, match="^result_unavailable$"):
        store.result(2, job)
    store.finish(job, "replacement.jpg", {}, None)
    store.delivered(job)
    store.delivered(job)
    store.fail(job, "late_failure")
    assert store.job(job)["status"] == "delivered"
    assert provider.calls == 1 and counters(store) == before and store.admin_stats()["generated_count"] == 1


def test_exempt_review_persists_after_restart_and_disabled_gate(setup):
    store, media, provider, photos = setup
    service = Service(store, media, provider, trial_access=True, unlimited_user_id=1)
    before = counters(store)
    job = service.submit(1, "interrupted", "hair", photos[1][:1], "test")
    assert store.claim(job)
    store.recover()
    reopened = Store(store.path)
    disabled = Service(reopened, media, provider, trial_access=True)
    assert disabled.submit(1, "interrupted", "hair", photos[1][:1], "test") == job
    assert reopened.job(job)["quota_exempt"] == 1 and reopened.job(job)["status"] == "review"
    with pytest.raises(DomainError, match="^already_active$"):
        disabled.submit(1, "new", "hair", photos[1][:1], "test")
    reopened.fail(job, "manual_release")
    reopened.fail(job, "repeat_release")
    assert counters(reopened) == before and reopened.job(job)["status"] == "failed" and provider.calls == 0


def test_owner_exemption_does_not_bypass_input_ownership_or_ttl(setup):
    store, media, provider, photos = setup
    service = Service(store, media, provider, trial_access=True, unlimited_user_id=1)
    with pytest.raises(DomainError, match="^invalid_inputs$"):
        service.submit(1, "foreign", "hair", photos[2][:1], "test")
    path = media.path(photos[1][0])
    expired = path.stat().st_mtime - INPUT_TTL - 1
    os.utime(path, (expired, expired))
    with pytest.raises(DomainError, match="^invalid_inputs$"):
        service.submit(1, "expired", "hair", photos[1][:1], "test")
    assert not store.jobs() and provider.calls == 0


@pytest.mark.parametrize("trial", [False, True])
async def test_exempt_job_replays_and_settles_after_gate_off_without_granting_trial(setup, trial):
    store, media, provider, photos = setup
    enabled = Service(store, media, provider, trial_access=trial, unlimited_user_id=1)
    before = counters(store)
    job = enabled.submit(1, "exempt", "hair", photos[1][:1], "test")
    disabled = Service(Store(store.path), media, provider, trial_access=trial)
    assert disabled.submit(1, "exempt", "hair", photos[1][:1], "test") == job
    assert counters(disabled.store) == before and disabled.store.job(job)["quota_exempt"] == 1
    assert await disabled.process(job)
    assert counters(disabled.store) == before
    disabled.store.revoke_consent(1)
    assert disabled.submit(1, "exempt", "hair", photos[1][:1], "test") == job
    assert not await disabled.process(job) and provider.calls == 1


@pytest.mark.parametrize("trial", [False, True])
async def test_normal_job_replays_and_settles_normally_after_gate_on(setup, trial):
    store, media, provider, photos = setup
    if not trial:
        store.grant_pilot(1, 3)
    normal = Service(store, media, provider, trial_access=trial)
    job = normal.submit(1, "normal", "hair", photos[1][:1], "test")
    enabled = Service(store, media, provider, trial_access=trial, unlimited_user_id=1)
    assert enabled.submit(1, "normal", "hair", photos[1][:1], "test") == job
    assert store.job(job)["quota_exempt"] == 0
    with pytest.raises(DomainError, match="^request_mismatch$"):
        enabled.submit(1, "normal", "glasses", photos[1][:1], "test")
    with pytest.raises(DomainError, match="^request_mismatch$"):
        store.reserve(1, "normal", "hair", 1, trial=not trial, quota_exempt=True)
    assert await enabled.process(job)
    assert store.wallet(1, trial=trial) == (0 if trial else 2, 0)
    row = counters(store)
    assert row["trial_used"] == int(trial) and row["spent"] == int(not trial)
    store.revoke_consent(1)
    assert enabled.submit(1, "normal", "hair", photos[1][:1], "test") == job


def test_serialized_additive_migration_preserves_old_rows_and_old_job_settlement(setup):
    store, media, _, _ = setup
    store.grant_pilot(1, 7)
    order = store.invoice(1, "offline", 100, 2)
    store.payment(1, order, "RUB", 100, "offline-test")
    paid = store.reserve(1, "legacy-paid", "merge", 2)
    assert store.claim(paid)
    store.grant_trial(2)
    trial = store.reserve(2, "legacy-trial", "hair", 1, trial=True)
    files = {path: path.read_bytes() for path in media.root.rglob("*") if path.is_file()}
    with store.tx() as connection:
        connection.execute("ALTER TABLE jobs DROP COLUMN quota_exempt")
        tables = ("users", "jobs", "events", "invoices", "payments")
        columns = {
            table: [row["name"] for row in connection.execute(f"PRAGMA table_info({table})")]
            for table in tables
        }
        before = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                  for table in tables}
    with ThreadPoolExecutor(max_workers=4) as pool:
        reopened = list(pool.map(lambda _: Store(store.path), range(4)))
    migrated = reopened[0]
    with migrated.tx() as connection:
        for table in tables:
            rows = [tuple(row) for row in connection.execute(f"SELECT {','.join(columns[table])} FROM {table}")]
            assert rows == before[table], table
        assert [row[0] for row in connection.execute("SELECT quota_exempt FROM jobs")] == [0, 0]
    assert {path: path.read_bytes() for path in media.root.rglob("*") if path.is_file()} == files
    migrated.finish(paid, "legacy-result.jpg", {}, None)
    migrated.fail(trial, "legacy-release")
    assert migrated.wallet(1) == (7, 0) and counters(migrated)["spent"] == 2
    assert migrated.wallet(2, trial=True) == (1, 0)
    assert Store(store.path).admin_stats()["generated_count"] == 1
