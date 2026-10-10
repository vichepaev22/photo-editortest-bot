import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from image_studio.store import DomainError, Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "admin.sqlite3")


def ready(store, user=10):
    store.consent(user)
    store.grant_trial(user)
    store.grant_manual(user, 5, f"{user:032x}")
    return user


def test_adjustment_preserves_running_paid_reserve_and_is_single_use(store):
    ready(store)
    job = store.reserve(10, "one", "hair", 1)
    op = store.admin_prepare_credit(99, 10, "set", 2, "support correction", trial=True)
    assert (op["before_available"], op["reserved"], op["after_available"]) == (5, 1, 2)
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(lambda _: store.admin_confirm_credit(99, op["id"], trial=True), range(4)))
    assert sum(row["applied"] for row in result) == 1
    assert store.wallet(10) == (3, 1)
    assert len(store.admin_credit_history(10)) == 1
    store.claim(job)
    store.finish(job, "result.jpg", {}, "request")
    assert store.wallet(10) == (2, 0)


def test_adjustment_waits_for_trial_success_and_does_not_regrant_base(store):
    store.consent(10)
    store.grant_trial(10)
    job = store.reserve(10, "one", "hair", 1, trial=True)
    with pytest.raises(DomainError, match="already_active"):
        store.admin_prepare_credit(99, 10, "add", 5, "gift", trial=True)
    store.claim(job)
    store.finish(job, "result.jpg", {}, "request")
    op = store.admin_prepare_credit(99, 10, "add", 5, "gift", trial=True)
    store.admin_confirm_credit(99, op["id"], trial=True)
    assert store.wallet(10, trial=True) == (5, 0)
    assert not store.grant_trial(10)


def test_trial_promotion_preserves_existing_paid_funds(store):
    store.consent(10)
    store.grant_trial(10)
    store.grant_pilot(10, 3)
    op = store.admin_prepare_credit(99, 10, "add", 5, "gift", trial=True)
    assert op["before_available"] == 4
    store.admin_confirm_credit(99, op["id"], trial=True)
    assert store.wallet(10, trial=True) == (9, 0)


@pytest.mark.parametrize("state", ["queued", "running", "review"])
def test_active_trial_cannot_be_promoted_before_failure_release(store, state):
    store.consent(10)
    store.grant_trial(10)
    job = store.reserve(10, "trial", "hair", 1, trial=True)
    if state != "queued":
        store.claim(job)
    if state == "review":
        store.recover()
    with pytest.raises(DomainError, match="already_active"):
        store.admin_prepare_credit(99, 10, "add", 5, "gift during trial", trial=True)
    assert store.wallet(10, trial=True) == (1, 1)
    assert store.admin_user(10, trial=True)["reserved"] == 1
    store.fail(job, "provider_error")
    store.fail(job, "duplicate failure")
    assert store.wallet(10, trial=True) == (1, 0)
    op = store.admin_prepare_credit(99, 10, "add", 5, "gift after trial", trial=True)
    store.admin_confirm_credit(99, op["id"], trial=True)
    assert store.wallet(10, trial=True) == (6, 0)
    assert store.admin_user(10, trial=True)["reserved"] == 0
    assert not store.grant_trial(10)


def test_exempt_trial_failure_after_admin_promotion_does_not_create_refund(store):
    store.consent(10)
    store.grant_trial(10)
    job = store.reserve(10, "owner", "hair", 1, trial=True, quota_exempt=True)
    op = store.admin_prepare_credit(99, 10, "add", 5, "gift", trial=True)
    store.admin_confirm_credit(99, op["id"], trial=True)
    assert store.wallet(10, trial=True) == (6, 0)
    store.fail(job, "provider_error")
    assert store.wallet(10, trial=True) == (6, 0)


def test_paid_failure_after_legacy_manual_conversion_only_releases_balance_reserve(store):
    ready(store)
    job = store.reserve(10, "manual", "hair", 1)
    op = store.admin_prepare_credit(99, 10, "add", 5, "additional gift", trial=True)
    store.admin_confirm_credit(99, op["id"], trial=True)
    assert store.wallet(10, trial=True) == (11, 1)
    store.fail(job, "provider_error")
    store.fail(job, "duplicate failure")
    assert store.wallet(10, trial=True) == (11, 0)


