import asyncio
import io
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User
from PIL import Image

from image_studio.bot import NAV, build_dispatcher
from image_studio.config import Settings
from image_studio.media import Media
from image_studio.provider import MockProvider
from image_studio.purchase_demo import (
    MAX_SESSIONS,
    PRIVACY_URL,
    TERMS_URL,
    TEST_URL,
    TTL_SECONDS,
    PurchaseDemo,
)
from image_studio.service import Service
from image_studio.store import Store


class OfflineBot(Bot):
    def __init__(self):
        super().__init__("123456789:ABCdefghijklmnopqrstuvwxyz123456789")
        self.sent = []
        self.last_message_id = 1000
        self.edit_error = None

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        if method.__api_method__ == "answerCallbackQuery":
            return True
        if method.__api_method__ == "editMessageText" and self.edit_error:
            raise TelegramBadRequest(method=method, message=self.edit_error)
        if method.__api_method__ == "sendMessage":
            self.last_message_id += 1
        return Message(
            message_id=(method.message_id if method.__api_method__ == "editMessageText"
                        else self.last_message_id),
            date=datetime.now(timezone.utc),
            chat=Chat(id=int(method.chat_id), type="private"),
            from_user=User(id=self.id, is_bot=True, first_name="Бот"),
            text=method.text,
            reply_markup=method.reply_markup,
        ).as_(self)

    async def download(self, file, destination=None, **kwargs):
        buf = io.BytesIO()
        Image.new("RGB", (256, 256), "#c8b9a0").save(buf, "JPEG")
        destination.write(buf.getvalue())
        return destination


class Harness:
    def __init__(self, tmp_path, *, trial=False):
        self.store, self.media, self.provider = Store(tmp_path / "db.sqlite3"), Media(tmp_path), MockProvider()
        self.store.consent(1)
        if not trial:
            self.store.grant_demo(1)
        self.service = Service(self.store, self.media, self.provider, trial_access=trial)
        self.service.wallet(1)
        self.bot = OfflineBot()
        self.now = 0.0
        self.demo = PurchaseDemo(clock=lambda: self.now)
        self.settings = Settings(image_provider="openai" if trial else "mock", trial_access=trial)
        self.dp = build_dispatcher(
            self.settings, self.store, self.media, self.service, purchase_demo=self.demo
        )
        self.sequence = 0

    def snapshot(self, baseline=None):
        # Normalize visits in a copy; the source database and all accounting stay intact.
        with closing(sqlite3.connect(self.store.path)) as source, closing(sqlite3.connect(":memory:")) as copy:
            source.backup(copy)
            copy.row_factory = sqlite3.Row
            accounting = [row["name"] for row in copy.execute("PRAGMA table_info(users)")
                          if row["name"] not in {"id", "username", "first_seen", "last_seen"}]
            if baseline is not None:
                for user in copy.execute("SELECT * FROM users").fetchall():
                    if (user["id"] not in baseline["user_ids"] and 0 < user["id"] < 2**52
                            and all(user[column] == 0 for column in accounting)):
                        copy.execute("DELETE FROM users WHERE id=?", (user["id"],))
            copy.execute("UPDATE users SET username=NULL,first_seen=NULL,last_seen=NULL")
            return {
                "user_ids": frozenset(row["id"] for row in copy.execute("SELECT id FROM users")),
                "dump": list(copy.iterdump()),
            }

    async def send(self, text=None, *, data=None, user=1, mid=None, chat=None, photo=False):
        self.sequence += 1
        actor = User(id=user, is_bot=False, first_name="Тест")
        message = Message(
            message_id=mid if mid is not None else self.sequence,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user if chat is None else chat, type="private"),
            from_user=actor if data is None else User(id=self.bot.id, is_bot=True, first_name="Бот"),
            text=text,
            photo=([PhotoSize(file_id="test", file_unique_id="test", width=256, height=256)]
                   if photo else None),
        )
        update = (
            Update(update_id=self.sequence, message=message)
            if data is None else Update(update_id=self.sequence, callback_query=CallbackQuery(
                id=str(self.sequence), from_user=actor, chat_instance="test", message=message, data=data,
            ))
        )
        self.bot.sent.clear()
        await self.dp.feed_update(self.bot, update)
        return self.bot.sent[:]

    async def click(self, data, **kwargs):
        return await self.send(data=data, mid=self.demo.sessions[1].message_id, **kwargs)


