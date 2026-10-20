import uuid
from types import SimpleNamespace

import httpx
import pytest

from image_studio.platega import PlategaClient
from image_studio.pricing import bonus_credits, credits_text
from image_studio.recurring_free import balance_text
from image_studio.store import Store


@pytest.mark.parametrize("pack,price,count,bonus", [("express",240,20,0),("base",480,50,10),("premium",720,75,15)])
async def test_new_price_and_bonus_credit_exactly_once(tmp_path,pack,price,count,bonus):
    store=Store(tmp_path/"ledger.db")
    store.consent(11)
    order=store.platega_order(11,pack,"sbp")
    assert (order["amount"],order["credits"])==(price*100,count)
    merchant,transaction=str(uuid.uuid4()),str(uuid.uuid4())
    value={"id":transaction,"status":"CONFIRMED","mechantId":merchant,
           "paymentDetails":{"amount":price,"currency":"RUB"},"payload":order["id"]}
    settings=SimpleNamespace(platega_merchant_id=merchant,platega_secret="offline",platega_return_url="https://example.com/")
    gateway=PlategaClient(settings,store,httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=value))))
    try:
        assert await gateway.reconcile(transaction,order["id"])
        assert not await gateway.reconcile(transaction,order["id"])
        with store.tx() as c:
            assert c.execute("SELECT balance FROM users WHERE id=11").fetchone()[0]==count
        assert bonus_credits(pack,count,price)==bonus
        assert (f"{bonus} бонусных" in credits_text(pack,count,price)) is bool(bonus)
    finally:
        await gateway.close()


async def test_historic_invoice_still_settles_original_price_and_credits(tmp_path):
    store=Store(tmp_path/"ledger.db")
    store.consent(11)
    order=store.invoice(11,"express",16000,5)
    with store.tx() as c:
        c.execute("INSERT INTO platega_orders(id,user_id,method) VALUES(?,11,'sbp')",(order,))
    merchant,transaction=str(uuid.uuid4()),str(uuid.uuid4())
    value={"id":transaction,"status":"CONFIRMED","mechantId":merchant,
           "paymentDetails":{"amount":160,"currency":"RUB"},"payload":order}
    settings=SimpleNamespace(platega_merchant_id=merchant,platega_secret="offline",platega_return_url="https://example.com/")
    gateway=PlategaClient(settings,store,httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=value))))
    try:
        assert await gateway.reconcile(transaction,order)
        with store.tx() as c:
            assert c.execute("SELECT balance FROM users WHERE id=11").fetchone()[0]==5
        assert bonus_credits("express",5,160)==0
        assert store.platega_get(order)["amount"]==16000
    finally:
        await gateway.close()


def test_balance_shows_total_free_and_purchased_parts():
    text=balance_text({"free_available":2,"extra_available":50,"next_due":None})
    assert "Доступно генераций: 52" in text and "Бесплатные: 2" in text and "дополнительные: 50" in text


def test_old_checkout_displays_stored_price_instead_of_new_catalog(tmp_path):
    from image_studio.platega_checkout import PlategaCheckout

    store=Store(tmp_path/"historic.db")
    store.consent(11)
    order=store.invoice(11,"express",16000,5)
    with store.tx() as c:
        c.execute("INSERT INTO platega_orders(id,user_id,method) VALUES(?,11,'sbp')",(order,))
    session={"user_id":11,"order_id":order,"pack":"express","stage":"link","method":"sbp","token":"offline","revision":0}
    text,_=PlategaCheckout(store,None).render(session)
    assert "Стоимость: 160 ₽" in text and "Генераций: 5" in text
    assert "240 ₽" not in text and "бонусных" not in text


async def test_reopening_buy_replaces_stale_checkout_but_keeps_old_invoice(tmp_path):
    from datetime import datetime, timezone

    from aiogram.types import Chat, Message
    from test_purchase_demo import OfflineBot

    from image_studio.platega_checkout import PlategaCheckout

    store=Store(tmp_path/"old-session.db")
    store.consent(11)
    order=store.invoice(11,"express",16000,5)
    with store.tx() as c:
        c.execute("INSERT INTO platega_orders(id,user_id,method) VALUES(?,11,'sbp')",(order,))
    session=store.platega_session(11)
    previous_token=session["token"]
    session.update(order_id=order,pack="express",method="sbp",stage="link")
    store.platega_save_session(session)
    incoming=Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=11,type="private")).as_(OfflineBot())
    assert await PlategaCheckout(store,None).open(incoming,11)
    current=store.platega_session(11)
    assert current["stage"]=="plans" and current["order_id"] is None and current["token"]!=previous_token
    assert (store.platega_get(order)["amount"],store.platega_get(order)["credits"])==(16000,5)