def test_credit_rejects_owner_stale_expired_negative_cancel_and_funding_change(store):
    ready(store)
    op = store.admin_prepare_credit(99, 10, "add", -1, "remove")
    with pytest.raises(DomainError):
        store.admin_confirm_credit(98, op["id"])
    store.reserve(10, "one", "hair", 1)
    with pytest.raises(DomainError):
        store.admin_confirm_credit(99, op["id"])
    op2 = store.admin_prepare_credit(99, 10, "set", 0, "zero")
    store.admin_cancel_credit(99, op2["id"])
    with pytest.raises(DomainError):
        store.admin_confirm_credit(99, op2["id"])
    op3 = store.admin_prepare_credit(99, 10, "add", 1, "expired")
    with store.tx() as c:
        c.execute("UPDATE admin_credit_operations SET expires=0 WHERE id=?", (op3["id"],))
    with pytest.raises(DomainError):
        store.admin_confirm_credit(99, op3["id"])
    with pytest.raises(DomainError):
        store.admin_prepare_credit(99, 10, "add", -100, "negative")
    with pytest.raises(DomainError):
        store.admin_confirm_credit(99, op3["id"], trial=True)


def test_first_visit_not_gift_and_restart_dedup(store):
    ready(store)
    assert store.admin_events() == []
    store.record_visit(10, "valid_name")
    store.record_visit(10, "valid_name")
    again = Store(store.path)
    again.record_visit(10)
    rows = again.admin_events()
    assert len(rows) == 1 and rows[0]["kind"] == "registration"


def test_session_settings_questions_and_restart(store):
    store.admin_session_set(99, "reason", {"user": 10})
    store.admin_configure("enabled", False)
    store.admin_configure("questions", False)
    assert store.admin_capture_question(10, "help", 1, 10)
    assert not store.admin_capture_question(10, "help", 1, 10)
    assert not store.admin_capture_question(10, "again", 2, 10)
    again = Store(store.path)
    assert again.admin_session(99) == {"kind": "reason", "data": {"user": 10}}
    assert again.admin_claim_event() is None
    again.admin_configure("enabled", True)
    again.admin_configure("questions", True)
    assert again.admin_claim_event()["kind"] == "questions"


def test_outbox_dedup_recovery_retry_and_terminal(store):
    assert store.admin_event("one", "generation", {"status": "started"})
    assert not store.admin_event("one", "generation", {})
    row = store.admin_claim_event()
    assert row["attempts"] == 1
    assert store.admin_event_detail(row["id"])["payload"] == {"status": "started"}
    with pytest.raises(DomainError):
        store.admin_event_detail("0" * 32)
    again = Store(store.path)
    again.admin_recover_events()
    assert again.admin_claim_event() is None
    assert again.admin_events()[0]["status"] == "needs_review"
    again.admin_event("retry", "generation", {})
    for attempt in range(3):
        row = again.admin_claim_event()
        assert row["attempts"] == attempt + 1
        again.admin_fail_event(row["id"], retry_after=1)
        with again.tx() as c:
            c.execute("UPDATE admin_outbox SET next_attempt=0 WHERE id=?", (row["id"],))
    assert again.admin_claim_event() is None
    assert again.admin_events()[0]["status"] == "failed"


def test_generation_events_metadata_and_production_payment_only(store):
    ready(store)
    job = store.reserve(10, "one", "hair", 1)
    store.claim(job)
    store.finish(job, "private_result.jpg", {}, "request")
    store.finish(job, "private_result.jpg", {}, "request")
    for is_test in (True, False):
        order = store.invoice(10, "pack", 100, 1)
        store.payment(10, order, "RUB", 100, str(is_test), is_test=is_test)
        assert not store.payment(10, order, "RUB", 100, str(is_test), is_test=is_test)
    rows = store.admin_events()
    assert [r["kind"] for r in rows].count("payment") == 1
    assert [r["kind"] for r in rows].count("generation") == 2
    assert "private_result" not in str(rows)


def test_safe_enqueue_failure_cannot_rollback_payment_or_job(store, monkeypatch):
    ready(store)
    monkeypatch.setattr(store, "_admin_enqueue", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("broken")))
    job = store.reserve(10, "one", "hair", 1)
    assert store.claim(job)
    store.finish(job, "result.jpg", {}, "request")
    assert store.job(job)["status"] == "generated"
    order = store.invoice(10, "pack", 100, 1)
    assert store.payment(10, order, "RUB", 100, "production", is_test=False)
    assert store.wallet(10) == (6, 0)


