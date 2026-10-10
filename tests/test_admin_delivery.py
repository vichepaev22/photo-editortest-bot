import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage

from image_studio.admin_delivery import AdminDelivery, notification_text

OWNER = 77
JOB = "a" * 32


class FakeStore:
    """Reuse durable state across delivery instances to model a restart."""

    def __init__(self, *events):
        self.rows = []
        self.enabled = True
        self.categories = {}
        self.recoveries = 0
        for index, (kind, payload) in enumerate(events):
            event_id = f"{index + 1:032x}"
            self.rows.append(dict(id=event_id, event_key=event_id, kind=kind,
                                  payload=payload, status="pending", attempts=0, message_id=None))

    def admin_recover_events(self):
        self.recoveries += 1
        for row in self.rows:
            if row["status"] == "running":
                row["status"] = "needs_review"

    def admin_claim_event(self):
        for row in self.rows:
            if row["status"] != "pending":
                continue
            if not self.enabled or not self.categories.get(row["kind"], True):
                continue
            row["attempts"] += 1
            row["status"] = "running"
            return row.copy()
        return None

    def admin_finish_event(self, event_id, message_id):
        row = next(row for row in self.rows if row["id"] == event_id)
        row.update(status="sent", message_id=message_id)

    def admin_fail_event(self, event_id, *, uncertain=False, retry_after=None):
        row = next(row for row in self.rows if row["id"] == event_id)
        row["retry_after"] = retry_after
        row["status"] = ("needs_review" if uncertain else
                         "pending" if retry_after is not None and row["attempts"] < 3 else "failed")


class FakeBot:
    def __init__(self):
        self.calls = []
        self.failure = None

    async def send_message(self, target, text, **kwargs):
        self.calls.append((target, text, kwargs))
        if self.failure:
            raise self.failure
        return SimpleNamespace(message_id=len(self.calls) + 100)


@pytest.mark.parametrize("error", [TimeoutError(), TelegramNetworkError(
    method=SendMessage(chat_id=OWNER, text="x"), message="ambiguous send")])
async def test_ambiguous_send_and_restart_require_review(error):
    store = FakeStore(("registration", {"user_id": 11}))
    bot = FakeBot()
    bot.failure = error
    assert await AdminDelivery(bot, store, OWNER).process_one()
    assert store.rows[0]["status"] == "needs_review"
    assert not await AdminDelivery(bot, store, OWNER).process_one()
    assert len(bot.calls) == 1


async def test_startup_recovers_crashed_claim_and_preserves_pending_event():
    store = FakeStore(("generation", {"user_id": 11}), ("registration", {"user_id": 12}))
    store.rows[0]["status"] = "running"
    bot = FakeBot()
    assert await AdminDelivery(bot, store, OWNER).process_one()
    assert [row["status"] for row in store.rows] == ["needs_review", "sent"]
    assert [call[0] for call in bot.calls] == [OWNER]


async def test_mute_and_category_pause_preserve_pending_events():
    store = FakeStore(("registration", {"user_id": 11}), ("questions", {"user_id": 12, "text": "Как купить?"}))
    store.enabled = False
    bot = FakeBot()
    delivery = AdminDelivery(bot, store, OWNER)
    assert not await delivery.process_one()
    assert all(row["status"] == "pending" for row in store.rows)
    store.enabled = True
    store.categories["registration"] = False
    assert await delivery.process_one()
    assert store.rows[0]["status"] == "pending"
    assert not await delivery.process_one()
    store.categories["registration"] = True
    assert await delivery.process_one()
    assert all(row["status"] == "sent" for row in store.rows)
    assert len(bot.calls) == 2
    assert store.recoveries == 1


async def test_known_throttling_is_bounded_and_success_never_repeated():
    store = FakeStore(("registration", {"user_id": 11}))
    bot = FakeBot()
    bot.failure = TelegramRetryAfter(method=SendMessage(chat_id=OWNER, text="x"), message="wait", retry_after=40)
    delivery = AdminDelivery(bot, store, OWNER)
    for _ in range(3):
        assert await delivery.process_one()
    assert not await delivery.process_one()
    assert store.rows[0]["status"] == "failed"
    assert store.rows[0]["retry_after"] == 40
    assert len(bot.calls) == 3

    bot.failure = None
    store = FakeStore(("registration", {"user_id": 12}))
    delivery = AdminDelivery(bot, store, OWNER)
    assert await delivery.process_one()
    assert not await AdminDelivery(bot, store, OWNER).process_one()
    assert store.rows[0]["status"] == "sent"
    assert store.rows[0]["message_id"] == 104


