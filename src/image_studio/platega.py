"""Platega checkout ledger and verified one-time crediting. No automatic bank refunds."""

import asyncio
import json
import logging
import secrets
import time
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx

from .admin_store import DomainError
from .pricing import credits_text
from .purchase_demo import PLANS

log = logging.getLogger(__name__)


class PlategaError(Exception):
    pass


def transaction_id(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise PlategaError("invalid_transaction") from None


def amount_kopecks(value, *, allow_zero=False):
    try:
        amount = Decimal(str(value)) * 100
        if (
            not amount.is_finite()
            or amount < 0
            or (amount == 0 and not allow_zero)
            or amount != amount.to_integral_value()
        ):
            raise ValueError
        return int(amount)
    except (ValueError, InvalidOperation, TypeError):
        raise PlategaError("invalid_amount") from None


class PlategaStoreMixin:
    def platega_plans(self, user):
        return dict(PLANS)

    def _platega_init(self, c):
        c.execute("""CREATE TABLE IF NOT EXISTS platega_orders(
            id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,method TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'ready',transaction_id TEXT UNIQUE,url TEXT,
            expires REAL,next_check REAL NOT NULL DEFAULT 0)""")
        c.execute("CREATE INDEX IF NOT EXISTS platega_due ON platega_orders(state,next_check)")
        columns = {row[1] for row in c.execute("PRAGMA table_info(platega_orders)")}
        for column in ("provider_amount", "provider_fee", "commission_type"):
            if column not in columns:
                c.execute(f"ALTER TABLE platega_orders ADD COLUMN {column} INTEGER")
        c.execute("""CREATE TABLE IF NOT EXISTS platega_sessions(
            user_id INTEGER PRIMARY KEY,token TEXT NOT NULL UNIQUE,message_id INTEGER,
            stage TEXT NOT NULL DEFAULT 'plans',pack TEXT,method TEXT,order_id TEXT,
            revision INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS platega_notices(
            order_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL DEFAULT 0)""")

    def platega_order(self, user, pack, method):
        plans = self.platega_plans(user)
        if (
            pack not in plans
            or method not in {"sbp", "crypto"}
            or type(user) is not int
            or not 0 < user < 2**52
        ):
            raise DomainError("invalid_invoice")
        _, price, count = plans[pack]
        with self.tx() as c:
            u = self._user(c, user)
            if not u["consent"] or u["refund_lock"]:
                raise DomainError("consent_required" if not u["consent"] else "refund_needs_support")
            order = uuid.uuid4().hex
            c.execute(
                "INSERT INTO invoices(id,user_id,pack,amount,credits,created,spent_baseline) "
                "VALUES(?,?,?,?,?,?,?)",
                (order, user, pack, price * 100, count, time.time(), u["spent"]),
            )
            c.execute("INSERT INTO platega_orders(id,user_id,method) VALUES(?,?,?)", (order, user, method))
        return self.platega_get(order)

    def platega_get(self, order):
        with self.tx() as c:
            row = c.execute(
                "SELECT i.*,p.method,p.state,p.transaction_id,p.url,p.expires,p.next_check,"
                "p.provider_amount,p.provider_fee,p.commission_type "
                "FROM platega_orders p JOIN invoices i ON i.id=p.id WHERE p.id=?",
                (order,),
            ).fetchone()
            if row is None:
                raise DomainError("unknown_order")
            return dict(row)

    def platega_promote(self, c, user):
        u = self._user(c, user)
        if (
            u["manual_access"]
            or u["trial_reserved"]
            or not c.execute(
                "SELECT 1 FROM platega_orders WHERE user_id=? AND state='paid'", (user,)
            ).fetchone()
        ):
            return
        initial = max(0, 1 - u["trial_used"]) if u["trial_granted"] else 0
        c.execute("UPDATE users SET balance=balance+?,manual_access=1 WHERE id=?", (initial, user))

    def platega_paid(self, c, order, charge):
        p = c.execute("SELECT * FROM platega_orders WHERE id=?", (order,)).fetchone()
        if p is None:
            return
        if p["transaction_id"] != charge or p["state"] == "chargebacked":
            raise DomainError("payment_identity_mismatch")
        c.execute("UPDATE platega_orders SET state='paid' WHERE id=?", (order,))
        c.execute(
            "INSERT OR IGNORE INTO platega_notices(order_id,user_id) VALUES(?,?)", (order, p["user_id"])
        )
        self.platega_promote(c, p["user_id"])

    def platega_session(self, user, *, reset=False):
        with self.tx() as c:
            if reset:
                c.execute("DELETE FROM platega_sessions WHERE user_id=?", (user,))
            c.execute(
                "INSERT OR IGNORE INTO platega_sessions(user_id,token,created) VALUES(?,?,?)",
                (user, secrets.token_hex(10), time.time()),
            )
            return dict(c.execute("SELECT * FROM platega_sessions WHERE user_id=?", (user,)).fetchone())

    def platega_save_session(self, session):
        with self.tx() as c:
            c.execute(
                "UPDATE platega_sessions SET message_id=?,stage=?,pack=?,method=?,order_id=?,revision=? "
                "WHERE user_id=? AND token=?",
                tuple(
                    session[k]
                    for k in [
                        "message_id",
                        "stage",
                        "pack",
                        "method",
                        "order_id",
                        "revision",
                        "user_id",
                        "token",
                    ]
                ),
            )

    def platega_bind(
        self,
        order,
        tx,
        *,
        url=None,
        expires=None,
        provider_amount=None,
        provider_fee=None,
        commission_type=None,
    ):
        with self.tx() as c:
            p = c.execute("SELECT * FROM platega_orders WHERE id=?", (order,)).fetchone()
            if p is None or p["transaction_id"] not in {None, tx}:
                raise PlategaError("transaction_mismatch")
            c.execute(
                "UPDATE platega_orders SET transaction_id=?,url=COALESCE(?,url),"
                "expires=COALESCE(?,expires),state=CASE WHEN state IN ('paid','chargebacked') THEN state "
                "ELSE 'pending' END,next_check=?,provider_amount=COALESCE(?,provider_amount),"
                "provider_fee=COALESCE(?,provider_fee),commission_type=COALESCE(?,commission_type) WHERE id=?",
                (tx, url, expires, time.time() + 15, provider_amount, provider_fee, commission_type, order),
            )
            c.execute(
                "UPDATE invoices SET provider_id=?,confirmation_url=COALESCE(?,confirmation_url) WHERE id=?",
                (tx, url, order),
            )


class PlategaClient:
    def __init__(self, settings, store, client=None):
        self.settings, self.store = settings, store
        self.client = client or httpx.AsyncClient(
            base_url="https://app.platega.io/", timeout=25, follow_redirects=False, trust_env=False
        )

    async def request(self, method, path, body=None):
        try:
            response = await self.client.request(
                method,
                "https://app.platega.io/" + path.lstrip("/"),
                json=body,
                headers={
                    "X-MerchantId": self.settings.platega_merchant_id,
                    "X-Secret": self.settings.platega_secret,
                },
            )
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (httpx.HTTPError, ValueError):
            raise PlategaError("provider_unavailable_or_uncertain") from None

    async def create(self, order):
        p = self.store.platega_get(order)
        if p["transaction_id"]:
            return p
        with self.store.tx() as c:
            claimed = c.execute(
                "UPDATE platega_orders SET state='creating' WHERE id=? AND state='ready'", (order,)
            ).rowcount
        if not claimed:
            raise PlategaError("creation_uncertain_needs_support")
        body = {
            "paymentMethod": 2 if p["method"] == "sbp" else 13,
            "paymentDetails": {"amount": p["amount"] // 100, "currency": "RUB"},
            "description": "Образ: " + credits_text(p["pack"], p["credits"], p["amount"] / 100),
            "payload": order,
            "orderId": order,
            "metadata": {"userId": str(p["user_id"]), "userName": f"user-{p['user_id']}"},
            "return": self.settings.platega_return_url,
            "failedUrl": self.settings.platega_return_url,
        }
        try:
            value = await self.request("POST", "transaction/process", body)
            tx = transaction_id(value.get("transactionId"))
            merchant = value.get("merchantId")
            if merchant and transaction_id(merchant) != transaction_id(self.settings.platega_merchant_id):
                raise PlategaError("merchant_mismatch")
            self.store.platega_bind(order, tx)
            url = value.get("redirect")
            parsed = urlsplit(url) if isinstance(url, str) else None
            if (
                not parsed
                or parsed.scheme != "https"
                or parsed.hostname not in {"pay.platega.io", "app.platega.io"}
                or parsed.username
                or parsed.password
                or parsed.port not in {None, 443}
            ):
                raise PlategaError("invalid_payment_url")
            expires = None
            if isinstance(value.get("expiresIn"), str):
                parts = value["expiresIn"].split(":")
                if len(parts) == 3 and all(part.isdigit() for part in parts):
                    hours, minutes, seconds = map(int, parts)
                    duration = hours * 3600 + minutes * 60 + seconds
                    if 0 < duration <= 604800 and minutes < 60 and seconds < 60:
                        expires = time.time() + duration
            self.store.platega_bind(order, tx, url=url, expires=expires)
            return self.store.platega_get(order)
        except (PlategaError, ValueError):
            with self.store.tx() as c:
                c.execute(
                    "UPDATE platega_orders SET state='uncertain' WHERE id=? AND state='creating'", (order,)
                )
            raise PlategaError("creation_uncertain_needs_support") from None

    async def reconcile(self, tx, order=None, *, callback=None):
        tx = transaction_id(tx)
        value = await self.request("GET", "transaction/" + tx)
        if transaction_id(value.get("id")) != tx:
            raise PlategaError("transaction_mismatch")
        payload = value.get("payload")
        if not isinstance(payload, str) or len(payload) != 32:
            raise PlategaError("order_mismatch")
        try:
            uuid.UUID(payload)
        except ValueError:
            raise PlategaError("order_mismatch") from None
        if order is not None and payload != order:
            raise PlategaError("order_mismatch")
        p = self.store.platega_get(payload)
        details = value.get("paymentDetails")
        if not isinstance(details, dict):
            raise PlategaError("amount_mismatch")
        gross = amount_kopecks(details.get("amount"))
        fee = amount_kopecks(value.get("comission", 0), allow_zero=True)
        kind = value.get("comissionType")
        if fee > gross:
            raise PlategaError("amount_mismatch")
        if gross != p["amount"]:
            # Observed merchant API format: type 0 includes the customer's fee.
            # Require exact integer arithmetic; never accept arbitrary overpayment.
            if type(kind) is not int or kind != 0 or fee <= 0 or gross - fee != p["amount"]:
                raise PlategaError("amount_mismatch")
        merchant = value.get("merchantId", value.get("mechantId"))
        if (
            transaction_id(merchant) != transaction_id(self.settings.platega_merchant_id)
            or details.get("currency") != "RUB"
            or p["transaction_id"] not in {None, tx}
        ):
            raise PlategaError("identity_mismatch")
        status = value.get("status")
        if status not in {"PENDING", "CONFIRMED", "CANCELED", "CHARGEBACKED"}:
            raise PlategaError("invalid_status")
        if callback is not None and (
            transaction_id(callback.get("id")) != tx
            or callback.get("currency") != "RUB"
            or amount_kopecks(callback.get("amount")) not in {p["amount"], gross}
            or callback.get("status") != status
        ):
            raise PlategaError("callback_mismatch")
        if p["state"] == "chargebacked" and status != "CHARGEBACKED":
            raise PlategaError("chargeback_needs_review")
        self.store.platega_bind(
            payload,
            tx,
            provider_amount=gross,
            provider_fee=fee,
            commission_type=kind if type(kind) is int else None,
        )
        if status == "CONFIRMED":
            return self.store.payment(p["user_id"], payload, "RUB", p["amount"], tx, is_test=False)
        if status == "CHARGEBACKED":
            with self.store.tx() as c:
                changed = c.execute(
                    "UPDATE platega_orders SET state='chargebacked' WHERE id=? AND state!='chargebacked'",
                    (payload,),
                ).rowcount
                c.execute("UPDATE users SET refund_lock=1 WHERE id=?", (p["user_id"],))
                if changed:
                    self.store._admin_safe_event(
                        c,
                        "platega_chargeback:" + payload,
                        "questions",
                        {
                            "user_id": p["user_id"],
                            "text": "Возврат/оспаривание Platega. Расход заморожен до проверки заказа "
                            + payload,
                            "created": time.time(),
                        },
                    )
        elif status == "CANCELED" and p["state"] != "paid":
            with self.store.tx() as c:
                c.execute(
                    "UPDATE platega_orders SET state='canceled' WHERE id=? AND state!='paid'", (payload,)
                )
        return False

    async def worker(self, notify):
        with self.store.tx() as c:
            c.execute("UPDATE platega_orders SET state='uncertain' WHERE state='creating'")
            c.execute("UPDATE platega_notices SET state='unknown' WHERE state='running'")
        while True:
            try:
                with self.store.tx() as c:
                    c.execute(
                        "UPDATE platega_orders SET state='expired' WHERE state='pending' AND id IN "
                        " (SELECT id FROM invoices WHERE created<?)",
                        (time.time() - 172800,),
                    )
                    row = c.execute(
                        "SELECT id,transaction_id FROM platega_orders WHERE state='pending' AND "
                        "transaction_id IS NOT NULL AND next_check<=? ORDER BY next_check LIMIT 1",
                        (time.time(),),
                    ).fetchone()
                    if row:
                        c.execute(
                            "UPDATE platega_orders SET next_check=? WHERE id=?", (time.time() + 60, row["id"])
                        )
                if row:
                    await self.reconcile(row["transaction_id"], row["id"])
                with self.store.tx() as c:
                    notice = c.execute(
                        "SELECT * FROM platega_notices WHERE state='queued' AND next_attempt<=? "
                        "ORDER BY rowid LIMIT 1",
                        (time.time(),),
                    ).fetchone()
                    if notice:
                        c.execute(
                            "UPDATE platega_notices SET state='running',attempts=attempts+1 WHERE order_id=?",
                            (notice["order_id"],),
                        )
                if notice:
                    result = "unknown"
                    delay = 0
                    try:
                        order = self.store.platega_get(notice["order_id"])
                        if order["state"] != "paid":
                            result = "skipped"
                        else:
                            await notify(order)
                            result = "sent"
                    except Exception as error:
                        from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

                        if isinstance(error, TelegramRetryAfter) and notice["attempts"] < 4:
                            result, delay = "queued", error.retry_after
                        elif isinstance(error, TelegramForbiddenError):
                            result = "failed"
                    finally:
                        with self.store.tx() as c:
                            c.execute(
                                "UPDATE platega_notices SET state=?,next_attempt=? WHERE order_id=?",
                                (result, time.time() + delay, notice["order_id"]),
                            )
            except Exception:
                log.warning("platega_background_check_failed")
            await asyncio.sleep(2)

    async def close(self):
        await self.client.aclose()


def install_platega_routes(app, settings, gateway):
    from fastapi import Request
    from fastapi.responses import HTMLResponse, JSONResponse

    @app.post("/api/payments/platega/callback")
    async def callback(request: Request):
        try:
            merchant = transaction_id(request.headers.get("x-merchantid", ""))
        except PlategaError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if not secrets.compare_digest(
            merchant, transaction_id(settings.platega_merchant_id)
        ) or not secrets.compare_digest(
            request.headers.get("x-secret", "").encode(), settings.platega_secret.encode()
        ):
            return JSONResponse({"error": "not_found"}, status_code=404)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 8192:
                return JSONResponse({"error": "invalid_body"}, status_code=413)
            data.extend(chunk)
        try:
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError
            await gateway.reconcile(value.get("id"), callback=value)
        except (ValueError, PlategaError, DomainError):
            # Non-2xx asks the provider to retry; no raw payloads/headers are logged.
            return JSONResponse({"error": "payment_not_verified"}, status_code=503)
        return JSONResponse({"ok": True})

    @app.get("/api/payments/platega/result", response_class=HTMLResponse)
    async def result():
        return (
            '<!doctype html><html lang="ru"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Образ · Оплата</title><body style="font:18px system-ui;max-width:480px;margin:10vh auto;padding:24px">'
            "<h1>Образ ·</h1><p>Статус оплаты проверяется автоматически. После подтверждения "
            "генерации появятся в вашем балансе.</p><p>Если платёж отменён, начисления не будет.</p>"
            '<a href="https://t.me/obraz_photo_bot">Вернуться в бот</a></body></html>'
        )