def test_sharing_default_revoke_and_ttl(store):
    ready(store)
    job = store.reserve(10, "one", "hair", 1)
    store.admin_capture_job(job, 10, "secret prompt")
    assert "description" not in store.admin_job_detail(job)
    store.admin_set_sharing(10, True)
    store.admin_capture_job(job, 10, "secret prompt")
    store.claim(job)
    store.finish(job, "result.jpg", {}, "request")
    assert store.admin_job_detail(job)["result"] == "result.jpg"
    store.admin_set_sharing(10, False)
    assert "result" not in store.admin_job_detail(job)
    store.admin_set_sharing(10, True)
    assert "description" not in store.admin_job_detail(job)
    store.admin_capture_job(job, 10, "secret prompt")
    with store.tx() as c:
        c.execute("UPDATE admin_job_shares SET expires=0 WHERE job_id=?", (job,))
    assert "result" not in store.admin_job_detail(job)


def test_additive_migration_keeps_data_and_admin_search(store):
    ready(store)
    store.record_visit(10, "valid_name")
    before = store.wallet(10)
    again = Store(store.path)
    assert again.wallet(10) == before
    assert again.admin_search("@VALID_NAME")["users"][0]["id"] == 10
    assert again.admin_user(10, trial=True)["available"] == 6
    assert again.admin_search("999")["users"] == []


def test_delete_revokes_sharing_and_idle_worker_purges_expired_prompt(store):
    ready(store)
    store.admin_set_sharing(10, True)
    job = store.reserve(10, "one", "hair", 1)
    store.admin_capture_job(job, 10, "private")
    with store.tx() as c:
        c.execute("UPDATE admin_job_shares SET expires=0")
    store.admin_claim_event()
    with store.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM admin_job_shares").fetchone()[0] == 0
    store.admin_capture_job(job, 10, "private")
    store.fail(job, "provider_error")
    store.revoke_consent(10)
    assert not store.admin_share_enabled(10)
    with store.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM admin_job_shares").fetchone()[0] == 0


def test_partial_enqueue_error_isolated_by_savepoint(store, monkeypatch):
    ready(store)
    original = store._admin_enqueue

    def broken(c, *args):
        original(c, *args)
        raise RuntimeError("after insert")

    monkeypatch.setattr(store, "_admin_enqueue", broken)
    job = store.reserve(10, "one", "hair", 1)
    assert store.claim(job)
    store.fail(job, "provider_error")
    assert store.wallet(10) == (6, 0)
    assert store.admin_events() == []


def test_question_dedup_still_holds_after_cooldown_and_recovery_keeps_reserve(store):
    ready(store)
    assert store.admin_capture_question(10, "hello", 1, 10)
    with store.tx() as c:
        c.execute("UPDATE admin_question_limits SET last_at=0")
    assert not store.admin_capture_question(10, "hello", 1, 10)
    assert store.admin_capture_question(10, "new", 2, 10)
    job = store.reserve(10, "one", "hair", 1)
    store.claim(job)
    store.recover()
    store.recover()
    assert store.wallet(10) == (6, 1)
    review = [r for r in store.admin_events() if r["kind"] == "generation" and r["payload"]["status"] == "review"]
    assert len(review) == 1


def test_quota_exempt_metadata_does_not_claim_a_charge(store):
    store.consent(10)
    job = store.reserve(10, "owner", "hair", 1, quota_exempt=True)
    store.claim(job)
    store.finish(job, "result.jpg", {}, "request")
    assert store.wallet(10) == (0, 0)
    assert all(r["payload"]["reserve_action"] == "exempt" for r in store.admin_events())


