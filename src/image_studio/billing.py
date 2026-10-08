import json
import re
import secrets
import time
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .catalog import PACKS
from .store import DomainError


class BillingError(Exception):
    pass


class YooKassaClient:
    """Direct documented HTTP API, bounded timeout and durable Idempotence-Key.

    Only test transactions are accepted in this MVP. No external links in the bot.
    """

    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            base_url="https://api.yookassa.ru/v3/",
            auth=(settings.shop_id, settings.shop_key),
            timeout=30,
            follow_redirects=False,
        )

    async def request(self, method, path, body=None, key=None):
        try:
            headers = {"Idempotence-Key": key} if key else {}
            response = await self.client.request(method, path, json=body, headers=headers)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise BillingError("invalid_billing_response")
            return result
        except (httpx.HTTPError, ValueError):
            # Do not release a refund lock on uncertain HTTP results.
            raise BillingError("billing_unavailable_or_uncertain") from None

    async def create(self, store, order, email=""):
        invoice = store.get_invoice(order)
        if invoice["status"] not in {"new", "paid"}:
            raise DomainError("order_closed")
        if invoice["provider_id"]:
            payment = await self.request("GET", "payments/" + invoice["provider_id"])
        else:
            if time.time() - invoice["created"] >= 23 * 3600:
                raise BillingError("expired_idempotency_window_needs_support")
            value = f"{invoice['amount'] / 100:.2f}"
            body = {
                "amount": {"value": value, "currency": "RUB"},
                "capture": True,
                "confirmation": {"type": "redirect", "return_url": self.settings.return_url},
                "description": f"Photo editing: {invoice['credits']} credits",
                "metadata": {"order_id": order},
            }
            if email:
                if len(email) > 200 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                    raise DomainError("invalid_receipt_email")
                body["receipt"] = {
                    "customer": {"email": email},
                    "items": [
                        {
                            "description": body["description"],
                            "quantity": "1.00",
                            "amount": body["amount"],
                            "vat_code": self.settings.vat_code,
                            "payment_subject": "service",
                            "payment_mode": "full_prepayment",
                        }
                    ],
                }
            body = store.billing_body(order, body)
            payment = await self.request("POST", "payments", body, order)
        self.validate_identity(invoice, payment)
        url = payment.get("confirmation", {}).get("confirmation_url")
        if url and not url.startswith("https://"):
            raise BillingError("invalid_confirmation_url")
        store.bind_payment(order, payment["id"], url)
        self.credit(store, order, payment)
        return {
            "order_id": order,
            "payment_id": payment["id"],
            "confirmation_url": url,
            "status": payment["status"],
            "test_only": True,
        }

    @staticmethod
    def validate_identity(invoice, payment):
        try:
            amount = Decimal(payment["amount"]["value"]) * 100
            if (
                payment.get("test") is not True
                or payment["amount"]["currency"] != "RUB"
                or amount != invoice["amount"]
                or payment.get("metadata", {}).get("order_id") != invoice["id"]
                or (invoice["provider_id"] and invoice["provider_id"] != payment["id"])
            ):
                raise BillingError("billing_identity_mismatch_or_live_payment")
        except (KeyError, InvalidOperation, TypeError):
            raise BillingError("invalid_billing_response") from None

    def credit(self, store, order, payment):
        invoice = store.get_invoice(order)
        self.validate_identity(invoice, payment)
        if payment.get("status") == "succeeded" and payment.get("paid") is True:
            return store.payment(invoice["user_id"], order, "RUB", invoice["amount"], payment["id"], is_test=True)
        return False

    async def reconcile(self, store, order):
        invoice = store.get_invoice(order)
        if not invoice["provider_id"]:
            raise DomainError("unknown_payment")
        canonical = await self.request("GET", "payments/" + invoice["provider_id"])
        return self.credit(store, order, canonical)

    async def refund(self, store, order):
        invoice = store.get_invoice(order)
        if invoice["status"] == "paid":
            # Validate before locking a wallet when no refund has been attempted.
            payment = await self.request("GET", "payments/" + invoice["provider_id"])
            self.validate_identity(invoice, payment)
            if payment.get("status") != "succeeded" or payment.get("paid") is not True:
                raise BillingError("payment_not_refundable")
        invoice = store.begin_refund(order)
        # The lock and invoice remain durable if HTTP results are ambiguous.
        if invoice["refund_id"]:
            result = await self.request("GET", "refunds/" + invoice["refund_id"])
        else:
            if invoice["refund_started"] is None or time.time() - invoice["refund_started"] >= 23 * 3600:
                raise BillingError("refund_window_needs_support")
            body = {
                "payment_id": invoice["charge_id"],
                "amount": {"value": f"{invoice['amount'] / 100:.2f}", "currency": "RUB"},
            }
            original = json.loads(invoice["billing_body"] or "{}")
            if "receipt" in original:
                body["receipt"] = original["receipt"]
            result = await self.request("POST", "refunds", body, "refund-" + order)
        try:
            if (
                not isinstance(result.get("id"), str)
                or not result["id"]
                or (invoice["refund_id"] and result["id"] != invoice["refund_id"])
                or result.get("payment_id") != invoice["charge_id"]
                or result["amount"]["currency"] != "RUB"
                or Decimal(result["amount"]["value"]) * 100 != invoice["amount"]
                or result.get("status") not in {"pending", "succeeded", "canceled"}
            ):
                raise BillingError("refund_identity_mismatch")
        except (KeyError, InvalidOperation, TypeError):
            raise BillingError("invalid_refund_response") from None
        store.bind_refund(order, result["id"])
        if result.get("status") == "succeeded":
            store.finish_refund(order)
        elif result.get("status") == "canceled":
            store.cancel_refund(order)
        return {"order_id": order, "refund_id": result["id"], "status": result["status"]}

    async def close(self):
        await self.client.aclose()


