import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from image_studio.store import DomainError, Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "trial.sqlite3")


def ready(store, user=10):
    store.consent(user)
    assert store.grant_trial(user)
    return user


def complete(store, key, preset="hair", user=10):
    job = store.reserve(user, key, preset, 1, trial=True)
    assert store.claim(job)
    store.finish(job, f"{user}/{job}.jpg", {"output_tokens": 1}, "offline-request")
    return job


def test_trial_claim_requires_consent_and_never_changes_paid_wallet(store):
    with pytest.raises(DomainError, match="^consent_required$"):
        store.grant_trial(10)
    assert store.wallet(10, trial=True) == (0, 0)
    store.consent(10)
    store.grant_pilot(10, 3)
    assert store.grant_trial(10)
    assert not store.grant_trial(10)
    assert store.wallet(10) == (3, 0)
    assert store.wallet(10, trial=True) == (1, 0)


@pytest.mark.parametrize("user", [0, -1, True, False, "10", 1.0, None, 2**52])
def test_only_valid_positive_telegram_ids_can_claim_trial(store, user):
    with pytest.raises(DomainError, match="^invalid_trial_user$"):
        store.grant_trial(user)
    with store.tx() as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_trial_is_granted_once_under_concurrent_requests(store):
    store.consent(10)
    with ThreadPoolExecutor(max_workers=8) as pool:
        grants = list(pool.map(lambda _: store.grant_trial(10), range(16)))
    assert grants.count(True) == 1 and grants.count(False) == 15
    assert store.wallet(10, trial=True) == (1, 0) and store.wallet(10) == (0, 0)
    with store.tx() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events WHERE kind='trial_grant'").fetchone()[0] == 1


def test_one_output_merge_consumes_trial_and_paid_wallet_cannot_extend_it(store):
    ready(store)
    for _ in range(10):
        store.grant_pilot(10, 10)
    first = complete(store, "first-merge", "merge")
    assert store.job(first)["trial"] == 1 and store.job(first)["cost"] == 1
    assert store.wallet(10, trial=True) == (0, 0)
    assert store.wallet(10) == (100, 0)
    with store.tx() as connection:
        assert connection.execute("SELECT spent FROM users WHERE id=10").fetchone()[0] == 0
    for key, preset in (("regenerate-merge", "merge"), ("second-output", "hair")):
        with pytest.raises(DomainError, match="^trial_exhausted$"):
            store.reserve(10, key, preset, 1, trial=True)
    assert store.reserve(10, "first-merge", "merge", 1, trial=True) == first
    assert store.wallet(10) == (100, 0) and len(store.jobs(10)) == 1


@pytest.mark.parametrize("cost", [0, 2, -1, True, 1.0])
def test_trial_reservation_must_cost_exactly_one(store, cost):
    ready(store)
    with pytest.raises(DomainError, match="^invalid_job$"):
        store.reserve(10, "bad-cost", "merge", cost, trial=True)
    assert store.jobs(10) == [] and store.wallet(10, trial=True) == (1, 0)


def test_trial_reserve_requires_existing_claim_and_consent(store):
    store.consent(10)
    with pytest.raises(DomainError, match="^trial_not_granted$"):
        store.reserve(10, "no-claim", "hair", 1, trial=True)
    store.grant_trial(10)
    store.revoke_consent(10)
    with pytest.raises(DomainError, match="^consent_required$"):
        store.reserve(10, "revoked", "hair", 1, trial=True)


def test_trial_reserve_duplicate_and_failure_release_only_once(store):
    ready(store)
    with ThreadPoolExecutor(max_workers=8) as pool:
        duplicates = list(pool.map(lambda _: store.reserve(10, "same", "hair", 1, trial=True), range(8)))
    assert len(set(duplicates)) == 1 and len(store.jobs(10)) == 1
    job = duplicates[0]
    assert store.wallet(10, trial=True) == (1, 1) and store.wallet(10) == (0, 0)
    store.fail(job, "provider_rejected")
    store.fail(job, "provider_rejected")
    assert store.reserve(10, "same", "hair", 1, trial=True) == job
    assert not store.claim(job)
    assert store.wallet(10, trial=True) == (1, 0)
    complete(store, "explicit-new-retry")
    assert store.wallet(10, trial=True) == (0, 0)


