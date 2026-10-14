from concurrent.futures import ThreadPoolExecutor

import pytest

from image_studio.recurring_free import FreeDelivery, FreeDeliveryError
from image_studio.store import DomainError, Store

DAY = 86400


def account(tmp_path, monkeypatch, *, trial=True):
    clock = [1000000.0]
    monkeypatch.setattr('time.time', lambda: clock[0])
    store = Store(tmp_path / 'ledger.sqlite3')
    store.record_visit(11)
    store.consent(11)
    if trial:
        store.grant_trial(11)
    store.enable_recurring_free(trial=trial)
    return store, clock


def test_five_days_cap_start_and_restart(tmp_path, monkeypatch):
    store, clock = account(tmp_path, monkeypatch)
    clock[0] += 5 * DAY - 1
    assert store.advance_free_due() == 0
    clock[0] += 1
    assert store.advance_free_due() == 1
    assert store.wallet(11, trial=True) == (2, 0)
    assert store.advance_free_due() == 0
    clock[0] += 30 * DAY
    assert store.advance_free_due() == 1
    assert store.wallet(11, trial=True) == (3, 0)
    restart = Store(store.path)
    restart.enable_recurring_free(trial=True)
    assert restart.advance_free_due() == 0
    notice = restart.claim_free_notice()
    assert notice['added'] == 1  # room left above the unused starting generation
    assert notice['remaining'] == 3
    assert restart.claim_free_notice() is None


def test_free_spent_first_and_failure_returns_same_pool(tmp_path, monkeypatch):
    store, clock = account(tmp_path, monkeypatch, trial=False)
    gift = store.admin_prepare_credit(99, 11, 'add', 10, 'gift')
    store.admin_confirm_credit(99, gift['id'])
    clock[0] += 5 * DAY
    store.advance_free_due()
    job = store.reserve(11, 'one', 'hair', 1)
    assert store.job(job)['free_credit'] == 1
    assert store.wallet(11) == (11, 1)
    store.fail(job, 'failure')
    assert store.wallet(11) == (11, 0)
    job = store.reserve(11, 'two', 'merge', 1)
    assert store.reserve(11, 'two', 'merge', 1) == job
    store.claim(job)
    store.finish(job, 'photo.jpg', {}, 'fake')
    assert store.wallet(11) == (10, 0)
    assert store.free_info(11)['free_available'] == 0


def test_reserve_counts_toward_cap_and_parallel_due(tmp_path, monkeypatch):
    store, clock = account(tmp_path, monkeypatch, trial=False)
    clock[0] += 15 * DAY
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sum(workers.map(lambda _: store.advance_free_due(), range(2))) == 1
    assert store.wallet(11) == (3, 0)
    job = store.reserve(11, 'active', 'hair', 1)
    clock[0] += 5 * DAY
    store.advance_free_due()
    assert store.wallet(11) == (3, 1)
    assert store.claim_free_notice()['added'] == 0
    store.fail(job, 'failure')
    assert store.wallet(11) == (3, 0)


def test_consent_pause_excluded_owner_and_notices(tmp_path, monkeypatch):
    store, clock = account(tmp_path, monkeypatch)
    store.record_visit(22)
    store.consent(22)
    store.revoke_consent(11)
    clock[0] += 25 * DAY
    store.advance_free_due()
    assert store.free_info(11)['free_available'] == 1
    store.consent(11)
    assert store.advance_free_due() == 0
    clock[0] += 5 * DAY
    assert store.advance_free_due() == 2
    assert store.free_info(11)['free_available'] == 2
    notice = store.claim_free_notice()
    store.recover_free_notices()
    with store.tx() as c:
        assert c.execute('SELECT status FROM free_notices WHERE id=?', (notice['id'],)).fetchone()[0] == 'unknown'
    store.free_notice_result(store.claim_free_notice()['id'], 'blocked')
    clock[0] += 5 * DAY
    store.advance_free_due()
    assert store.claim_free_notice() is not None  # other user still receives notice
    store.record_visit(0)
    assert store.free_info(0)['next_due'] is None


def test_admin_adjustment_preserves_free_pool(tmp_path, monkeypatch):
    store, clock = account(tmp_path, monkeypatch, trial=False)
    gift = store.admin_prepare_credit(99, 11, 'add', 10, 'gift')
    store.admin_confirm_credit(99, gift['id'])
    clock[0] += 10 * DAY
    store.advance_free_due()
    op = store.admin_prepare_credit(99, 11, 'add', 5, 'Таков путь')
    store.admin_confirm_credit(99, op['id'])
    assert store.wallet(11) == (17, 0)
    assert store.free_info(11)['free_available'] == 2
    op = store.admin_prepare_credit(99, 11, 'set', 1, 'test')
    store.admin_confirm_credit(99, op['id'])
    assert store.wallet(11) == (1, 0)
    assert store.free_info(11)['free_available'] == 1
    with pytest.raises(DomainError):
        store.reserve(0, 'bad', 'hair', 1)


def test_starting_use_first_then_recurring_and_offer_once(tmp_path, monkeypatch):
    store, clock = account(tmp_path, monkeypatch)
    clock[0] += 10 * DAY
    store.advance_free_due()
    job = store.reserve(11, 'first', 'hair', 1, trial=True)
    assert store.job(job)['free_credit'] == 0
    store.claim(job)
    store.finish(job, 'photo.jpg', {}, 'fake')
    store.delivered(job)
    assert store.claim_channel_bonus_offer(11, job)
    assert not store.claim_channel_bonus_offer(11, job)
    later = store.reserve(11, 'later', 'hair', 1, trial=True)
    assert store.job(later)['free_credit'] == 1
    store.claim(later)
    store.finish(later, 'second.jpg', {}, 'fake')
    store.delivered(later)
    assert not store.claim_channel_bonus_offer(11, later)
    assert store.wallet(11, trial=True) == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['sent', 'blocked', 'unknown', 'queued'])
async def test_delivery_failure_does_not_undo_or_double_grant(tmp_path, monkeypatch, outcome):
    store, clock = account(tmp_path, monkeypatch, trial=False)
    clock[0] += 5 * DAY
    sent = []

    async def send(user, text):
        sent.append((user, text))
        if outcome == 'unknown':
            raise OSError('simulated uncertain transport')
        if outcome != 'sent':
            raise FreeDeliveryError(outcome, 60)
        return 'message-1'

    worker = FreeDelivery(store, send)
    assert await worker.once()
    assert not await worker.once()
    assert store.wallet(11) == (1, 0)
    assert len(sent) == 1
    assert 'Каждые 5 дней' in sent[0][1]
    with store.tx() as c:
        assert c.execute('SELECT status FROM free_notices').fetchone()[0] == outcome
    if outcome == 'queued':
        clock[0] += 60
        assert await worker.once()
        assert store.wallet(11) == (1, 0)


def test_unlimited_owner_excluded_and_existing_funds_unchanged(tmp_path, monkeypatch):
    store, clock = account(tmp_path, monkeypatch, trial=False)
    store.enable_recurring_free(exempt=11)
    clock[0] += 100 * DAY
    assert store.advance_free_due() == 0
    assert store.wallet(11) == (0, 0)
    assert store.claim_free_notice() is None
