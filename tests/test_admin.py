import sqlite3
from datetime import datetime, timezone

import pytest
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Chat, InaccessibleMessage, Message, Update, User

from image_studio.admin import register_admin
from image_studio.store import DomainError, Store

OWNER = 1234


class OfflineBot(Bot):
    def __init__(self):
        super().__init__("123456789:ABCdefghijklmnopqrstuvwxyz123456789")
        self.sent = []
        self.sequence = 0
        self.unchanged = False

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        if method.__api_method__ == "answerCallbackQuery":
            return True
        if method.__api_method__ == "editMessageText" and self.unchanged:
            raise TelegramBadRequest(method=method, message="Bad Request: message is not modified")
        return Message(
            message_id=900, date=datetime.now(timezone.utc),
            chat=Chat(id=OWNER, type="private"),
        )


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "admin.sqlite3")


@pytest.fixture
def ui(store):
    router = Router()
    register_admin(router, store, OWNER)

    @router.message(F.text)
    async def broad_text(message):
        await message.answer("Обычный сценарий")

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher, OfflineBot(), store


async def send(
    ui, text=None, callback=None, *, user_id=OWNER, chat_id=None, chat_type="private",
    username="owner_test", is_bot=False, message_kind="message",
):
    dispatcher, bot, _ = ui
    bot.sequence += 1
    bot.sent.clear()
    user = User(id=user_id, is_bot=is_bot, first_name="Private name not stored", username=username)
    chat = Chat(id=chat_id if chat_id is not None else user_id, type=chat_type)
    message = Message(
        message_id=bot.sequence, date=datetime.now(timezone.utc), chat=chat, from_user=user, text=text,
    )
    if callback is None:
        update = Update(update_id=bot.sequence, message=message)
    else:
        if message_kind == "inaccessible":
            message = InaccessibleMessage(message_id=bot.sequence, chat=chat)
        elif message_kind == "inline":
            message = None
        update = Update(
            update_id=bot.sequence,
            callback_query=CallbackQuery(
                id=str(bot.sequence), from_user=user, chat_instance="offline", message=message,
                inline_message_id="offline-inline" if message is None else None, data=callback,
            ),
        )
    await dispatcher.feed_update(bot, update)
    return bot.sent


def text_methods(sent):
    return [m for m in sent if m.__api_method__ in {"sendMessage", "editMessageText"}]


def test_visit_updates_only_public_identity_and_preserves_first_timestamp(store, monkeypatch):
    monkeypatch.setattr("image_studio.store.time.time", lambda: 100.0)
    assert store.record_visit(OWNER, "owner_first")
    monkeypatch.setattr("image_studio.store.time.time", lambda: 200.0)
    store.record_visit(OWNER, "owner_second")
    with store.tx() as connection:
        user = dict(connection.execute("SELECT * FROM users WHERE id=?", (OWNER,)).fetchone())
    assert (user["username"], user["first_seen"], user["last_seen"]) == ("owner_second", 100.0, 200.0)
    store.record_visit(OWNER)
    assert store.admin_stats()["users"][0]["username"] is None
    assert "first_name" not in user and "Private name not stored" not in str(user)


@pytest.mark.parametrize("user_id", [0, -1, True, "1234", 1.0, None, 2**52])
def test_invalid_visits_do_not_create_users(store, user_id):
    assert not store.record_visit(user_id, "valid_user")
    assert store.admin_stats()["total_users"] == 0


@pytest.mark.parametrize("username", ['<a href="x">', "../../admin", "@valid_user", "имя_польз", "a" * 33])
def test_untrusted_handles_are_not_persisted(store, username):
    store.record_visit(OWNER, username)
    assert store.admin_stats()["users"][0]["username"] is None


