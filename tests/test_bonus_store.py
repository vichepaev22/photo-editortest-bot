from concurrent.futures import ThreadPoolExecutor

import pytest

from image_studio.store import DomainError, Store

OWNER = 3975497


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "bonus.sqlite3")
    result.consent(OWNER)
    return result


def complete(store, step, *, deliver=True):
    job = store.reserve(OWNER, "test:" + step["id"], step["preset"], 1,
                        quota_exempt=True, bonus_step=step["id"])
    store.claim(job)
    store.finish(job, f"{OWNER}/{job}.jpg", {}, None)
    if deliver:
        store.delivered(job)
    return job


def test_owner_only_and_program_is_durable(store):
    with pytest.raises(DomainError, match="bonus_unavailable"):
        store.bonus_snapshot(123)
    first = store.bonus_snapshot(OWNER)
    assert first == Store(store.path).bonus_snapshot(OWNER)
    assert [len(m["steps"]) for m in first["missions"]] == [1, 2, 2, 3, 3]
    assert sum(m["reward"] for m in first["missions"]) == 500
    assert first["points"] == first["xp"] == first["redeemed"] == 0
    titles = [s["title"] for m in first["missions"] for s in m["steps"]]
    assert len(titles) == len(set(titles))
    mission = first["missions"][0]
    changed = store.bonus_swap(OWNER, mission["id"])
    assert changed["title"] != mission["title"]
    complete(store, changed["steps"][0])
    with pytest.raises(DomainError, match="bonus_started"):
        store.bonus_swap(OWNER, mission["id"])


def test_one_result_cannot_complete_two_steps_or_wrong_function(store):
    mission = store.bonus_snapshot(OWNER)["missions"][1]
    second = mission["steps"][1]
    with pytest.raises(DomainError, match="bonus_step_order"):
        complete(store, second)
    first = mission["steps"][0]
    with pytest.raises(DomainError, match="bonus_step_mismatch"):
        store.reserve(OWNER, "wrong", "merge", 1, quota_exempt=True, bonus_step=first["id"])
    job = complete(store, first)
    snapshot = store.bonus_snapshot(OWNER)
    assert snapshot["points"] == 0 and snapshot["xp"] == 10
    assert snapshot["missions"][1]["steps"][0]["done"]
    assert not snapshot["missions"][1]["steps"][1]["done"]
    store.delivered(job)
    assert store.bonus_snapshot(OWNER)["xp"] == 10


def test_failed_and_ordinary_jobs_do_not_reward_and_retry_is_explicit(store):
    step = store.bonus_snapshot(OWNER)["missions"][0]["steps"][0]
    job = store.reserve(OWNER, "failed", step["preset"], 1, quota_exempt=True, bonus_step=step["id"])
    store.claim(job)
    store.fail(job, "provider_error")
    assert store.bonus_snapshot(OWNER)["points"] == 0
    ordinary = store.reserve(OWNER, "ordinary", step["preset"], 1, quota_exempt=True)
    store.claim(ordinary)
    store.finish(ordinary, f"{OWNER}/ordinary.jpg", {}, None)
    store.delivered(ordinary)
    assert store.bonus_snapshot(OWNER)["points"] == 0
    complete(store, step)
    assert store.bonus_snapshot(OWNER)["points"] == 50


def test_generated_without_delivery_waits_and_restart_does_not_duplicate(store):
    step = store.bonus_snapshot(OWNER)["missions"][0]["steps"][0]
    job = complete(store, step, deliver=False)
    assert store.bonus_snapshot(OWNER)["points"] == 0
    restarted = Store(store.path)
    restarted.delivered(job)
    restarted.delivered(job)
    assert restarted.bonus_snapshot(OWNER)["points"] == 50
    assert restarted.bonus_snapshot(OWNER)["xp"] == 10


def test_whole_program_550_atomic_exchange_and_no_repeat_program(store):
    before = store.wallet(OWNER)
    for mission in store.bonus_snapshot(OWNER)["missions"]:
        for step in mission["steps"]:
            complete(store, step)
    snapshot = store.bonus_snapshot(OWNER)
    assert snapshot["points"] == snapshot["earned"] == 550
    assert snapshot["xp"] == 110
    quote = store.bonus_quote(OWNER, 5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: store.bonus_exchange(OWNER, quote["id"]), range(2)))
    after = store.bonus_snapshot(OWNER)
    assert after["points"] == 50 and after["redeemed"] == 5
    assert store.wallet(OWNER) == (before[0] + 5, before[1])
    assert len(after["missions"]) == 5 and all(m["done"] for m in after["missions"])
    assert Store(store.path).bonus_snapshot(OWNER)["points"] == 50
    with pytest.raises(DomainError, match="bonus_insufficient"):
        store.bonus_quote(OWNER, 1)
    with pytest.raises(DomainError, match="bonus_invalid_quantity"):
        store.bonus_quote(OWNER, -1)
    assert sum(row["points"] for row in after["history"]) == 50


def test_competing_different_checkouts_cannot_overspend(store):
    for mission in store.bonus_snapshot(OWNER)["missions"]:
        for step in mission["steps"]:
            complete(store, step)
    first, second = store.bonus_quote(OWNER, 5), store.bonus_quote(OWNER, 5)
    store.bonus_exchange(OWNER, first["id"])
    with pytest.raises(DomainError, match="bonus_insufficient"):
        store.bonus_exchange(OWNER, second["id"])
    assert store.bonus_snapshot(OWNER)["redeemed"] == 5


def test_consent_and_request_binding_are_enforced(store):
    step = store.bonus_snapshot(OWNER)["missions"][0]["steps"][0]
    store.revoke_consent(OWNER)
    with pytest.raises(DomainError, match="consent_required"):
        store.bonus_snapshot(OWNER)
    store.consent(OWNER)
    job = store.reserve(OWNER, "same", step["preset"], 1, quota_exempt=True, bonus_step=step["id"])
    assert store.reserve(OWNER, "same", step["preset"], 1,
                         quota_exempt=True, bonus_step=step["id"]) == job
    with pytest.raises(DomainError, match="request_mismatch"):
        store.reserve(OWNER, "same", step["preset"], 1, quota_exempt=True)


def test_catalog_is_valid_diverse_and_every_budget_is_fixed():
    from image_studio.bonus_catalog import TEMPLATES
    from image_studio.catalog import PRESETS, prompt_for
    for slot, variants in enumerate(TEMPLATES):
        assert len(variants) >= 3
        for _, _, steps in variants:
            assert len(steps) == (1, 2, 2, 3, 3)[slot]
            assert len({s["title"] for s in steps}) == len(steps)
            for step in steps:
                assert step["preset"] in PRESETS and PRESETS[step["preset"]].inputs == 1
                prompt_for(step["preset"], step["description"])


def test_exchange_preserves_unused_trial_and_cannot_reset_it(store):
    store.grant_trial(OWNER)
    for mission in store.bonus_snapshot(OWNER)["missions"][:2]:
        for step in mission["steps"]:
            complete(store, step)
    quote = store.bonus_quote(OWNER, 1)
    store.bonus_exchange(OWNER, quote["id"])
    assert store.wallet(OWNER, trial=True) == (2, 0)
    assert store.has_manual_access(OWNER)
    store.bonus_exchange(OWNER, quote["id"])
    assert store.wallet(OWNER, trial=True) == (2, 0)