def test_true_legacy_schema_migration_keeps_existing_reservation(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE users(id INTEGER PRIMARY KEY,balance INTEGER NOT NULL DEFAULT 0,"
                  "reserved INTEGER NOT NULL DEFAULT 0,consent INTEGER NOT NULL DEFAULT 0,"
                  "demo_used INTEGER NOT NULL DEFAULT 0,spent INTEGER NOT NULL DEFAULT 0,"
                  "refund_lock INTEGER NOT NULL DEFAULT 0)")
        c.execute("INSERT INTO users VALUES(10,5,1,1,1,2,0)")
    store = Store(path)
    assert store.wallet(10) == (5, 1)
    assert store.admin_user(10)["available"] == 4
    assert not store.admin_share_enabled(10)
    assert store.admin_events() == []


def test_search_filters_count_only_actual_visitors_and_real_purchases(store):
    ready(store, 10)
    ready(store, 11)
    store.record_visit(10, "visited_one")
    store.record_visit(12, "visited_two")
    real = store.invoice(10, "express", 16000, 5)
    store.payment(10, real, "RUB", 16000, "real", is_test=False)
    test = store.invoice(11, "express", 16000, 5)
    store.payment(11, test, "RUB", 16000, "demo", is_test=True)
    stats = store.admin_stats()
    assert (stats["total_users"], stats["visited_users"], stats["paying_users"], stats["revenue_kopecks"]) == (3, 2, 1, 16000)
    assert [u["id"] for u in store.admin_search(filter="visited")["users"]] == [10, 12]
    assert [u["id"] for u in store.admin_search(filter="paying")["users"]] == [10]
    assert store.admin_search("11", filter="visited")["users"] == []
    assert store.admin_search("@VISITED_ONE", filter="paying")["users"][0]["id"] == 10
    with pytest.raises(DomainError):
        store.admin_search(filter="unknown")
    with pytest.raises(DomainError):
        store.admin_search("x' OR 1=1")


def test_card_distinguishes_paid_funds_trial_and_previsit_entitlement(store):
    store.consent(10)
    store.grant_pilot(10, 3)
    card = store.admin_user(10, trial=True)
    assert card["trial_remaining"] == card["base_available"] == 1
    assert card["paid_balance"] == card["paid_available"] == 3
    assert card["available"] == 4
    assert card["active_available"] == 1
    assert card["first_seen"] is None and not card["share_enabled"]
    op = store.admin_prepare_credit(99, 10, "add", 1, "before first visit", trial=True)
    store.admin_confirm_credit(99, op["id"], trial=True)
    assert store.wallet(10, trial=True) == (5, 0)
    assert store.admin_user(10, trial=True)["trial_remaining"] == 0
    assert not store.grant_trial(10)


def test_owner_and_new_trial_reservation_cannot_apply_stale_adjustment(store):
    store.consent(99)
    with pytest.raises(DomainError, match="owner_access_protected"):
        store.admin_prepare_credit(99, 99, "add", 1, "own account", trial=True)
    store.consent(10)
    store.grant_trial(10)
    op = store.admin_prepare_credit(99, 10, "set", 5, "support", trial=True)
    job = store.reserve(10, "trial", "hair", 1, trial=True)
    with pytest.raises(DomainError, match="stale_admin_confirmation"):
        store.admin_confirm_credit(99, op["id"], trial=True)
    assert store.wallet(10, trial=True) == (1, 1)
    store.fail(job, "provider_error")
    assert store.wallet(10, trial=True) == (1, 0)
    assert store.admin_credit_history(10) == []


def test_migration_never_replays_existing_visits_payments_or_generations(store):
    ready(store)
    store.record_visit(10)
    job = store.reserve(10, "old", "hair", 1)
    store.claim(job)
    store.finish(job, "result.jpg", {}, "request")
    order = store.invoice(10, "pack", 100, 1)
    store.payment(10, order, "RUB", 100, "production", is_test=False)
    with store.tx() as c:
        c.execute("DELETE FROM admin_outbox")
    again = Store(store.path)
    assert again.admin_events() == []
    again.record_visit(10)
    assert again.admin_events() == []
    with again.tx() as c:
        tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "admin_announcements" not in tables and "admin_publish_confirmations" not in tables


def test_expired_sharing_checks_both_job_age_and_prompt_age(store):
    ready(store)
    store.admin_set_sharing(10, True)
    job = store.reserve(10, "private", "hair", 1)
    assert store.admin_capture_job(job, 10, "private prompt")
    with store.tx() as c:
        c.execute("UPDATE jobs SET created=0 WHERE id=?", (job,))
    assert not store.admin_job_detail(job)["share_available"]
    assert "description" not in store.admin_job_detail(job)


def test_paused_announcements_keep_legacy_data_but_never_enqueue_or_claim(store):
    with store.tx() as c:
        c.execute("CREATE TABLE admin_announcements(id TEXT PRIMARY KEY,text TEXT)")
        c.execute("INSERT INTO admin_announcements VALUES('old','preserve draft')")
        c.execute("INSERT INTO admin_settings VALUES('announcement',1)")
        c.execute("INSERT INTO admin_outbox(id,event_key,kind,payload,created) "
                  "VALUES(?,?,'announcement',?,0)", ("a" * 32, "legacy-announcement", '{"text":"old"}'))
    again = Store(store.path)
    with pytest.raises(DomainError, match="invalid_admin_event"):
        again.admin_event("new-announcement", "announcement", {"text": "new"})
    assert not hasattr(again, "admin_confirm_publish")
    assert again.admin_claim_event() is None
    with again.tx() as c:
        assert c.execute("SELECT text FROM admin_announcements").fetchone()[0] == "preserve draft"
        assert c.execute("SELECT status FROM admin_outbox WHERE kind='announcement'").fetchone()[0] == "pending"
