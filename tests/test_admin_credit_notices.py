import sqlite3

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from test_admin_delivery import FakeBot

from image_studio.admin_delivery import AdminDelivery
from image_studio.store import DomainError, Store


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "credit-notices.sqlite3")
    store.consent(10)
    store.grant_demo(10, 5)
    return store


@pytest.mark.parametrize("mode,value,delta", [("add", 3, 3), ("set", 8, 3), ("add", -1, 0), ("set", 5, 0)])
def test_only_applied_positive_delta_creates_one_durable_notice(store, mode, value, delta):
    op = store.admin_prepare_credit(99, 10, mode, value, "<Моя пометка> & текст")
    assert store.admin_events() == []
    assert store.admin_confirm_credit(99, op["id"])["applied"]
    reopened = Store(store.path)
    assert not reopened.admin_confirm_credit(99, op["id"])["applied"]
    notices = reopened.admin_events()
    assert len(notices) == int(delta > 0)
    if delta:
        assert notices[0]["payload"] == {"user_id": 10, "credits": delta, "reason": "<Моя пометка> & текст"}
        reopened.admin_configure("enabled", False)
        event = reopened.admin_claim_event()
        assert event["kind"] == "credit_grant"
        assert reopened.admin_credit_notice(event["id"], 99) == notices[0]["payload"]


def test_canceled_or_failed_notification_transaction_does_not_apply_quota(store, monkeypatch):
    op = store.admin_prepare_credit(99, 10, "add", 3, "Таков путь")
    store.admin_cancel_credit(99, op["id"])
    with pytest.raises(DomainError):
        store.admin_confirm_credit(99, op["id"])
    op = store.admin_prepare_credit(99, 10, "add", 3, "Таков путь")
    original = store._admin_enqueue

    def broken(*args):
        original(*args)
        raise sqlite3.OperationalError("failed before commit")

    monkeypatch.setattr(store, "_admin_enqueue", broken)
    with pytest.raises(sqlite3.OperationalError):
        store.admin_confirm_credit(99, op["id"])
    assert store.wallet(10) == (5, 0) and store.admin_events() == []
    assert store.admin_credit_history(10) == []
    monkeypatch.setattr(store, "_admin_enqueue", original)
    assert store.admin_confirm_credit(99, op["id"])["applied"]
    assert store.wallet(10) == (8, 0) and len(store.admin_events()) == 1


async def test_notice_routes_from_operation_ignores_payload_and_delivers_once_after_restart(store):
    op = store.admin_prepare_credit(99, 10, "add", 3, "<Моя пометка> & текст")
    store.admin_confirm_credit(99, op["id"])
    with store.tx() as c:
        c.execute("UPDATE admin_outbox SET payload=?", ('{"user_id":-123,"target":88,"reason":"forged"}',))
    store.admin_configure("enabled", False)
    bot = FakeBot()
    assert await AdminDelivery(bot, Store(store.path), 99).process_one()
    assert bot.calls[0][0] == 10
    assert "+3" in bot.calls[0][1] and "&lt;Моя пометка&gt; &amp; текст" in bot.calls[0][1]
    assert "forged" not in bot.calls[0][1] and bot.calls[0][2]["reply_markup"] is None
    assert not await AdminDelivery(bot, Store(store.path), 99).process_one()
    assert len(bot.calls) == 1 and store.wallet(10) == (8, 0)


async def test_blocked_recipient_keeps_grant_and_records_failure(store):
    op = store.admin_prepare_credit(99, 10, "add", 3, "Таков путь")
    store.admin_confirm_credit(99, op["id"])
    bot = FakeBot()
    bot.failure = TelegramForbiddenError(method=SendMessage(chat_id=10, text="test"), message="blocked")
    assert await AdminDelivery(bot, store, 99).process_one()
    assert store.admin_events()[0]["status"] == "failed"
    assert store.wallet(10) == (8, 0)


async def test_forged_recipient_event_or_wrong_actor_cannot_send(store):
    with pytest.raises(DomainError):
        store.admin_event("forged", "credit_grant", {"user_id": 10})
    with store.tx() as c:
        store._admin_enqueue(c, "forged", "credit_grant", {"user_id": 10})
    bot = FakeBot()
    assert await AdminDelivery(bot, store, 99).process_one()
    assert bot.calls == []
    op = store.admin_prepare_credit(99, 10, "add", 3, "Таков путь")
    store.admin_confirm_credit(99, op["id"])
    assert await AdminDelivery(bot, store, 98).process_one()
    assert bot.calls == [] and store.wallet(10) == (8, 0)