class OrderRequest(BaseModel):
    user_id: int = Field(gt=0)
    pack: str
    receipt_email: str = ""


def create_app(settings, store, gateway=None):
    gateway = gateway or YooKassaClient(settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await gateway.close()

    app = FastAPI(title="Image Studio — external billing sandbox", lifespan=lifespan)

    def authorize(authorization):
        if not settings.billing_enabled:
            raise HTTPException(503, "billing_disabled")
        expected = "Bearer " + settings.admin_token
        if not settings.admin_token or not secrets.compare_digest(authorization or "", expected):
            raise HTTPException(401, "unauthorized")

    @app.get("/health")
    async def health():
        return {"status": "ok", "billing_enabled": settings.billing_enabled, "test_only": True}

    @app.post("/orders")
    async def create_order(body: OrderRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        if body.pack not in PACKS:
            raise HTTPException(400, "unknown_pack")
        credits, amount = PACKS[body.pack]
        try:
            order = store.invoice(body.user_id, body.pack, amount, credits)
            # Return durable ID before external request; subsequent retries use /checkout/{order}.
            return {"order_id": order, "amount_kopecks": amount, "credits": credits}
        except DomainError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/checkout/{order}")
    async def checkout(order: str, body: OrderRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        try:
            invoice = store.get_invoice(order)
            if invoice["user_id"] != body.user_id or invoice["pack"] != body.pack:
                raise DomainError("order_owner_mismatch")
            return await gateway.create(store, order, body.receipt_email)
        except DomainError as exc:
            raise HTTPException(409, str(exc)) from None
        except BillingError as exc:
            raise HTTPException(503, str(exc)) from None

    @app.post("/reconcile/{order}")
    async def reconcile(order: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        try:
            return {"credited": await gateway.reconcile(store, order)}
        except (DomainError, BillingError):
            raise HTTPException(503, "payment_reconciliation_failed") from None

    @app.post("/webhooks/yookassa")
    async def webhook(request: Request):
        if not settings.billing_enabled:
            raise HTTPException(503, "billing_disabled")
        raw = await request.body()
        if len(raw) > 32768:
            raise HTTPException(413, "payload_too_large")
        try:
            body = json.loads(raw)
            if body.get("event") != "payment.succeeded":
                return {"ok": True}
            obj = body.get("object") or {}
            order = (obj.get("metadata") or {}).get("order_id")
            invoice = store.get_invoice(order)
            if invoice["provider_id"] != obj.get("id"):
                return {"ok": True}
        except (ValueError, DomainError, AttributeError, TypeError):
            return {"ok": True}
        try:
            await gateway.reconcile(store, order)
        except (BillingError, DomainError):
            # Provider retries non-200; no credit from the untrusted notification body.
            raise HTTPException(503, "verification_pending") from None
        return {"ok": True}

    return app