@pytest.fixture(params=[False, True], ids=["demo-wallet", "trial-wallet"])
def ui(tmp_path, request):
    return Harness(tmp_path, trial=request.param)


def content(sent):
    return next(method for method in reversed(sent) if method.__api_method__ in {
        "sendMessage", "editMessageText",
    })


def data(sent, suffix):
    return next(
        button.callback_data for row in content(sent).reply_markup.inline_keyboard for button in row
        if button.callback_data and button.callback_data.endswith(":" + suffix)
    )


def only_alert(sent):
    assert len(sent) == 1
    assert sent[0].__api_method__ == "answerCallbackQuery" and sent[0].show_alert is True


@pytest.mark.parametrize("plan,label,price,count", [
    ("express", "Express", 160, 5), ("base", "Базовый", 380, 12), ("premium", "Premium", 790, 30),
])
@pytest.mark.parametrize("method,label_method", [("sbp", "СБП"), ("crypto", "Крипта")])
async def test_single_message_flow_back_cancel_and_no_ledger(ui, plan, label, price, count, method, label_method):
    before = ui.snapshot()
    initial = await ui.send(NAV["buy"])
    session = ui.demo.sessions[1]
    assert session.message_id == ui.bot.last_message_id != ui.sequence
    assert len(initial) == 1 and initial[0].__api_method__ == "sendMessage"
    assert content(initial).text.startswith("Образ · Покупка доступа (демо)")
    assert {b.text for row in content(initial).reply_markup.inline_keyboard for b in row} == {
        "Express · 160 ₽ (5 генераций)", "Базовый · 380 ₽ (12 генераций)",
        "Premium · 790 ₽ (30 генераций)", "❌ Отменить покупку",
    }
    assert "Любая правка, объединение фото" in content(initial).text
    assert "Количество генераций и срок доступа пока не определены" not in content(initial).text
    plan_data = data(initial, "plan:" + plan)
    choice = await ui.click(plan_data)
    selection = content(choice)
    assert selection.__api_method__ == "editMessageText" and selection.message_id == session.message_id
    assert f"Тариф: {label}" in selection.text and f"Стоимость: {price} ₽" in selection.text
    assert f"Генераций: {count}" in selection.text
    method_rows = selection.reply_markup.model_dump(mode="json", exclude_none=True)["inline_keyboard"]
    assert [(button["text"], button["style"]) for button in method_rows[0]] == [
        ("СБП", "success"), ("Крипта", "primary"),
    ]
    duplicate = await ui.click(plan_data)
    assert len(duplicate) == 1 and duplicate[0].__api_method__ == "answerCallbackQuery"
    final = await ui.click(data(choice, "method:" + method))
    result = content(final)
    assert result.message_id == session.message_id
    assert f"Тариф: {label}" in result.text and f"Стоимость: {price} ₽" in result.text
    assert f"Генераций: {count}" in result.text and f"Способ: {label_method}" in result.text
    assert "DEMO" in result.text and "деньги не списываются" in result.text
    assert "Сервис: Platega · демо" in result.text
    assert "доступ и генерации не начисляются" in result.text
    assert "а не срок действия ссылки" in result.text and "Telegram Stars" in result.text
    assert "Оплачивая услугу после подключения платежей" in result.text
    urls = {b.url: b.text for row in result.reply_markup.inline_keyboard for b in row if b.url}
    assert urls == {TEST_URL: "🔗 Тестовая ссылка", PRIVACY_URL: "Политика конфиденциальности",
                    TERMS_URL: "Пользовательское соглашение"}
    for stage in (selection, result):
        rows = stage.reply_markup.model_dump(mode="json", exclude_none=True)["inline_keyboard"]
        assert rows[-2:] == [
            [{"text": "Политика конфиденциальности", "url": PRIVACY_URL}],
            [{"text": "Пользовательское соглашение", "url": TERMS_URL}],
        ]
        assert [row[0]["text"] for row in rows[-4:-2]] == ["⬅️ Назад", "❌ Отменить покупку"]
        assert rows[-4][0]["callback_data"].endswith(":back")
        assert rows[-3][0]["callback_data"].endswith(":cancel")
        assert all("style" not in button for row in rows[-4:] for button in row)
        assert sum(button.get("url") in {PRIVACY_URL, TERMS_URL} for row in rows for button in row) == 2
    for trigger in (NAV["buy"], "/buy"):
        repeated = await ui.send(trigger)
        assert len(repeated) == 1 and content(repeated).message_id == session.message_id
        assert content(repeated).__api_method__ == "editMessageText"
    repeated = await ui.send(data="nav:buy")
    assert content(repeated).__api_method__ == "editMessageText"
    assert content(repeated).message_id == session.message_id
    back = await ui.click(data(final, "back"))
    only_alert(await ui.click(data(final, "cancel")))  # The previous revision is stale.
    plans = await ui.click(data(back, "back"))
    cancelled = await ui.click(data(plans, "cancel"))
    assert "отменена" in content(cancelled).text and content(cancelled).reply_markup is None
    assert content(cancelled).text.startswith("Образ · Покупка доступа (демо)")
    only_alert(await ui.click(plan_data))
    assert ui.snapshot(before) == before and ui.provider.calls == 0
    assert ui.settings.trial_access == ui.service.trial_access


