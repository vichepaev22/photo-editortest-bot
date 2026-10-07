import httpx
import pytest

from image_studio.billing import BillingError, YooKassaClient, create_app
from image_studio.config import Settings
from image_studio.store import Store


async def test_first_refund_of_old_order_uses_fresh_window(billing):
    settings, store, order = billing
    store.bind_payment(order, "test-payment-id")
    store.payment(42, order, "RUB", 14900, "test-payment-id")
    with store.tx() as c:
        c.execute("UPDATE invoices SET created=created-172800 WHERE id=?", (order,))
    calls = []

    async def handle(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=canonical(order))
        return httpx.Response(
            200,
            json={
                "id": "refund-test",
                "payment_id": "test-payment-id",
                "status": "succeeded",
                "amount": {"value": "149.00", "currency": "RUB"},
            },
        )

    gateway = YooKassaClient(
        settings,
        httpx.AsyncClient(base_url="https://api.yookassa.ru/v3/", transport=httpx.MockTransport(handle)),
    )
    assert (await gateway.refund(store, order))["status"] == "succeeded"
    assert store.wallet(42) == (0, 0)
    assert calls == ["GET", "POST"]
    await gateway.close()


async def test_confirmed_canceled_refund_unlocks_credits(billing):
    settings, store, order = billing
    store.bind_payment(order, "test-payment-id")
    store.payment(42, order, "RUB", 14900, "test-payment-id")

    async def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json=canonical(order))
        return httpx.Response(
            200,
            json={
                "id": "refund-test",
                "payment_id": "test-payment-id",
                "status": "canceled",
                "amount": {"value": "149.00", "currency": "RUB"},
            },
        )

    gateway = YooKassaClient(
        settings,
        httpx.AsyncClient(base_url="https://api.yookassa.ru/v3/", transport=httpx.MockTransport(handle)),
    )
    assert (await gateway.refund(store, order))["status"] == "canceled"
    assert store.wallet(42) == (5, 0)
    assert store.reserve(42, "new", "hair", 1)
    assert store.get_invoice(order)["status"] == "refund_canceled"
    await gateway.close()


@pytest.fixture
def billing(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.consent(42)
    settings = Settings(
        billing_enabled=True,
        shop_id="test",
        shop_key="test",
        admin_token="x" * 32,
        return_url="https://example.com/result",
    )
    order = store.invoice(42, "small", 14900, 5)
    return settings, store, order


def canonical(order, status="succeeded", paid=True, value="149.00", test=True):
    return {
        "id": "test-payment-id",
        "test": test,
        "status": status,
        "paid": paid,
        "amount": {"value": value, "currency": "RUB"},
        "metadata": {"order_id": order},
        "confirmation": {"confirmation_url": "https://yookassa.ru/test"},
    }


async def test_forged_webhook_verified_with_api_and_no_credit(billing):
    settings, store, order = billing
    store.bind_payment(order, "test-payment-id")

    async def handle(request):
        return httpx.Response(200, json=canonical(order, "pending", False))

    gateway = YooKassaClient(
        settings,
        httpx.AsyncClient(base_url="https://api.yookassa.ru/v3/", transport=httpx.MockTransport(handle)),
    )
    app = create_app(settings, store, gateway)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhooks/yookassa", json={"event": "payment.succeeded", "object": canonical(order)}
        )
        assert response.status_code == 200
    assert store.wallet(42) == (0, 0)
    await gateway.close()


async def test_verified_success_replay_and_amount_mismatch(billing):
    settings, store, order = billing
    store.bind_payment(order, "test-payment-id")

    async def handle(request):
        return httpx.Response(200, json=canonical(order))

    gateway = YooKassaClient(
        settings,
        httpx.AsyncClient(base_url="https://api.yookassa.ru/v3/", transport=httpx.MockTransport(handle)),
    )
    assert await gateway.reconcile(store, order)
    assert not await gateway.reconcile(store, order)
    assert store.wallet(42) == (5, 0)
    with pytest.raises(BillingError):
        gateway.credit(store, order, canonical(order, value="1.00"))
    with pytest.raises(BillingError):
        gateway.credit(store, order, canonical(order, test=False))
    await gateway.close()


async def test_checkout_same_idempotency_key_after_http_failure(billing):
    settings, store, order = billing
    keys, bodies = [], []

    async def handle(request):
        keys.append(request.headers.get("Idempotence-Key"))
        bodies.append(await request.aread())
        if len(keys) == 1:
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(200, json=canonical(order, "pending", False))

    gateway = YooKassaClient(
        settings,
        httpx.AsyncClient(base_url="https://api.yookassa.ru/v3/", transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(BillingError):
        await gateway.create(store, order)
    await gateway.create(store, order)
    assert keys == [order, order]
    assert bodies[0] == bodies[1]
    assert store.wallet(42) == (0, 0)
    await gateway.close()


async def test_billing_disabled_and_internal_checkout_auth(billing):
    settings, store, order = billing

    async def handle(request):
        raise AssertionError("Unexpected external request")

    gateway = YooKassaClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    app = create_app(settings, store, gateway)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/orders", json={"user_id": 42, "pack": "small"})).status_code == 401
        settings.billing_enabled = False
        assert (await client.post("/webhooks/yookassa", json={})).status_code == 503
    await gateway.close()