async def test_forbidden_owner_is_terminal():
    store = FakeStore(("registration", {"user_id": 11}))
    bot = FakeBot()
    bot.failure = TelegramForbiddenError(method=SendMessage(chat_id=OWNER, text="x"), message="blocked")
    delivery = AdminDelivery(bot, store, OWNER)
    assert await delivery.process_one()
    assert store.rows[0]["status"] == "failed"
    assert not await delivery.process_one()


async def test_untrusted_targets_and_unknown_kinds_cannot_redirect_delivery():
    store = FakeStore(("registration", {"user_id": 11, "chat_id": -100, "target": 44}),
                      ("announcement", {"channel": "@intruder", "text": "Post this"}))
    bot = FakeBot()
    delivery = AdminDelivery(bot, store, OWNER)
    assert await delivery.process_one()
    assert await delivery.process_one()
    assert [call[0] for call in bot.calls] == [OWNER]
    assert store.rows[1]["status"] == "failed"
    with pytest.raises(ValueError, match="invalid_admin_user_id"):
        AdminDelivery(bot, store, -100)


async def test_malformed_payload_is_bounded_escaped_and_private_fields_ignored():
    payload = dict(user_id='1"><a href="https://evil">x', username="</a>",
                   text="&" * 1000, created="<time>", description="SECRET_PROMPT",
                   result="SECRET_RESULT", photo="SECRET_PHOTO")
    store = FakeStore(("questions", payload), ("generation", dict(payload, preset="<hair>",
                     status=[], error_category={"private": "SECRET_ERROR"}, job_id="../private")))
    bot = FakeBot()
    delivery = AdminDelivery(bot, store, OWNER)
    assert await delivery.process_one()
    assert await delivery.process_one()
    for _, text, kwargs in bot.calls:
        assert len(text) < 4096
        assert "SECRET" not in text
        assert "https://evil" not in text
        assert "<time>" not in text
        assert kwargs["parse_mode"] == "HTML"
        callbacks = [button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row]
        assert not any(value.startswith(("admin:user:", "admin:job:")) for value in callbacks)
    assert "&amp;" in bot.calls[0][1]
    assert "&lt;hair&gt;" in bot.calls[1][1]


async def test_cancellation_during_send_requires_review_and_propagates():
    store = FakeStore(("registration", {"user_id": 11}))
    bot = FakeBot()
    bot.failure = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await AdminDelivery(bot, store, OWNER).process_one()
    assert store.rows[0]["status"] == "needs_review"
    assert not await AdminDelivery(bot, store, OWNER).process_one()


async def test_success_without_persisted_receipt_is_not_automatically_repeated():
    class ReceiptFailureStore(FakeStore):
        def admin_finish_event(self, event_id, message_id):
            raise ValueError("receipt write failed after Telegram accepted the send")

    store = ReceiptFailureStore(("registration", {"user_id": 11}))
    bot = FakeBot()
    assert await AdminDelivery(bot, store, OWNER).process_one()
    assert store.rows[0]["status"] == "needs_review"
    assert not await AdminDelivery(bot, store, OWNER).process_one()
    assert len(bot.calls) == 1


async def test_categories_owner_buttons_and_localized_generation_metadata():
    store = FakeStore(("registration", {"user_id": 11}),
                      ("questions", {"user_id": 12, "text": "Как пользоваться?"}),
                      ("generation", {"user_id": 13, "status": "generated", "job_id": JOB,
                                      "preset": "glasses", "occurred": 0}),
                      ("payment", {"user_id": 14, "amount": 9900, "currency": "RUB"}))
    bot = FakeBot()
    delivery = AdminDelivery(bot, store, OWNER)
    for _ in range(4):
        assert await delivery.process_one()
    assert not await delivery.process_one()
    assert all(row["status"] == "sent" for row in store.rows)
    assert [call[0] for call in bot.calls] == [OWNER] * 4
    _, text, kwargs = bot.calls[2]
    assert "Генерация: завершена" in text
    assert "Функция: Очки" in text
    assert "01.01.1970 05:00:00 UTC+5" in text
    callbacks = [button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row]
    assert callbacks == ["admin:user:13", f"admin:job:{JOB}", f"admin:event:{store.rows[2]['id']}", "admin:events:0"]


def test_test_payment_rejected_and_production_amount_rendered_in_minor_units():
    payload = dict(user_id=11, amount=9900, currency="RUB", order_id="order", credits=5)
    assert "99.00 RUB" in notification_text("payment", payload)
    with pytest.raises(ValueError, match="test_payment_notification"):
        notification_text("payment", dict(payload, is_test=True))
    with pytest.raises(ValueError, match="unknown_admin_event_kind"):
        notification_text("description", {})


@pytest.mark.parametrize("created", [True, None, float("inf"), -10**100, 10**100])
def test_invalid_timestamp_never_stops_notification(created):
    assert "Время: —" in notification_text("registration", {"created": created})