async def test_expiry_exact_boundary_and_repeated_open_does_not_extend_session(ui):
    before = ui.snapshot()
    first = await ui.send("/buy")
    session = ui.demo.sessions[1]
    initial_id = session.message_id
    ui.now = TTL_SECONDS - 1
    await ui.send(NAV["buy"])
    choice = await ui.click(data(first, "plan:base"))
    assert ui.demo.sessions[1].created == 0
    ui.now = TTL_SECONDS
    only_alert(await ui.send(data=data(choice, "method:sbp"), mid=initial_id))
    assert ui.demo.sessions == {} and ui.snapshot(before) == before
    await ui.send(NAV["buy"])
    assert ui.demo.sessions[1].message_id != initial_id
    assert ui.demo.sessions[1].created == TTL_SECONDS
    only_alert(await ui.send(data=data(first, "plan:express"), mid=initial_id))


@pytest.mark.parametrize("tamper", [
    "token", "foreign_user", "foreign_message", "foreign_chat", "plan", "method", "revision", "shape",
])
async def test_tampered_callbacks_only_alert_and_preserve_state(ui, tamper):
    before = ui.snapshot()
    first = await ui.send("/buy")
    session = ui.demo.sessions[1]
    payload, user, chat, mid = data(first, "plan:express"), 1, 1, session.message_id
    if tamper == "token":
        payload = payload.replace(session.token, "0" * len(session.token))
    elif tamper == "foreign_user":
        user = 2
    elif tamper == "foreign_message":
        mid += 1
    elif tamper == "foreign_chat":
        chat = 2
    elif tamper == "plan":
        payload = payload.replace("express", "invented")
    elif tamper == "method":
        payload = payload.replace("plan:express", "method:crypto")
    elif tamper == "revision":
        payload = payload.replace(":0:", ":9:")
    else:
        payload += ":extra"
    only_alert(await ui.send(data=payload, user=user, chat=chat, mid=mid))
    assert ui.demo.sessions[1] == session and ui.snapshot(before) == before and ui.provider.calls == 0


