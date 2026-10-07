from concurrent.futures import ThreadPoolExecutor

import pytest

from image_studio.store import DomainError, Store


@pytest.fixture
def db(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.consent(10)
    store.grant_demo(10, 3)
    return store


def test_no_negative_or_concurrent_overspending(db):
    def submit(i):
        try:
            return db.reserve(10, str(i), "merge", 2)
        except DomainError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    assert sum(x is not None for x in results) == 1
    assert db.wallet(10) == (3, 2)


def test_duplicate_request_and_failure_are_idempotent(db):
    job = db.reserve(10, "same", "hair", 1)
    assert db.reserve(10, "same", "hair", 1) == job
    db.fail(job, "provider_error")
    db.fail(job, "provider_error")
    assert db.wallet(10) == (3, 0)


def test_finish_once_and_result_ownership(db):
    job = db.reserve(10, "job", "hair", 1)
    assert db.claim(job)
    db.finish(job, "result.jpg", {"output_tokens": 100}, "req_1")
    db.finish(job, "result.jpg", {}, None)
    assert db.wallet(10) == (2, 0)
    assert db.result(10, job)["result"] == "result.jpg"
    with pytest.raises(DomainError):
        db.result(11, job)


def test_crash_does_not_repeat_paid_call(db):
    job = db.reserve(10, "crash", "hair", 1)
    db.claim(job)
    db.recover()
    assert db.job(job)["status"] == "review"
    assert not db.claim(job)
    assert db.wallet(10) == (3, 1)
    db.fail(job, "manual_release")
    assert db.wallet(10) == (3, 0)


def test_payment_once_and_order_identity(db):
    order = db.invoice(10, "small", 100, 5)
    assert db.precheck(10, order, "RUB", 100)
    assert not db.precheck(11, order, "RUB", 100)
    assert not db.precheck(10, order, "USD", 100)
    assert not db.precheck(10, order, "RUB", 1)
    assert db.payment(10, order, "RUB", 100, "charge_1")
    assert not db.payment(10, order, "RUB", 100, "charge_1")
    assert db.wallet(10) == (8, 0)
    with pytest.raises(DomainError):
        db.payment(11, order, "RUB", 100, "charge_2")
    assert db.wallet(10) == (8, 0)


def test_demo_cannot_repeat(db):
    assert not db.grant_demo(10, 3)
    assert db.wallet(10) == (3, 0)


def test_pilot_grant_requires_consent_and_bounded_amount(db):
    with pytest.raises(DomainError, match="consent_required"):
        db.grant_pilot(11, 3)
    with pytest.raises(DomainError, match="invalid_pilot_amount"):
        db.grant_pilot(10, 1000)
    db.grant_pilot(10, 2)
    assert db.wallet(10) == (5, 0)
    with db.tx() as c:
        assert c.execute("SELECT ref FROM events WHERE kind='pilot_grant'").fetchone()["ref"] == "10:2"


def test_unused_package_refund_locks_balance(db):
    order = db.invoice(10, "small", 100, 5)
    db.payment(10, order, "RUB", 100, "charge_1")
    record = db.begin_refund(order)
    assert record["charge_id"] == "charge_1"
    with pytest.raises(DomainError):
        db.reserve(10, "new", "hair", 1)
    db.finish_refund(order)
    assert db.wallet(10) == (3, 0)
    with pytest.raises(DomainError):
        db.begin_refund(order)


def test_spent_package_needs_manual_refund(db):
    order = db.invoice(10, "small", 100, 5)
    db.payment(10, order, "RUB", 100, "charge_1")
    job = db.reserve(10, "spend", "hair", 1)
    db.claim(job)
    db.finish(job, "result.jpg", {}, None)
    with pytest.raises(DomainError):
        db.begin_refund(order)
