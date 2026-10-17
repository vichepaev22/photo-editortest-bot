import pytest

from image_studio.store import DomainError, Store


def test_quiet_inbox_retains_old_queue_and_read_state_per_actor(tmp_path):
    store = Store(tmp_path / "quiet.sqlite3")
    for kind in ("registration", "questions", "generation", "payment"):
        store.admin_event(kind, kind, {"user_id": 10})
        store.admin_configure(kind, True)
    store.admin_configure("enabled", True)
    assert store.admin_claim_event() is None
    inbox = store.admin_inbox(99)
    assert inbox["unread"] == inbox["total"] == 4
    event = inbox["events"][0]
    assert all(row["status"] == "pending" and row["attempts"] == 0 for row in inbox["events"])
    assert store.admin_mark_read(99, event["id"])
    assert not store.admin_mark_read(99, event["id"])
    reopened = Store(store.path)
    assert reopened.admin_inbox(99)["unread"] == 3
    assert reopened.admin_inbox(98)["unread"] == 4
    assert reopened.admin_claim_event() is None
    assert reopened.admin_mark_all_read(99) == 3
    assert reopened.admin_mark_all_read(99) == 0
    reopened.admin_event("new-after-read", "registration", {})
    assert reopened.admin_unread_count(99) == 1
    assert reopened.admin_unread_count(98) == 5
    assert reopened.admin_event_detail(event["id"])["status"] == "pending"


def test_inbox_pagination_and_credit_notices_are_independent(tmp_path):
    store = Store(tmp_path / "quiet.sqlite3")
    for index in range(11):
        store.admin_event(f"visitor:{index}", "registration", {"user_id": index+1})
    assert store.admin_inbox(99)["pages"] == 2
    assert len(store.admin_inbox(99, 1)["events"]) == 1
    store.consent(10)
    operation = store.admin_prepare_credit(99, 10, "add", 2, "Таков путь")
    store.admin_confirm_credit(99, operation["id"])
    store.admin_mark_all_read(99)
    notice = store.admin_claim_event()
    assert notice["kind"] == "credit_grant"
    assert store.admin_credit_notice(notice["id"], 99)["user_id"] == 10
    assert store.admin_claim_event() is None
    store.admin_finish_event(notice["id"], 123)
    assert Store(store.path).admin_claim_event() is None


@pytest.mark.parametrize("actor,page", [(0, 0), (True, 0), (99, -1), (99, True), (99, 100001)])
def test_inbox_rejects_invalid_arguments(tmp_path, actor, page):
    with pytest.raises(DomainError):
        Store(tmp_path / "quiet.sqlite3").admin_inbox(actor, page)