def test_legacy_migration_preserves_all_accounting_and_reopens(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0,
                reserved INTEGER NOT NULL DEFAULT 0, consent INTEGER NOT NULL DEFAULT 0,
                demo_used INTEGER NOT NULL DEFAULT 0, spent INTEGER NOT NULL DEFAULT 0,
                refund_lock INTEGER NOT NULL DEFAULT 0, trial_granted INTEGER NOT NULL DEFAULT 0,
                trial_used INTEGER NOT NULL DEFAULT 0, trial_reserved INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE payments (
                charge_id TEXT PRIMARY KEY, order_id TEXT UNIQUE NOT NULL,
                user_id INTEGER NOT NULL, amount INTEGER NOT NULL);
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, request_key TEXT NOT NULL,
                preset TEXT NOT NULL, cost INTEGER NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
                result TEXT, usage TEXT, request_id TEXT, error TEXT, trial INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id,request_key));
            CREATE TABLE events (
                id INTEGER PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL);
            INSERT INTO users VALUES(1234,7,2,1,1,6,0,1,1,1);
            INSERT INTO payments VALUES('old-test','old-order',1234,100);
            INSERT INTO jobs VALUES('old-success',1234,'s','merge',2,'delivered',1,NULL,'{}',NULL,NULL,0);
            INSERT INTO jobs VALUES('old-running',1234,'r','merge',2,'running',2,NULL,NULL,NULL,NULL,0);
            INSERT INTO events VALUES(1,1,'payment','old-order');
        """)
        old_columns = {
            table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            for table in ("users", "payments", "jobs", "events")
        }
        old_rows = {
            table: connection.execute(f"SELECT * FROM {table}").fetchall() for table in old_columns
        }
    migrated = Store(path)
    assert migrated.wallet(OWNER) == (7, 2) and migrated.wallet(OWNER, trial=True) == (2, 1)
    assert migrated.admin_stats()["paying_users"] == 0
    assert migrated.admin_stats()["generated_count"] == 1
    for reopened in (migrated, Store(path)):
        with reopened.tx() as connection:
            for table, columns in old_columns.items():
                rows = connection.execute(f"SELECT {','.join(columns)} FROM {table}").fetchall()
                assert [tuple(row) for row in rows] == old_rows[table]
            user = connection.execute("SELECT username,first_seen,last_seen FROM users").fetchone()
            assert tuple(user) == (None, None, None)
            assert connection.execute("SELECT is_test FROM payments").fetchone()[0] == 1


def complete(store, user, key, *, cost=1, trial=False):
    job = store.reserve(user, key, "merge", cost, trial=trial)
    assert store.claim(job)
    store.finish(job, "offline.jpg", {}, None)
    return job


def pay(store, user, charge, *, is_test=True):
    order = store.invoice(user, "offline", 100, 5)
    assert store.payment(user, order, "RUB", 100, charge, is_test=is_test)
    return order


def test_separate_real_purchase_and_generation_totals_survive_replay_delete_and_refund(store):
    store.consent(OWNER)
    store.grant_demo(OWNER, 3)
    store.grant_trial(OWNER)
    first_real = pay(store, OWNER, "real-one", is_test=False)
    pay(store, OWNER, "real-two", is_test=False)
    test_order = pay(store, OWNER, "sandbox")
    assert not store.payment(OWNER, test_order, "RUB", 100, "sandbox", is_test=False)
    assert not store.payment(OWNER, first_real, "RUB", 100, "real-one", is_test=True)
    store.begin_refund(first_real)
    store.finish_refund(first_real)
    paid = complete(store, OWNER, "paid-job", cost=2)
    trial = complete(store, OWNER, "trial-job", trial=True)
    store.finish(paid, "replacement.jpg", {}, None)
    store.delivered(paid)
    store.delivered(paid)
    failed = store.reserve(OWNER, "failure", "hair", 1, trial=True)
    store.fail(failed, "offline-error")
    store.consent(42)
    store.grant_demo(42)
    pay(store, 42, "other-test")
    store.invoice(42, "unpaid", 100, 5)
    store.wallet(0)
    store.revoke_consent(OWNER)
    with store.tx() as connection:
        connection.execute("UPDATE jobs SET result=NULL WHERE id IN (?,?)", (paid, trial))
        assert connection.execute("SELECT is_test FROM payments WHERE charge_id='sandbox'").fetchone()[0] == 1
        assert connection.execute("SELECT is_test FROM payments WHERE charge_id='real-one'").fetchone()[0] == 0
    stats = Store(store.path).admin_stats()
    assert (stats["total_users"], stats["paying_users"], stats["generated_count"]) == (2, 1, 2)
    assert stats["users"] == [
        {"id": 42, "username": None, "generated_count": 0, "purchase_count": 0},
        {"id": OWNER, "username": None, "generated_count": 2, "purchase_count": 2},
    ]


def test_pagination_is_stable_and_bounded(store):
    for user in reversed(range(1, 24)):
        store.record_visit(user, "known_user")
    first, second, last = (store.admin_stats(page=page) for page in (0, 1, 999999999))
    assert first["pages"] == 3 and [u["id"] for u in first["users"]] == list(range(1, 11))
    assert [u["id"] for u in second["users"]] == list(range(11, 21))
    assert last["page"] == 2 and [u["id"] for u in last["users"]] == [21, 22, 23]
    for arguments in ({"page": -1}, {"page": True}, {"page_size": 0}, {"page_size": 101}):
        with pytest.raises(DomainError, match="invalid_admin_page"):
            store.admin_stats(**arguments)


@pytest.mark.parametrize("callback", [None, "admin:page:0"])
@pytest.mark.parametrize("options", [
    {"user_id": 4321},
    {"chat_type": "group", "chat_id": -50},
    {"chat_id": 4321},
    {"user_id": 4321, "chat_id": OWNER},
    {"is_bot": True},
])
async def test_admin_denial_has_no_stats_and_checks_chat_as_well_as_sender(ui, monkeypatch, callback, options):
    ui[2].record_visit(77, "private_user")

    def forbidden_stats(*args, **kwargs):
        pytest.fail("Unauthorized request queried statistics")

    monkeypatch.setattr(ui[2], "admin_stats", forbidden_stats)
    sent = await send(ui, "/admin", callback, **options)
    assert not any(m.__api_method__ == "editMessageText" for m in sent)
    assert any(getattr(m, "text", "") == "Доступ запрещён." for m in sent)
    assert "private_user" not in str(sent) and "Статистика бота" not in str(sent)


@pytest.mark.parametrize("message_kind", ["inaccessible", "inline"])
async def test_inaccessible_and_inline_callbacks_neither_record_visits_nor_read_stats(ui, monkeypatch, message_kind):
    monkeypatch.setattr(ui[2], "admin_stats", lambda **kwargs: pytest.fail("Inaccessible request read stats"))
    await send(ui, callback="admin:page:0", message_kind=message_kind)
    with ui[2].tx() as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


async def test_disabled_admin_never_reads_statistics(store, monkeypatch):
    router = Router()
    register_admin(router, store, 0)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    monkeypatch.setattr(store, "admin_stats", lambda **kwargs: pytest.fail("Disabled admin read stats"))
    sent = await send((dispatcher, OfflineBot(), store), "/admin")
    assert text_methods(sent)[0].text == "Доступ запрещён."


async def test_private_visits_are_recorded_for_normal_messages_and_callbacks_only(ui):
    await send(ui, "Обычный текст", user_id=41, username="first_user")
    await send(ui, callback="unhandled", user_id=41, username="second_user")
    await send(ui, "Нет username", user_id=42, username=None)
    await send(ui, "group", user_id=43, chat_id=-50, chat_type="group")
    await send(ui, "bot", user_id=44, is_bot=True)
    await send(ui, "mismatch", user_id=45, chat_id=46)
    assert [(u["id"], u["username"]) for u in ui[2].admin_stats()["users"]] == [
        (41, "second_user"), (42, None),
    ]


async def test_owner_panel_links_and_page_refresh_edit_one_message_before_broad_text(ui):
    for user in range(1, 13):
        ui[2].record_visit(user, "linked_user" if user == 1 else None)
    with ui[2].tx() as connection:
        connection.execute("UPDATE users SET username=? WHERE id=2", ('evil\"><b>name</b>',))
    sent = await send(ui, "/admin")
    messages = text_methods(sent)
    assert len(messages) == 1 and messages[0].__api_method__ == "sendMessage"
    assert messages[0].parse_mode == "HTML" and "Обычный сценарий" not in messages[0].text
    assert '<a href="https://t.me/linked_user">@linked_user</a>' in messages[0].text
    assert '<a href="tg://user?id=2">2</a>' in messages[0].text
    assert "evil" not in messages[0].text and "Страница 1/2" in messages[0].text
    sent = await send(ui, callback="admin:page:1")
    assert len(text_methods(sent)) == 1 and text_methods(sent)[0].__api_method__ == "editMessageText"
    assert "Страница 2/2" in text_methods(sent)[0].text
    assert "tg://user?id=11" in text_methods(sent)[0].text
    ui[1].unchanged = True
    sent = await send(ui, callback="admin:refresh:1")
    assert any(m.__api_method__ == "answerCallbackQuery" for m in sent)


@pytest.mark.parametrize("callback", [
    "admin:page:-1", "admin:page:01", "admin:page:1000000000", "admin:page:٠", "admin:wrong:0",
])
async def test_malformed_owner_callbacks_are_bounded_before_stats(ui, monkeypatch, callback):
    monkeypatch.setattr(ui[2], "admin_stats", lambda **kwargs: pytest.fail("Malformed callback read stats"))
    sent = await send(ui, callback=callback)
    assert not text_methods(sent)
    assert sent[0].text == "Страница недоступна."


async def test_missing_page_callback_returns_no_panel(ui):
    sent = await send(ui, callback="admin:page:999999999")
    assert not text_methods(sent) and sent[0].text == "Страница недоступна."


async def test_short_verified_public_handle_is_preserved_and_linked(ui):
    await send(ui, "visit", username="four")
    assert ui[2].admin_stats()["users"][0]["username"] == "four"
    methods = await send(ui, "/admin", username="four")
    assert any("https://t.me/four" in (getattr(method, "text", "") or "") for method in methods)