async def test_two_users_isolated_and_nav_buy_requires_own_private_chat(ui):
    first = await ui.send(NAV["buy"])
    first_session = ui.demo.sessions[1]
    second = await ui.send(NAV["buy"], user=2)
    second_session = ui.demo.sessions[2]
    assert first_session.token != second_session.token
    assert first_session.message_id != second_session.message_id
    only_alert(await ui.send(data=data(first, "plan:premium"), user=2, mid=second_session.message_id))
    only_alert(await ui.send(data="nav:buy", user=2, chat=1))
    selected = await ui.send(data=data(second, "plan:base"), user=2, mid=second_session.message_id)
    assert content(selected).chat_id == 2 and ui.demo.sessions[1] == first_session


@pytest.mark.parametrize("error,successful", [
    ("Bad Request: message is not modified: specified new message content and reply markup are exactly "
     "the same as a current content and reply markup of the message", True),
    ("Bad Request: message to edit not found", False),
    ("Bad Request: unrelated error mentioning message is not modified", False),
])
async def test_edit_failure_never_sends_fallback_and_exact_not_modified_is_idempotent(ui, error, successful):
    first = await ui.send("/buy")
    before = ui.demo.sessions[1]
    ui.bot.edit_error = error
    sent = await ui.click(data(first, "plan:base"))
    assert [m.__api_method__ for m in sent] == ["editMessageText", "answerCallbackQuery"]
    if successful:
        assert ui.demo.sessions[1].stage == "methods"
        assert not sent[-1].show_alert
        assert len(await ui.click(data(first, "plan:base"))) == 1
    else:
        assert ui.demo.sessions[1] == before and sent[-1].show_alert
        # A repeated lower button must not fall back to another checkout message either.
        repeated = await ui.send(NAV["buy"])
        assert [m.__api_method__ for m in repeated] == ["editMessageText"]


async def test_purchase_preserves_loaded_photo_active_job_and_step_tracking(ui):
    await ui.send(data="preset:hair")
    await ui.send(photo=True)
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), "#c8b9a0").save(buf, "JPEG")
    original = ui.media.save(1, buf.getvalue())
    job = ui.service.submit(1, "existing-job", "hair", [original], "Каре")
    before = ui.snapshot()
    scopes_before = list(ui.dp["step_messages"].scopes)
    files_before = {path: path.read_bytes() for path in ui.media.root.rglob("*") if path.is_file()}
    first = await ui.send(NAV["buy"])
    choice = await ui.click(data(first, "plan:express"))
    final = await ui.click(data(choice, "method:crypto"))
    await ui.click(data(final, "cancel"))
    assert ui.snapshot(before) == before and ui.store.job(job)["status"] == "queued"
    assert list(ui.dp["step_messages"].scopes) == scopes_before
    assert {path: path.read_bytes() for path in ui.media.root.rglob("*") if path.is_file()} == files_before
    after = await ui.send("Сохрани лицо, добавь каре")
    if ui.settings.trial_access:
        assert any("уже создаётся" in m.text for m in after if hasattr(m, "text"))
    else:
        assert any(b.callback_data.startswith("confirm:") for row in content(after).reply_markup.inline_keyboard
                   for b in row if b.callback_data)
    assert ui.provider.calls == 0 and len(ui.store.jobs(1)) == 1


async def test_support_direct_green_url_keeps_draft(ui):
    await ui.send(data="preset:hair")
    await ui.send(photo=True)
    for trigger in (NAV["support"], "/support"):
        support = content(await ui.send(trigger))
        assert len(support.reply_markup.inline_keyboard) == 1
        button = support.reply_markup.inline_keyboard[0][0]
        assert button.url == "https://t.me/nedelsky" and button.style == "success"
        assert button.callback_data is None
    after = await ui.send("Каре")
    assert "Шаг 3" in content(after).text or ui.settings.trial_access


