import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import BaseMiddleware, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .catalog import PRESETS
from .store import DomainError, Store, validated_username

_PAGE_CALLBACK = re.compile(r"admin:(?:page|refresh):(0|[1-9][0-9]{0,8})", re.ASCII)
_DENIED = "Доступ запрещён."
_ID = r"[1-9][0-9]{0,15}"
_UUID = r"[a-f0-9]{32}"
_PAGE = r"(?:0|[1-9][0-9]{0,8})"
_CATEGORIES = {"enabled": "Все уведомления", "registration": "Первые посещения", "questions": "Вопросы", "generation": "Генерации", "payment": "Реальные оплаты"}
_EVENT_NAMES = _CATEGORIES | {"credit_grant": "Начисление пользователю"}
_DEFAULT_REASON = "Таков путь"
_TEXT_KINDS = {"search", "credit_value", "credit_reason"}
_FILTERS = {"all": "Все", "visited": "Заходили", "paying": "Покупали"}
_LOCAL_TIME = timezone(timedelta(hours=5))
_DELIVERY = {"pending": "в очереди", "running": "отправляется", "sent": "отправлено", "failed": "не доставлено", "needs_review": "нужно проверить доставку"}
_CREDIT_STATUS = {"pending": "ожидает подтверждения", "applied": "применено", "canceled": "отменено", "expired": "срок истёк", "stale": "остаток изменился"}
_JOB_STATUS = {"queued": "в очереди", "running": "обрабатывается", "generated": "готово", "delivered": "получено пользователем", "failed": "ошибка", "review": "нужна проверка", "started": "запущено", "completed": "готово", "success": "готово"}
_RESERVE_ACTION = {"reserved": "зарезервировано", "spent": "списано", "released": "возвращено", "kept": "сохранено"}


def _button(text, data):
    style = None
    if data in {"admin:filter:paying", "admin:filter:visited"} or data.startswith(("admin:confirm:", "admin:reason:")):
        style = "success"
    elif data in {"admin:filter:all", "admin:search", "admin:settings", "admin:home"} or data.startswith("admin:events:"):
        style = "primary"
    elif data.startswith("admin:credit:"):
        action = data.rsplit(":", 1)[-1]
        style = "success" if action in {"p5", "p10"} else "danger" if action == "m1" else "primary"
    elif data.startswith("admin:setting:") and data.endswith(":1"):
        style = "success"
    return InlineKeyboardButton(text=text, callback_data=data, style=style)


def _keyboard(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button(text, data) for text, data in row]
        for row in rows if row
    ])


def _home_row():
    return [("← Администрирование", "admin:home")]


def _timestamp(value):
    return datetime.fromtimestamp(value, _LOCAL_TIME).strftime("%d.%m.%Y %H:%M ЕКБ") if value else "—"


def _short(value, limit=64):
    return str(value if value is not None else "—")[:limit]


def _money(value):
    return f"{value // 100},{value % 100:02d} ₽" if type(value) is int and value >= 0 else "—"


def _status(value, names):
    return names.get(value, "неизвестно")


def _preset(value):
    return PRESETS[value].label if isinstance(value, str) and value in PRESETS else "—"


def _account(user):
    user_id = int(user["id"])
    username = validated_username(user.get("username"))
    if username:
        return f'<a href="https://t.me/{escape(username, quote=True)}">@{escape(username)}</a> (<code>{user_id}</code>)'
    return f'<a href="tg://user?id={user_id}">{user_id}</a>'


def _private_human(message, user):
    return (
        isinstance(message, Message)
        and user is not None
        and not user.is_bot
        and 0 < user.id < 2**52
        and message.chat.type == "private"
        and message.chat.id == user.id
    )


class PrivateVisitMiddleware(BaseMiddleware):
    def __init__(self, store: Store):
        self.store = store

    async def __call__(self, handler, event, data):
        message = event.message if isinstance(event, CallbackQuery) else event
        user = event.from_user
        if _private_human(message, user):
            self.store.record_visit(user.id, user.username)
        return await handler(event, data)