def test_trial_result_finish_and_delivery_are_idempotent_and_owner_scoped(store):
    ready(store)
    job = complete(store, "output")
    store.finish(job, "replacement.jpg", {}, None)
    store.fail(job, "late_failure")
    store.delivered(job)
    store.finish(job, "replacement.jpg", {}, None)
    assert store.wallet(10, trial=True) == (0, 0)
    assert store.result(10, job)["result"] == f"10/{job}.jpg"
    assert store.result(10, job)["status"] == "delivered"
    with pytest.raises(DomainError, match="^result_unavailable$"):
        store.result(11, job)


def test_review_keeps_trial_reserved_until_explicit_release_and_blocks_other_modes(store):
    ready(store)
    store.grant_pilot(10, 3)
    job = store.reserve(10, "interrupted", "hair", 1, trial=True)
    assert store.claim(job)
    store.recover()
    store.recover()
    assert store.job(job)["status"] == "review"
    assert store.wallet(10, trial=True) == (1, 1)
    assert store.wallet(10) == (3, 0) and not store.claim(job)
    for trial in (False, True):
        with pytest.raises(DomainError, match="^already_active$"):
            store.reserve(10, "another-" + str(trial), "hair", 1, trial=trial)
    with pytest.raises(DomainError, match="^already_active$"):
        store.revoke_consent(10)
    with pytest.raises(DomainError, match="^invalid_job_state$"):
        store.finish(job, "uncertain-output.jpg", {}, None)
    store.fail(job, "manual_release")
    store.fail(job, "manual_release")
    assert store.wallet(10, trial=True) == (1, 0)
    assert store.wallet(10) == (3, 0)


def test_queued_cancel_and_financial_active_job_preserve_distinct_reservations(store):
    ready(store)
    store.grant_pilot(10, 3)
    financial = store.reserve(10, "paid", "merge", 2)
    with pytest.raises(DomainError, match="^already_active$"):
        store.reserve(10, "trial-blocked", "hair", 1, trial=True)
    assert store.wallet(10) == (3, 2) and store.wallet(10, trial=True) == (1, 0)
    store.fail(financial, "cancel")
    trial = store.reserve(10, "trial", "merge", 1, trial=True)
    store.fail(trial, "cancel")
    assert store.wallet(10) == (3, 0) and store.wallet(10, trial=True) == (1, 0)


def test_request_identity_includes_financial_vs_trial_mode(store):
    ready(store)
    store.grant_pilot(10, 3)
    trial = store.reserve(10, "trial-first", "hair", 1, trial=True)
    store.fail(trial, "cancel")
    with pytest.raises(DomainError, match="^request_mismatch$"):
        store.reserve(10, "trial-first", "hair", 1)
    paid = store.reserve(10, "paid-first", "hair", 1)
    store.fail(paid, "cancel")
    with pytest.raises(DomainError, match="^request_mismatch$"):
        store.reserve(10, "paid-first", "hair", 1, trial=True)
    assert store.wallet(10) == (3, 0) and store.wallet(10, trial=True) == (1, 0)


def test_trial_history_survives_revoke_reconsent_and_restart(store):
    ready(store)
    complete(store, "one")
    store.revoke_consent(10)
    reopened = Store(store.path)
    assert not reopened.has_consent(10)
    assert reopened.wallet(10, trial=True) == (0, 0)
    reopened.consent(10)
    assert not reopened.grant_trial(10)
    with pytest.raises(DomainError, match="^trial_exhausted$"):
        reopened.reserve(10, "second", "hair", 1, trial=True)
    reopened.revoke_consent(10)
    restarted = Store(store.path)
    restarted.consent(10)
    assert not restarted.grant_trial(10)
    with pytest.raises(DomainError, match="^trial_exhausted$"):
        restarted.reserve(10, "reset-attempt", "hair", 1, trial=True)


