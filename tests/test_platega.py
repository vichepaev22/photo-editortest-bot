import uuid
from types import SimpleNamespace

import httpx
import pytest

from image_studio.platega import PlategaClient, PlategaError, install_platega_routes
from image_studio.store import Store

MERCHANT = str(uuid.uuid4())
SECRET = "test-only-secret-never-real"


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "ledger.sqlite3")
    store.record_visit(11)
    store.consent(11)
    store.grant_trial(11)
    settings = SimpleNamespace(
        platega_merchant_id=MERCHANT, platega_secret=SECRET, platega_return_url="https://example.com/return"
    )
    return store, settings


def canonical(order, tx=None, status="CONFIRMED"):
    return {
        "id": tx or str(uuid.uuid4()),
        "status": status,
        "mechantId": MERCHANT,
        "paymentDetails": {"amount": 240, "currency": "RUB"},
        "payload": order,
    }


@pytest.mark.asyncio
async def test_verified_payment_once_and_start_preserved(setup):
    store, settings = setup
    order = store.platega_order(11, "express", "sbp")
    result = canonical(order["id"])

    async def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json=result)

    gateway = PlategaClient(settings, store, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await gateway.reconcile(result["id"], order["id"])
    assert not await gateway.reconcile(result["id"], order["id"])
    assert store.wallet(11, trial=True) == (21, 0)
    assert store.has_manual_access(11)
    assert store.admin_stats()["revenue_kopecks"] == 24000
    with store.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM platega_notices").fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value", [("amount", 159), ("currency", "USD"), ("merchant", "bad"), ("payload", "wrong")]
)
async def test_identity_mismatch_never_credits(setup, field, value):
    store, settings = setup
    order = store.platega_order(11, "express", "sbp")
    result = canonical(order["id"])
    if field in {"amount", "currency"}:
        result["paymentDetails"][field] = value
    else:
        result["mechantId" if field == "merchant" else field] = value
    gateway = PlategaClient(
        settings,
        store,
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=result))),
    )
    with pytest.raises(PlategaError):
        await gateway.reconcile(result["id"], order["id"])
    assert store.wallet(11, trial=True) == (1, 0)


@pytest.mark.asyncio
async def test_unknown_create_is_not_replayed(setup):
    store, settings = setup
    order = store.platega_order(11, "express", "sbp")
    calls = []

    def handler(request):
        calls.append(request.method)
        raise httpx.ReadTimeout("uncertain")

    gateway = PlategaClient(settings, store, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    for _ in range(2):
        with pytest.raises(PlategaError):
            await gateway.create(order["id"])
    assert calls == ["POST"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_buy_during_original_trial_retains_reserve_and_refund(setup, fail):
    store, settings = setup
    job = store.reserve(11, "initial", "hair", 1, trial=True)
    store.claim(job)
    order = store.platega_order(11, "express", "sbp")
    result = canonical(order["id"])
    gateway = PlategaClient(
        settings,
        store,
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=result))),
    )
    await gateway.reconcile(result["id"], order["id"])
    assert store.wallet(11, trial=True) == (21, 1)
    if fail:
        store.fail(job, "failed")
    else:
        store.finish(job, "x.jpg", {}, "test")
    assert store.wallet(11, trial=True) == (21 if fail else 20, 0)


@pytest.mark.asyncio
async def test_chargeback_does_not_credit_again(setup):
    store, settings = setup
    order = store.platega_order(11, "express", "sbp")
    result = canonical(order["id"])
    gateway = PlategaClient(
        settings,
        store,
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=result))),
    )
    await gateway.reconcile(result["id"], order["id"])
    result["status"] = "CHARGEBACKED"
    await gateway.reconcile(result["id"], order["id"])
    with store.tx() as c:
        assert c.execute("SELECT refund_lock FROM users WHERE id=11").fetchone()[0] == 1
    result["status"] = "CONFIRMED"
    with pytest.raises(PlategaError):
        await gateway.reconcile(result["id"], order["id"])
    assert store.wallet(11, trial=True)[0] == 21


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_amount", [240, 254.4])
async def test_verified_customer_fee_is_separate_from_order_price(setup, callback_amount):
    store, settings = setup
    order = store.platega_order(11, "express", "sbp")
    result = canonical(order["id"])
    result.update(comission=14.4, comissionType=0)
    result["paymentDetails"]["amount"] = 254.4
    gateway = PlategaClient(
        settings,
        store,
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=result))),
    )
    body = {"id": result["id"], "amount": callback_amount, "currency": "RUB", "status": "CONFIRMED"}
    assert await gateway.reconcile(result["id"], order["id"], callback=body)
    assert not await gateway.reconcile(result["id"], order["id"], callback=body)
    assert store.wallet(11, trial=True) == (21, 0)
    p = store.platega_get(order["id"])
    assert (p["amount"], p["provider_amount"], p["provider_fee"], p["commission_type"]) == (
        24000,
        25440,
        1440,
        0,
    )
    await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gross,fee,kind",
    [(254.39, 14.4, 0), (254.4, -14.4, 0), (254.4, 14.4, 1), (254.4, 14.4, False), (254.4, None, 0)],
)
async def test_fee_mismatch_never_credits(setup, gross, fee, kind):
    store, settings = setup
    order = store.platega_order(11, "express", "sbp")
    result = canonical(order["id"])
    result.update(comission=fee, comissionType=kind)
    result["paymentDetails"]["amount"] = gross
    gateway = PlategaClient(
        settings,
        store,
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=result))),
    )
    with pytest.raises(PlategaError):
        await gateway.reconcile(result["id"], order["id"])
    assert store.wallet(11, trial=True) == (1, 0)
    await gateway.close()