def _render(stats, *, listing=False, query="", filter="all"):
    lines = [
        "<b>Статистика бота</b>",
        f"Заходили в бот: {stats.get('visited_users', stats['total_users'])}",
        f"Всего учётных записей: {stats['total_users']} · Покупали: {stats['paying_users']}",
        f"Успешных генераций: {stats['generated_count']}",
        "",
    ]
    if listing:
        lines.append(f"Список: {_FILTERS[filter]}" + (f" · Поиск: {escape(query)}" if query else ""))
    for user in stats["users"]:
        user_id = user["id"]
        username = validated_username(user["username"])
        if username:
            account = (
                f'<a href="https://t.me/{escape(username, quote=True)}">@{escape(username)}</a>'
                f" (<code>{user_id}</code>)"
            )
        else:
            account = f'<a href="tg://user?id={user_id}">{user_id}</a>'
        lines.append(f"{account} · {user['generated_count']} ген. · {user['purchase_count']} покуп.")
    if not stats["users"]:
        lines.append("Пользователей не найдено." if listing else "Пользователей пока нет.")
    lines.extend(["", f"Страница {stats['page'] + 1}/{stats['pages']}"])
    lines.append("Ссылки по ID зависят от настроек Telegram.")
    page = stats["page"]
    prefix = "admin:found" if listing else "admin:page"
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(text="←", callback_data=f"{prefix}:{page - 1}"))
    if page + 1 < stats["pages"]:
        navigation.append(InlineKeyboardButton(text="→", callback_data=f"{prefix}:{page + 1}"))
    keyboard = [navigation] if navigation else []
    keyboard.append([InlineKeyboardButton(text="Обновить", callback_data=f"admin:found:{page}" if listing else f"admin:refresh:{page}")])
    for user in stats["users"]:
        username = validated_username(user.get("username"))
        label = f"Открыть @{username}" if username else f"Карточка {user['id']}"
        keyboard.append([InlineKeyboardButton(text=label, callback_data=f"admin:user:{user['id']}")])
    keyboard.extend([
        [_button(label, "admin:filter:" + key) for key, label in _FILTERS.items()],
        [_button("🔎 Найти пользователя", "admin:search")],
        [_button("События", "admin:events:0"), _button("Уведомления", "admin:settings")],
    ])
    if listing:
        keyboard.append([_button("← Администрирование", "admin:home")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)


class AdminController:
    """Owner-only durable UI; active() lets integrations exclude wizard replies."""

    def __init__(self, store, admin_user_id, *, media=None, trial_access=False, unlimited_user_id=0):
        self.store = store
        self.admin_user_id = admin_user_id
        self.media = media
        self.trial_access = trial_access
        self.unlimited_user_id = unlimited_user_id

    def owner(self, message, user):
        return (
            type(self.admin_user_id) is int
            and 0 < self.admin_user_id < 2**52
            and _private_human(message, user)
            and user.id == self.admin_user_id
        )

    def active(self, message):
        if not self.owner(message, message.from_user):
            return False
        session = self.store.admin_session(self.admin_user_id)
        return bool(session and session["kind"] in _TEXT_KINDS)

    def _session(self, kind, data, *, message=None):
        data = dict(data)
        previous = self.store.admin_session(self.admin_user_id)
        panel_id = message.message_id if message else (previous or {}).get("data", {}).get("_panel_id")
        if panel_id:
            data["_panel_id"] = panel_id
        self.store.admin_session_set(self.admin_user_id, kind, data)

    def _bound(self, kind, key, value):
        session = self.store.admin_session(self.admin_user_id)
        if not session or session["kind"] != kind or session["data"].get(key) != value:
            raise DomainError("stale_admin_session")
        return session["data"]

    async def _show(self, message, text, keyboard, *, edit=False, panel_id=None, html=True):
        options = dict(parse_mode="HTML" if html else None, reply_markup=keyboard, disable_web_page_preview=True)
        try:
            if panel_id:
                await message.bot.edit_message_text(text, chat_id=self.admin_user_id, message_id=panel_id, **options)
            else:
                await (message.edit_text if edit else message.answer)(text, **options)
        except TelegramBadRequest as error:
            if not (edit or panel_id) or "message is not modified" not in str(error).lower():
                raise

    async def home(self, message, *, edit=False, page=0, panel_id=None):
        stats = self.store.admin_stats(page=page)
        if stats["page"] != page:
            raise DomainError("invalid_admin_page")
        await self._show(message, *_render(stats), edit=edit, panel_id=panel_id)

    async def users(self, message, *, query="", page=0, filter="all", edit=False, panel_id=None):
        stats = self.store.admin_search(query, page=page, filter=filter)
        if stats["page"] != page:
            raise DomainError("invalid_admin_page")
        data = {"query": query, "filter": filter}
        if panel_id:
            data["_panel_id"] = panel_id
        self._session("user_list", data, message=message if edit else None)
        await self._show(message, *_render(stats, listing=True, query=query, filter=filter), edit=edit, panel_id=panel_id)

    async def card(self, message, user_id, *, edit=False):
        user = self.store.admin_user(user_id, trial=self.trial_access)
        unlimited = user_id == self.unlimited_user_id
        access = "безлимит владельца" if unlimited else ("конечный остаток" if user["manual_access"] or not self.trial_access else "пробный доступ")
        text = (
            f"<b>Пользователь</b> {_account(user)}\n\n"
            f"Доступно: {'∞' if unlimited else user['active_available']}\nВ обработке: {user['reserved']}\n"
            f"Всего неиспользовано: {user['available']}\n"
            f"Базовая бесплатная: {user.get('trial_remaining', 0)} осталось\n"
            f"Купленные / начисленные: {user.get('paid_available', user['balance'])} доступно\n"
            f"Бонус за подписки: {'получен' if user.get('channel_bonus') else 'ещё не получен'}\n\n"
            f"Успешных генераций: {user['generated_count']} · Реальных покупок: {user['purchase_count']}\n"
            f"Оплачено всего: {_money(user.get('revenue_kopecks'))}\n"
            f"Доступ: {access}\nРучное начисление: {'да' if user['manual_access'] else 'нет'}\n"
            f"Согласие на обработку: {'есть' if user.get('consent') else 'нет'}\n\n"
            f"Первое посещение: {_timestamp(user.get('first_seen'))}\nПоследнее посещение: {_timestamp(user.get('last_seen'))}\n"
            f"Передача описания и результата: {'включена' if user['share_enabled'] else 'выключена'}\n"
            + ("\nБезлимит владельца не меняется через остаток." if unlimited else "\nИзменения сохраняют резерв выполняемых заданий.")
        )
        rows = [] if unlimited else [
            [("+5", f"admin:credit:{user_id}:p5"), ("+10", f"admin:credit:{user_id}:p10"), ("−1", f"admin:credit:{user_id}:m1")],
            [("Изменить на число ±", f"admin:credit:{user_id}:delta"), ("Задать остаток", f"admin:credit:{user_id}:set")],
        ]
        await self._show(message, text, _keyboard(*rows, [("История изменений", f"admin:audit:{user_id}")], _home_row()), edit=edit)

    async def settings(self, message, *, edit=False):
        settings = self.store.admin_settings()
        lines = ["<b>Уведомления владельцу</b>", "Заглушение сохраняет события в очереди до включения уведомлений."]
        rows = []
        for key, label in _CATEGORIES.items():
            enabled = bool(settings[key])
            lines.append(f"{label}: {'включены' if enabled else 'выключены'}")
            rows.append([(f"{'Выключить' if enabled else 'Включить'}: {label}", f"admin:setting:{key}:{int(not enabled)}")])
        await self._show(message, "\n".join(lines), _keyboard(*rows, _home_row()), edit=edit)

    async def consume_text(self, message):
        if not self.active(message) or not message.text:
            return False
        session = self.store.admin_session(self.admin_user_id)
        if not session:
            return False
        text, kind, data = message.text, session["kind"], session["data"]
        panel_id = data.get("_panel_id")
        if message.text in {"/cancel", "Отмена"}:
            self.store.admin_session_clear(self.admin_user_id)
            await self.home(message, panel_id=panel_id)
            return True
        try:
            if kind == "search":
                query = text.strip()
                if not query or len(query) > 64:
                    raise DomainError("invalid_search")
                await self.users(message, query=query, filter=data.get("filter", "all"), panel_id=panel_id)
            elif kind == "credit_value":
                if data["user_id"] == self.unlimited_user_id:
                    raise DomainError("owner_unlimited")
                pattern = r"(?:0|[1-9][0-9]{0,5})" if data["mode"] == "set" else r"[+-]?(?:0|[1-9][0-9]{0,5})"
                if not re.fullmatch(pattern, text, re.ASCII) or abs(int(text)) > 100000:
                    raise DomainError("invalid_credit_value")
                data["value"] = int(text)
                await self._reason_prompt(message, data, panel_id=panel_id)
            elif kind == "credit_reason":
                reason = text.strip()
                if not 1 <= len(reason) <= 240:
                    raise DomainError("invalid_credit_reason")
                await self._credit_preview(message, data, reason)
        except DomainError as error:
            text = "Данные недоступны или недопустимы. Проверьте ввод; отмена — /cancel."
            if str(error) == "already_active":
                text = "Сейчас выполняется бесплатная генерация. Дождитесь результата и повторите изменение."
            await self._show(message, text, _keyboard(_home_row()), panel_id=panel_id)
        return True

    async def _reason_prompt(self, message, data, *, edit=False, panel_id=None):
        data = dict(data) | {"reason_key": uuid.uuid4().hex}
        self._session("credit_reason", data, message=message if edit else None)
        await self._show(message, "Напишите пометку (1–240 символов) или оставьте «Таков путь».\n"
                         "При начислении пользователь увидит эту пометку.",
                         _keyboard([("Таков путь", "admin:reason:" + data["reason_key"])], _home_row()),
                         edit=edit, panel_id=panel_id)

    async def _credit_preview(self, message, data, reason):
        if data["user_id"] == self.unlimited_user_id:
            raise DomainError("owner_unlimited")
        operation = self.store.admin_prepare_credit(self.admin_user_id, data["user_id"], data["mode"], data["value"], reason, trial=self.trial_access)
        self._session("credit_preview", {"operation": operation["id"], "user_id": data["user_id"]})
        preview = (
            f"<b>Подтверждение изменения</b>\nПользователь: <code>{data['user_id']}</code>\n"
            f"Доступно: {operation['before_available']} → {operation['after_available']}\n"
            f"Резерв: {operation['reserved']} → {operation['reserved']}\nПометка: {escape(reason)}\n\n"
        )
        if operation["after_available"] > operation["before_available"]:
            preview += "Пользователь получит уведомление с этой пометкой.\n"
        preview += "Подтверждение действует 5 минут. При изменении остатка потребуется новое."
        await self._show(message, preview, _keyboard([
            ("Подтвердить", f"admin:confirm:{operation['id']}"), ("Отменить", f"admin:cancel:{operation['id']}")
        ]), panel_id=data.get("_panel_id"))

    async def callback(self, callback):
        if not self.owner(callback.message, callback.from_user):
            await callback.answer(_DENIED, show_alert=True)
            return
        raw = callback.data or ""
        try:
            if len(raw) > 64:
                raise DomainError("invalid_callback")
            page_match = _PAGE_CALLBACK.fullmatch(raw)
            if page_match:
                self.store.admin_session_clear(self.admin_user_id)
                await self.home(callback.message, edit=True, page=int(page_match.group(1)))
            elif raw == "admin:home":
                self.store.admin_session_clear(self.admin_user_id)
                await self.home(callback.message, edit=True)
            elif raw == "admin:search":
                session = self.store.admin_session(self.admin_user_id)
                filter = (session or {}).get("data", {}).get("filter", "all")
                self._session("search", {"filter": filter}, message=callback.message)
                await self._show(callback.message, "Введите ID пользователя или username (можно с @).", _keyboard(_home_row()), edit=True)
            elif match := re.fullmatch(r"admin:filter:(all|visited|paying)", raw, re.ASCII):
                await self.users(callback.message, filter=match[1], edit=True)
            elif match := re.fullmatch(rf"admin:found:({_PAGE})", raw, re.ASCII):
                session = self.store.admin_session(self.admin_user_id)
                if not session or session["kind"] != "user_list":
                    raise DomainError("stale_search")
                data = session["data"]
                await self.users(callback.message, query=data["query"], page=int(match[1]), filter=data["filter"], edit=True)
            elif match := re.fullmatch(rf"admin:(user|audit):({_ID})", raw, re.ASCII):
                user_id = int(match[2])
                if user_id >= 2**52:
                    raise DomainError("invalid_user")
                self.store.admin_session_clear(self.admin_user_id)
                if match[1] == "user":
                    await self.card(callback.message, user_id, edit=True)
                else:
                    self.store.admin_user(user_id, trial=self.trial_access)
                    rows = self.store.admin_credit_history(user_id, limit=5)
                    lines = [f"<b>История изменений</b> · <code>{user_id}</code>"]
                    for row in rows:
                        lines.append(f"{_timestamp(row['created'])} · {_status(row['status'], _CREDIT_STATUS)}\n{row['before_available']} → {row['after_available']} · резерв {row['reserved']}\nИсполнитель: {row['actor']} · {escape(row['reason'])}\nОперация: <code>{row['id']}</code>")
                    await self._show(callback.message, "\n\n".join(lines) if rows else lines[0] + "\nИзменений нет.", _keyboard([("← Карточка", f"admin:user:{user_id}")]), edit=True)
            elif match := re.fullmatch(rf"admin:credit:({_ID}):(p5|p10|m1|delta|set)", raw, re.ASCII):
                user_id, action = int(match[1]), match[2]
                if user_id >= 2**52 or user_id == self.unlimited_user_id:
                    raise DomainError("invalid_user")
                self.store.admin_user(user_id, trial=self.trial_access)
                data = {"user_id": user_id, "mode": "set" if action == "set" else "add"}
                if action in {"set", "delta"}:
                    self._session("credit_value", data, message=callback.message)
                    prompt = "Введите доступный остаток: целое число 0–100000." if action == "set" else "Введите изменение: целое число от −100000 до +100000."
                    await self._show(callback.message, prompt, _keyboard(_home_row()), edit=True)
                else:
                    data["value"] = {"p5": 5, "p10": 10, "m1": -1}[action]
                    await self._reason_prompt(callback.message, data, edit=True)
            elif match := re.fullmatch(rf"admin:reason:({_UUID})", raw, re.ASCII):
                data = self._bound("credit_reason", "reason_key", match[1])
                await self._credit_preview(callback.message, data, _DEFAULT_REASON)
            elif match := re.fullmatch(rf"admin:(confirm|cancel):({_UUID})", raw, re.ASCII):
                data = self._bound("credit_preview", "operation", match[2])
                if data["user_id"] == self.unlimited_user_id:
                    raise DomainError("owner_unlimited")
                if match[1] == "confirm":
                    self.store.admin_confirm_credit(self.admin_user_id, match[2], trial=self.trial_access)
                else:
                    self.store.admin_cancel_credit(self.admin_user_id, match[2])
                self.store.admin_session_clear(self.admin_user_id)
                await self.card(callback.message, data["user_id"], edit=True)
            elif raw == "admin:settings":
                self.store.admin_session_clear(self.admin_user_id)
                await self.settings(callback.message, edit=True)
            elif match := re.fullmatch(r"admin:setting:(enabled|registration|questions|generation|payment):([01])", raw, re.ASCII):
                self.store.admin_session_clear(self.admin_user_id)
                self.store.admin_configure(match[1], match[2] == "1")
                await self.settings(callback.message, edit=True)
            elif match := re.fullmatch(rf"admin:events:({_PAGE})", raw, re.ASCII):
                self.store.admin_session_clear(self.admin_user_id)
                page = int(match[1])
                events = self.store.admin_events(page=page)
                lines, rows = ["<b>События</b>"], []
                for event in events:
                    payload = event["payload"]
                    label = _EVENT_NAMES.get(event["kind"], "Событие")
                    lines.append(f"{_timestamp(event.get('created'))} · {label} · {_status(event.get('status'), _DELIVERY)}\nПользователь: {escape(_short(payload.get('user_id'), 16))}")
                    if event.get("message_id"):
                        lines.append("Сообщение Telegram: " + escape(_short(event["message_id"], 32)))
                    if event["kind"] == "questions":
                        lines.append(escape(str(payload.get("text", ""))[:140]))
                    elif event["kind"] == "payment":
                        lines.append(f"{_money(payload.get('amount'))} · Начислено: {escape(_short(payload.get('credits'), 16))}")
                    elif event["kind"] == "generation":
                        lines.append(f"{_preset(payload.get('preset'))} · {_status(payload.get('status'), _JOB_STATUS)}")
                    elif event["kind"] == "credit_grant":
                        lines.append(f"Дополнительно: +{escape(_short(payload.get('credits'), 16))} ген.")
                    rows.append([("Подробно · " + label, "admin:event:" + event["id"])])
                    job = payload.get("job_id") or payload.get("job")
                    if isinstance(job, str) and re.fullmatch(_UUID, job, re.ASCII):
                        rows.append([("Задание " + job[:8], "admin:job:" + job)])
                nav = []
                if page:
                    nav.append(("←", f"admin:events:{page - 1}"))
                if len(events) == 10 and page < 999999999:
                    nav.append(("→", f"admin:events:{page + 1}"))
                if not events:
                    lines.append("Событий пока нет.")
                lines.append(f"Страница {page + 1}")
                # Keep Telegram's text limit even for ten long user questions.
                await self._show(callback.message, "\n\n".join(lines), _keyboard(*rows, nav, _home_row()), edit=True)
            elif match := re.fullmatch(rf"admin:event:({_UUID})", raw, re.ASCII):
                self.store.admin_session_clear(self.admin_user_id)
                await self.event(callback.message, match[1], edit=True)
            elif match := re.fullmatch(rf"admin:(job|result):({_UUID})", raw, re.ASCII):
                self.store.admin_session_clear(self.admin_user_id)
                await self.job(callback.message, match[2], result=match[1] == "result", edit=True)
            else:
                raise DomainError("invalid_callback")
        except (DomainError, ValueError, FileNotFoundError):
            await callback.answer("Страница недоступна.", show_alert=True)
            return
        await callback.answer()

    async def event(self, message, event_id, *, edit=False):
        event = self.store.admin_event_detail(event_id)
        payload, kind = event["payload"], event["kind"]
        lines = [
            _EVENT_NAMES.get(kind, "Событие"),
            f"Событие: {event_id}",
            f"Время: {_timestamp(event.get('created'))}",
            f"Доставка: {_status(event.get('status'), _DELIVERY)} · попыток: {_short(event.get('attempts'), 4)}",
            f"Сообщение в Telegram: {_short(event.get('message_id'), 32)}",
        ]
        if event.get("status") == "needs_review":
            lines.append("Telegram мог принять сообщение. Автоматического повтора нет; проверьте историю чата.")
        rows = []
        user_id = payload.get("user_id")
        if type(user_id) is int and 0 < user_id < 2**52:
            lines.append(f"Пользователь: {user_id}")
            rows.append([("Карточка пользователя", f"admin:user:{user_id}")])
        if kind == "questions":
            lines.extend([f"Username: {_short(payload.get('username'), 32)}", f"Исходное сообщение: {_short(payload.get('message_id'), 32)}", "", _short(payload.get("text"), 1000)])
        elif kind == "payment":
            lines.extend([
                f"Оплата: {_money(payload.get('amount'))}",
                f"Начислено генераций: {_short(payload.get('credits'), 16)}",
                f"Заказ: {_short(payload.get('order_id'), 128)}",
                f"Платёж: {_short(payload.get('charge_id'), 128)}",
            ])
        elif kind == "generation":
            lines.extend([
                f"Функция: {_preset(payload.get('preset'))}", f"Статус: {_status(payload.get('status'), _JOB_STATUS)}",
                f"Длительность: {_short(payload.get('duration'), 24)} с", f"Расход: {_short(payload.get('cost'), 16)}",
                f"Резерв: {_status(payload.get('reserve_action'), _RESERVE_ACTION)}", f"Категория ошибки: {_short(payload.get('error_category'))}",
            ])
            job = payload.get("job_id")
            if isinstance(job, str) and re.fullmatch(_UUID, job, re.ASCII):
                lines.append(f"Задание: {job}")
                rows.append([("Задание / готовый результат", "admin:job:" + job)])
        elif kind == "registration":
            lines.append(f"Username: {_short(payload.get('username'), 32)}")
            lines.append(f"Согласие: {_short(payload.get('consent'), 8)}")
        elif kind == "credit_grant":
            lines.extend([f"Дополнительно: +{_short(payload.get('credits'), 16)} ген.",
                          "Получатель уведомления: пользователь", "Пометка администратора:",
                          _short(payload.get("reason"), 240)])
        await self._show(message, "\n".join(lines), _keyboard(*rows, [("← События", "admin:events:0")], _home_row()), edit=edit, html=False)

    async def job(self, message, job_id, *, result=False, edit=False):
        detail = self.store.admin_job_detail(job_id)
        if not detail:
            raise DomainError("missing_job")
        shared = bool(detail.get("share_available")) and detail.get("expires", 0) > time.time()
        shared = shared and self.store.admin_share_enabled(detail["user_id"])
        if result:
            if not shared or not self.media or not detail.get("result"):
                raise DomainError("result_private_or_expired")
            path = self.media.path(detail["result"])
            owner_dir = self.media.root / str(detail["user_id"])
            if path.parent != owner_dir or path.stem != job_id or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"} or not path.is_file():
                raise DomainError("invalid_result_path")
            # Recheck the current opt-in at the last boundary before sending.
            if not self.store.admin_share_enabled(detail["user_id"]):
                raise DomainError("result_private")
            await message.answer_photo(FSInputFile(path), caption=f"Результат задания {job_id}. Хранение до 24 часов; отправленное сообщение остаётся в Telegram.")
            return
        text = (
            f"<b>Задание</b> <code>{job_id}</code>\nПользователь: <code>{detail['user_id']}</code>\n"
            f"Функция: {_preset(detail['preset'])}\nСтатус: {_status(detail['status'], _JOB_STATUS)}"
        )
        rows = [[("Карточка пользователя", f"admin:user:{detail['user_id']}")]]
        if shared:
            text += "\nОписание: " + escape(str(detail.get("description", ""))[:2000])
            if detail.get("result"):
                rows.append([("Посмотреть готовый результат", "admin:result:" + job_id)])
        else:
            text += "\nДоступны только метаданные. Передача выключена или срок 24 часа истёк."
        await self._show(message, text, _keyboard(*rows, [("События", "admin:events:0")], _home_row()), edit=edit)


def register_admin(router: Router, store: Store, admin_user_id: int, *, media=None, trial_access=False, unlimited_user_id=0):
    visits = PrivateVisitMiddleware(store)
    router.message.outer_middleware(visits)
    router.callback_query.outer_middleware(visits)
    controller = AdminController(store, admin_user_id, media=media, trial_access=trial_access, unlimited_user_id=unlimited_user_id)

    @router.message(Command("admin"))
    async def admin(message: Message):
        if not controller.owner(message, message.from_user):
            await message.answer(_DENIED)
            return
        store.admin_session_clear(admin_user_id)
        await controller.home(message)

    async def active_text(message):
        return bool(message.text and (not message.text.startswith("/") or message.text == "/cancel") and controller.active(message))

    @router.message(active_text)
    async def admin_text(message: Message):
        await controller.consume_text(message)

    @router.callback_query(F.data.startswith("admin:"))
    async def admin_page(callback: CallbackQuery):
        await controller.callback(callback)

    return controller