def test_additive_idempotent_legacy_migration_preserves_wallet_jobs_history_and_files(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    photo = tmp_path / "legacy-media.jpg"
    photo.write_bytes(b"offline-sentinel")
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0),
                reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved>=0 AND reserved<=balance),
                consent INTEGER NOT NULL DEFAULT 0, demo_used INTEGER NOT NULL DEFAULT 0,
                spent INTEGER NOT NULL DEFAULT 0, refund_lock INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, request_key TEXT NOT NULL,
                preset TEXT NOT NULL, cost INTEGER NOT NULL CHECK(cost>0),
                status TEXT NOT NULL DEFAULT 'queued', created REAL NOT NULL,
                result TEXT, usage TEXT, request_id TEXT, error TEXT, UNIQUE(user_id,request_key));
            CREATE TABLE events (id INTEGER PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL,
                                 ref TEXT NOT NULL);
            INSERT INTO users VALUES(10,7,2,1,1,4,0);
            INSERT INTO jobs(id,user_id,request_key,preset,cost,status,created)
                VALUES('legacy-job',10,'legacy-key','merge',2,'running',1);
            INSERT INTO events VALUES(1,1,'legacy_event','offline-history');
        """)
    with ThreadPoolExecutor(max_workers=4) as pool:
        stores = list(pool.map(lambda _: Store(path), range(4)))
    migrated = stores[0]
    assert migrated.wallet(10) == (7, 2) and migrated.wallet(10, trial=True) == (0, 0)
    assert migrated.job("legacy-job")["trial"] == 0
    assert migrated.job("legacy-job")["status"] == "running"
    assert migrated.grant_trial(10)
    migrated.finish("legacy-job", "legacy-output.jpg", {}, None)
    assert migrated.wallet(10) == (5, 0) and migrated.wallet(10, trial=True) == (1, 0)
    again = Store(path)
    assert not again.grant_trial(10) and again.wallet(10) == (5, 0)
    assert photo.read_bytes() == b"offline-sentinel"
    with again.tx() as connection:
        assert connection.execute("SELECT spent FROM users WHERE id=10").fetchone()[0] == 6
        assert connection.execute("SELECT ref FROM events WHERE kind='legacy_event'").fetchone()[0] == (
            "offline-history"
        )


@pytest.mark.parametrize("update", ["trial_used=4", "trial_used=-1", "trial_reserved=4",
                                    "trial_used=3,trial_reserved=1", "trial_granted=2"])
def test_sqlite_constraints_enforce_trial_counter_bounds(store, update):
    ready(store)
    with pytest.raises(sqlite3.IntegrityError):
        with store.tx() as connection:
            connection.execute("UPDATE users SET " + update + " WHERE id=10")
    assert store.wallet(10, trial=True) == (1, 0)


@pytest.mark.parametrize("settle", ["finish", "fail"])
def test_legacy_used_two_three_and_pending_reservation_survive_reopen_without_new_trial(store, settle):
    for user in (10, 11, 12):
        ready(store, user)
        store.grant_pilot(user, 7)
    pending = store.reserve(12, "legacy-pending", "merge", 1, trial=True)
    with store.tx() as connection:
        connection.executemany("UPDATE users SET trial_used=? WHERE id=?", [(2, 10), (3, 11), (2, 12)])
        before = [tuple(row) for row in connection.execute("SELECT * FROM users ORDER BY id")]
        grants = connection.execute("SELECT COUNT(*) FROM events WHERE kind='trial_grant'").fetchone()[0]
    reopened = Store(store.path)
    with reopened.tx() as connection:
        assert [tuple(row) for row in connection.execute("SELECT * FROM users ORDER BY id")] == before
    for user in (10, 11):
        assert reopened.wallet(user, trial=True) == (0, 0)
        assert reopened.wallet(user) == (7, 0)
        reopened.revoke_consent(user)
        reopened.consent(user)
        assert not reopened.grant_trial(user)
        with pytest.raises(DomainError, match="^trial_exhausted$"):
            reopened.reserve(user, "new", "hair", 1, trial=True)
    assert reopened.wallet(12, trial=True) == (1, 1)
    assert reopened.reserve(12, "legacy-pending", "merge", 1, trial=True) == pending
    if settle == "finish":
        assert reopened.claim(pending)
        reopened.finish(pending, "legacy-output.jpg", {}, None)
    else:
        reopened.fail(pending, "legacy-provider-failure")
    assert reopened.wallet(12, trial=True) == (0, 0) and reopened.wallet(12) == (7, 0)
    with pytest.raises(DomainError, match="^trial_exhausted$"):
        reopened.reserve(12, "new", "hair", 1, trial=True)
    again = Store(store.path)
    with again.tx() as connection:
        assert [row[0] for row in connection.execute("SELECT trial_used FROM users ORDER BY id")] == (
            [2, 3, 3 if settle == "finish" else 2]
        )
        assert connection.execute("SELECT COUNT(*) FROM events WHERE kind='trial_grant'").fetchone()[0] == grants


def journal_lock_injection(monkeypatch, *, failures, code):
    import image_studio.store as module
    actual_connect = sqlite3.connect
    attempts, sleeps, connections = [], [], []
    class JournalConnection(sqlite3.Connection):
        def journal_busy(self, sql):
            if re.search(r"PRAGMA\s+journal_mode\s*=\s*WAL", sql, re.I):
                attempts.append(sql)
                if len(attempts) <= failures:
                    error = sqlite3.OperationalError("injected database lock")
                    error.sqlite_errorcode = code
                    raise error

        def execute(self, sql, *args, **kwargs):
            self.journal_busy(sql)
            return super().execute(sql, *args, **kwargs)

        def executescript(self, sql):
            self.journal_busy(sql)
            return super().executescript(sql)

    def connect(*args, **kwargs):
        connection = actual_connect(*args, **kwargs, factory=JournalConnection)
        connections.append(connection)
        return connection
    monkeypatch.setattr(module.sqlite3, "connect", connect)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    return attempts, sleeps, connections


@pytest.mark.parametrize("code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_BUSY | 0x100])
def test_transient_wal_upgrade_contention_is_retried_and_connections_closed(tmp_path, monkeypatch, code):
    attempts, sleeps, connections = journal_lock_injection(monkeypatch, failures=1, code=code)
    store = Store(tmp_path / "contention.sqlite3")
    assert len(attempts) == 2 and 0 < sum(sleeps) <= 1
    assert store.wallet(10) == (0, 0)
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


def test_persistent_wal_contention_has_bounded_retries(tmp_path, monkeypatch):
    attempts, sleeps, connections = journal_lock_injection(
        monkeypatch, failures=100, code=sqlite3.SQLITE_BUSY,
    )
    with pytest.raises(sqlite3.OperationalError, match="injected database lock"):
        Store(tmp_path / "persistent-lock.sqlite3")
    assert 1 < len(attempts) <= 5 and 0 < sum(sleeps) <= 1
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


def test_non_lock_wal_errors_are_never_retried(tmp_path, monkeypatch):
    attempts, sleeps, _ = journal_lock_injection(monkeypatch, failures=100, code=sqlite3.SQLITE_READONLY)
    with pytest.raises(sqlite3.OperationalError, match="injected database lock"):
        Store(tmp_path / "readonly.sqlite3")
    assert len(attempts) == 1 and sleeps == []