@pytest.mark.asyncio
async def test_callback_auth_and_duplicate_confirmation(setup):
    from fastapi import FastAPI

    store, settings = setup
    order = store.platega_order(11, "express", "sbp")
    result = canonical(order["id"])
    gateway = PlategaClient(
        settings,
        store,
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=result))),
    )
    app = FastAPI()
    install_platega_routes(app, settings, gateway)
    body = {"id": result["id"], "amount": 240, "currency": "RUB", "status": "CONFIRMED"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://local") as api:
        assert (await api.post("/api/payments/platega/callback", json=body)).status_code == 404
        assert store.wallet(11, trial=True) == (1, 0)
        headers = {"X-MerchantId": MERCHANT, "X-Secret": SECRET}
        for _ in range(2):
            assert (
                await api.post("/api/payments/platega/callback", json=body, headers=headers)
            ).status_code == 200
        assert store.wallet(11, trial=True) == (21, 0)
        assert (await api.get("/api/payments/platega/result")).status_code == 200
        assert store.wallet(11, trial=True) == (21, 0)


def test_payment_test_is_retired_for_everyone(setup):
    from image_studio.platega_checkout import PlategaCheckout
    from image_studio.store import DomainError

    store, _ = setup
    store = Store(store.path, payment_test_owner_id=11)
    checkout = PlategaCheckout(store, SimpleNamespace())
    for user in (11, 22):
        assert set(store.platega_plans(user)) == {"express", "base", "premium"}
        assert "1 ₽ (1 генерация)" not in str(checkout.render(store.platega_session(user))[1])
        with pytest.raises(DomainError):
            store.platega_order(user, "payment_test", "sbp")


@pytest.mark.asyncio
@pytest.mark.parametrize("pack", ["express", "base", "premium"])
async def test_checkout_one_message_and_restart(setup, pack):
    from datetime import datetime, timezone

    from aiogram.types import CallbackQuery, Chat, Message, User
    from test_purchase_demo import OfflineBot

    from image_studio.platega_checkout import PlategaCheckout

    store, settings = setup
    store = Store(store.path, payment_test_owner_id=11)
    bot = OfflineBot()
    tx = str(uuid.uuid4())
    posted = []

    def handler(request):
        import json

        posted.append(request.method)
        assert json.loads(request.content)["paymentDetails"] == {
            "amount": {"express": 240, "base": 480, "premium": 720}[pack],
            "currency": "RUB",
        }
        return httpx.Response(
            200,
            json={
                "transactionId": tx,
                "status": "PENDING",
                "merchantId": MERCHANT,
                "redirect": "https://pay.platega.io/test",
                "expiresIn": "00:15:00",
            },
        )

    gateway = PlategaClient(settings, store, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    checkout = PlategaCheckout(store, gateway)
    incoming = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=11, type="private")).as_(
        bot
    )
    await checkout.open(incoming, 11)
    first = store.platega_session(11)

    async def click(action, actor=11):
        s = store.platega_session(11)
        message = Message(
            message_id=s["message_id"], date=datetime.now(timezone.utc), chat=Chat(id=11, type="private")
        ).as_(bot)
        callback = CallbackQuery(
            id=str(uuid.uuid4()),
            from_user=User(id=actor, is_bot=False, first_name="Test"),
            chat_instance="test",
            message=message,
            data=f"purchase:{s['token']}:{s['revision']}:{action}",
        ).as_(bot)
        await checkout.handle(callback)

    await click("plan:express", actor=22)
    assert store.platega_session(11)["stage"] == "plans"
    await click("plan:" + pack)
    await click("method:sbp")
    assert posted == ["POST"]
    s = store.platega_session(11)
    assert s["message_id"] == first["message_id"]
    before = posted.copy()
    await PlategaCheckout(store, gateway).open(incoming, 11)
    assert posted == before
    reopened = store.platega_session(11)
    assert reopened["message_id"] != s["message_id"]
    assert reopened["order_id"] == s["order_id"]
    assert reopened["revision"] > s["revision"]
    assert len([m for m in bot.sent if m.__api_method__ == "sendMessage"]) == 2
    assert any(m.__api_method__ == "deleteMessage" and m.message_id == s["message_id"] for m in bot.sent)
    assert store.wallet(11, trial=True) == (1, 0)
    text, markup = checkout.render(s)
    assert "Stars" not in text and "Оплачивая" in text
    assert all(
        len(b.callback_data.encode()) <= 64 for row in markup.inline_keyboard for b in row if b.callback_data
    )
    await gateway.close()
    await bot.session.close()


@pytest.mark.asyncio
async def test_historical_one_ruble_payment_still_settles_once(setup):
    store, settings = setup
    order = store.platega_order(11, "express", "sbp")
    with store.tx() as c:
        c.execute("UPDATE invoices SET pack='payment_test',amount=100,credits=1 WHERE id=?", (order["id"],))
    tx = str(uuid.uuid4())
    result = canonical(order["id"], tx)
    result["paymentDetails"]["amount"] = 1.09
    result.update(comission=0.09, comissionType=0)
    gateway = PlategaClient(settings, store, httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=result))))
    assert await gateway.reconcile(tx, order["id"])
    assert not await gateway.reconcile(tx, order["id"])
    assert store.wallet(11, trial=True) == (2, 0)
    assert store.admin_stats()["revenue_kopecks"] == 100
    await gateway.close()