def test_snapshot_excludes_only_visits_and_new_zero_accounting_users(ui):
    before = ui.snapshot()
    ui.store.record_visit(1, "changed_user")
    ui.store.record_visit(2, "new_visitor")
    assert ui.snapshot(before) == before
    with ui.store.tx() as connection:
        columns = [row["name"] for row in connection.execute("PRAGMA table_info(users)")
                   if row["name"] not in {"id", "username", "first_seen", "last_seen"}]
    for column in columns:
        mutation = "balance=1,reserved=1" if column == "reserved" else f"{column}=1"
        reset = "balance=0,reserved=0" if column == "reserved" else f"{column}=0"
        with ui.store.tx() as connection:
            connection.execute(f"UPDATE users SET {mutation} WHERE id=2")
        assert ui.snapshot(before) != before, column
        with ui.store.tx() as connection:
            connection.execute(f"UPDATE users SET {reset} WHERE id=2")
        assert ui.snapshot(before) == before
    # Normalizing a snapshot must not clear real visit identity or timestamps.
    with ui.store.tx() as connection:
        users = connection.execute("SELECT id,username,first_seen,last_seen FROM users ORDER BY id").fetchall()
        assert [row["username"] for row in users] == ["changed_user", "new_visitor"]
        assert all(row["first_seen"] is not None and row["last_seen"] is not None for row in users)


def test_snapshot_preserves_existing_zero_users_and_every_accounting_table(ui):
    ui.store.record_visit(2)
    before = ui.snapshot()
    with ui.store.tx() as connection:
        connection.execute("DELETE FROM users WHERE id=2")
    assert ui.snapshot(before) != before
    ui.store.record_visit(2)
    assert ui.snapshot(before) == before
    mutations = {
        "events": "INSERT INTO events(created,kind,ref) VALUES(1,'guard','offline')",
        "invoices": (
            "INSERT INTO invoices(id,user_id,pack,amount,credits,created,spent_baseline) "
            "VALUES('guard',1,'offline',100,1,1,0)"
        ),
        "payments": (
            "INSERT INTO payments(charge_id,order_id,user_id,amount,is_test) VALUES('guard','guard',1,100,1)"
        ),
        "jobs": (
            "INSERT INTO jobs(id,user_id,request_key,preset,cost,created) VALUES('guard',1,'guard','hair',1,1)"
        ),
    }
    for table, mutation in mutations.items():
        with ui.store.tx() as connection:
            connection.execute(mutation)
        assert ui.snapshot(before) != before, table
        with ui.store.tx() as connection:
            connection.execute(f"DELETE FROM {table} WHERE " + ("kind='guard'" if table == "events"
                                                               else "charge_id='guard'" if table == "payments"
                                                               else "id='guard'"))
        assert ui.snapshot(before) == before


async def test_concurrent_lower_presses_reuse_message_and_memory_cap():
    bot, demo = OfflineBot(), PurchaseDemo(clock=lambda: 0)

    def incoming(user):
        return Message(message_id=1, date=datetime.now(timezone.utc),
                       chat=Chat(id=user, type="private")).as_(bot)

    assert all(await asyncio.gather(demo.open(incoming(1), 1), demo.open(incoming(1), 1)))
    assert [m.__api_method__ for m in bot.sent] == ["sendMessage", "editMessageText"]
    for user in range(2, MAX_SESSIONS + 2):
        assert await demo.open(incoming(user), user)
    assert len(demo.sessions) == MAX_SESSIONS and 1 not in demo.sessions
    assert len({session.token for session in demo.sessions.values()}) == MAX_SESSIONS
    assert all(len(b.callback_data.encode()) <= 64 for m in bot.sent if m.reply_markup
               for row in m.reply_markup.inline_keyboard for b in row if b.callback_data)
