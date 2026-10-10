"""Durable owner events and recipient credit notices; never modifies quota."""

import asyncio
import html
import logging
import math
import re
from datetime import datetime, timedelta, timezone

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .catalog import PRESETS
from .store import DomainError

log = logging.getLogger(__name__)


def _value(payload, key, limit=100, *, escaped_limit=2800):
    value = payload.get(key)
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return "—"
    # Bound Telegram's message length without splitting HTML entities.
    parts, length = [], 0
    for char in str(value)[:limit]:
        escaped = html.escape(char, quote=True)
        if length + len(escaped) > escaped_limit:
            return "".join(parts) + "…"
        parts.append(escaped)
        length += len(escaped)
    return "".join(parts)


def _time(payload):
    value = payload.get("occurred", payload.get("created"))
    if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
        return "—"
    try:
        return datetime.fromtimestamp(value, timezone(timedelta(hours=5))).strftime("%d.%m.%Y %H:%M:%S UTC+5")
    except (OverflowError, OSError, ValueError):
        return "—"


def _preset(payload):
    preset = payload.get("preset")
    if isinstance(preset, str) and preset in PRESETS:
        return html.escape(PRESETS[preset].label)
    return _value(payload, "preset", 64)


def notification_keyboard(kind, payload, event_id):
    """Only owner panel callbacks; payload targets and arbitrary IDs are ignored."""
    rows = []
    user = payload.get("user_id")
    if type(user) is int and 0 < user <= 2**63 - 1:
        rows.append([InlineKeyboardButton(text="Пользователь", callback_data=f"admin:user:{user}")])
    job = payload.get("job_id")
    if kind == "generation" and isinstance(job, str) and re.fullmatch(r"[0-9a-f]{32}", job):
        rows.append([InlineKeyboardButton(text="Задание", callback_data=f"admin:job:{job}")])
    if isinstance(event_id, str) and re.fullmatch(r"[0-9a-f]{32}", event_id):
        rows.append([InlineKeyboardButton(text="Подробнее", callback_data=f"admin:event:{event_id}")])
    rows.append([InlineKeyboardButton(text="События", callback_data="admin:events:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _user(payload):
    user = payload.get("user_id")
    if type(user) is int and 0 < user <= 2**63 - 1:
        profile = f'<a href="tg://user?id={user}">{user}</a>'
    else:
        profile = "—"
    username = payload.get("username")
    if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        profile += f" · @{username}"
    return profile


def notification_text(kind, payload):
    """Render an explicit metadata allowlist; prompt, result and photo fields are ignored."""
    if not isinstance(payload, dict):
        raise ValueError("invalid_admin_payload")
    user = "Пользователь: " + _user(payload)
    created = "Время: " + _time(payload)
    if kind == "registration":
        consent = "да" if payload.get("consent") is True else "нет"
        lines = ["<b>Первое посещение</b>", user, created, "Согласие: " + consent]
    elif kind == "questions":
        lines = ["<b>Обращение пользователя</b>", user, created,
                 "Сообщение: " + _value(payload, "text", 1000)]
    elif kind == "generation":
        status = {
            "started": "начата", "running": "начата", "complete": "завершена",
            "done": "завершена", "generated": "завершена", "delivered": "выдана",
            "error": "ошибка", "failed": "ошибка",
            "review": "требует проверки", "needs_review": "требует проверки",
        }.get(payload.get("status") if isinstance(payload.get("status"), str) else "", "событие")
        lines = [f"<b>Генерация: {status}</b>", user, created,
                 "Задание: " + _value(payload, "job_id", 64),
                 "Функция: " + _preset(payload),
                 "Расход: " + _value(payload, "cost", 16)]
        reserve = {"reserved": "зарезервирован", "released": "возвращён", "spent": "списан",
                   "retained": "сохранён до проверки", "exempt": "не списывается (тестирование владельца)"}.get(
                       payload.get("reserve_action") if isinstance(payload.get("reserve_action"), str) else "")
        if reserve:
            lines.append("Резерв: " + reserve)
        if payload.get("error_category"):
            # Raw provider errors can contain user prompts, URLs or credentials.
            lines.append("Ошибка: " + {
                "provider_error": "сбой обработки", "timeout": "превышено время ожидания",
                "recovery": "прервано перезапуском", "delivery": "сбой выдачи результата",
            }.get(payload.get("error_category") if isinstance(payload.get("error_category"), str) else "",
                  "сбой сервиса"))
    elif kind == "payment":
        if payload.get("is_test") is True:
            raise ValueError("test_payment_notification")
        amount = payload.get("amount")
        currency = payload.get("currency")
        if type(amount) is not int or not 0 < amount <= 2**63 - 1 or not isinstance(currency, str):
            raise ValueError("invalid_payment_notification")
        lines = ["<b>Подтверждённая оплата</b>", user, created,
                 f"Сумма: {amount // 100}.{amount % 100:02d} {_value(payload, 'currency', 8)}",
                 "Заказ: " + _value(payload, "order_id", 64),
                 "Начислено: " + _value(payload, "credits", 16)]
    else:
        raise ValueError("unknown_admin_event_kind")
    return "\n".join(lines)


class AdminDelivery:
    def __init__(self, bot, store, admin_user_id):
        if type(admin_user_id) is not int or not 0 < admin_user_id <= 2**63 - 1:
            raise ValueError("invalid_admin_user_id")
        self.bot = bot
        self.store = store
        self.admin_user_id = admin_user_id
        self._recovered = False

    async def process_one(self):
        if not self._recovered:
            self.store.admin_recover_events()
            self._recovered = True
        row = self.store.admin_claim_event()
        if row is None:
            return False
        event_id = row["id"]
        send_started = False
        try:
            payload = row["payload"]
            target = self.admin_user_id
            if row["kind"] == "credit_grant":
                notice = self.store.admin_credit_notice(event_id, self.admin_user_id)
                target = notice["user_id"]
                text = ("🎁 <b>Вам начислены дополнительные генерации</b>\n"
                        f"Количество: <b>+{notice['credits']}</b>\n\n"
                        "Пометка администратора:\n" + _value(notice, "reason", 240))
                keyboard = None
            else:
                text = notification_text(row["kind"], payload)
                keyboard = notification_keyboard(row["kind"], payload, event_id)
            send_started = True
            message = await self.bot.send_message(
                target, text, parse_mode="HTML", disable_web_page_preview=True,
                reply_markup=keyboard,
            )
            self.store.admin_finish_event(event_id, message.message_id)
        except asyncio.CancelledError:
            # Interrupted sends must not be automatically repeated after restart.
            self.store.admin_fail_event(event_id, uncertain=send_started, retry_after=None if send_started else 30)
            raise
        except TelegramRetryAfter as exc:
            self.store.admin_fail_event(event_id, retry_after=max(1, exc.retry_after))
        except (TelegramForbiddenError, TelegramBadRequest, TelegramUnauthorizedError):
            self.store.admin_fail_event(event_id)
        except (ValueError, DomainError):
            self.store.admin_fail_event(event_id, uncertain=send_started)
        except (TelegramNetworkError, TimeoutError):
            self.store.admin_fail_event(event_id, uncertain=send_started, retry_after=None if send_started else 30)
        except Exception as exc:
            # No exception text or payload in logs: either may contain private data.
            log.warning("admin_delivery_failed type=%s", type(exc).__name__)
            self.store.admin_fail_event(event_id, uncertain=send_started, retry_after=None if send_started else 30)
        return True

    async def worker(self):
        while True:
            try:
                if not await self.process_one():
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("admin_outbox_failed type=%s", type(exc).__name__)
                await asyncio.sleep(5)
