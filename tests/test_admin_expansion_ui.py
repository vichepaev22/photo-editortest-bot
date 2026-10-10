import re
from html import unescape

import pytest
from aiogram import Dispatcher, F, Router
from test_admin import OWNER, OfflineBot, send, text_methods

from image_studio.admin import register_admin
from image_studio.media import Media
from image_studio.store import Store


@pytest.fixture
def expanded(tmp_path):
    store = Store(tmp_path / "expanded.sqlite3")
    store.record_visit(42, "target_user")
    store.consent(42)
    store.grant_demo(42, 5)
    router = Router()
    media = Media(tmp_path)
    controller = register_admin(router, store, OWNER, media=media)

    @router.message(F.text)
    async def general(message):
        await message.answer("Обычный сценарий")

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return (dispatcher, OfflineBot(), store), controller, media


def buttons(method):
    return [b.callback_data for row in method.reply_markup.inline_keyboard for b in row]


async def click(ui, prefix):
    method = text_methods(ui[1].sent)[-1]
    callback = next(value for value in buttons(method) if value.startswith(prefix))
    return await send(ui, callback=callback)


async def test_controller_and_card_keep_legacy_entry(expanded):
    ui, controller, _ = expanded
    assert controller is not None
    sent = await send(ui, "/admin")
    assert "Статистика бота" in text_methods(sent)[0].text
    sent = await send(ui, callback="admin:user:42")
    assert "Доступно: 5" in text_methods(sent)[0].text
    assert "В обработке: 0" in text_methods(sent)[0].text


@pytest.mark.parametrize("callback", ["admin:user:42", "admin:credit:42:p5", "admin:reason:" + "a" * 32, "admin:settings", "admin:events:0", "admin:job:" + "a" * 32, "admin:event:" + "a" * 32])
@pytest.mark.parametrize("options", [{"user_id": 99}, {"chat_type": "group", "chat_id": -1}, {"chat_id": 99}])
async def test_every_new_route_is_owner_private(expanded, callback, options):
    ui, _, _ = expanded
    before = ui[2].wallet(42)
    sent = await send(ui, callback=callback, **options)
    assert any(getattr(m, "text", "") == "Доступ запрещён." for m in sent)
    assert not text_methods(sent)
    assert ui[2].wallet(42) == before


@pytest.mark.parametrize("callback", ["admin:user:042", "admin:user:4503599627370496", "admin:credit:42:p100000", "admin:events:01", "admin:confirm:BAD", "admin:event:INVALID"])
async def test_forged_format_does_not_change_state(expanded, callback):
    ui, _, _ = expanded
    sent = await send(ui, callback=callback)
    assert not text_methods(sent)
    assert ui[2].wallet(42) == (5, 0)


async def test_credit_reason_preview_confirmation_replay_and_escaping(expanded):
    ui, _, _ = expanded
    await send(ui, callback="admin:credit:42:p5")
    assert ui[2].wallet(42) == (5, 0)
    sent = await send(ui, "Исправление <b>ошибки</b>")
    preview = text_methods(sent)[0]
    assert "5 → 10" in preview.text
    assert "&lt;b&gt;ошибки&lt;/b&gt;" in preview.text
    assert "Обычный сценарий" not in str(sent)
    confirmation = next(b for b in buttons(preview) if b.startswith("admin:confirm:"))
    assert ui[2].wallet(42) == (5, 0)
    await send(ui, callback=confirmation)
    assert ui[2].wallet(42) == (10, 0)
    await send(ui, callback=confirmation)
    assert ui[2].wallet(42) == (10, 0)
    sent = await send(ui, callback="admin:audit:42")
    assert "Исправление &lt;b&gt;ошибки&lt;/b&gt;" in text_methods(sent)[0].text


async def test_work_buttons_have_native_styles_but_user_cards_are_neutral(expanded):
    ui, _, _ = expanded
    sent = await send(ui, "/admin")
    markup = text_methods(sent)[0].reply_markup
    styles = {b.callback_data: b.style for row in markup.inline_keyboard for b in row}
    assert styles["admin:filter:all"] == styles["admin:search"] == "primary"
    assert styles["admin:filter:visited"] == styles["admin:filter:paying"] == "success"
    assert all(style is None for data, style in styles.items() if data.startswith("admin:user:"))
    sent = await send(ui, callback="admin:user:42")
    styles = {b.callback_data: b.style for row in text_methods(sent)[0].reply_markup.inline_keyboard for b in row}
    assert styles["admin:credit:42:p5"] == "success"
    assert styles["admin:credit:42:m1"] == "danger"


async def test_default_note_is_bound_to_current_wizard_and_requires_confirmation(expanded):
    ui, controller, _ = expanded
    await send(ui, callback="admin:credit:42:p5")
    old_default = next(b for b in buttons(text_methods(ui[1].sent)[0]) if b.startswith("admin:reason:"))
    await send(ui, callback="admin:credit:42:p10")
    default = next(b for b in buttons(text_methods(ui[1].sent)[0]) if b.startswith("admin:reason:"))
    assert old_default != default
    await send(ui, callback=old_default)
    assert ui[2].admin_session(OWNER)["kind"] == "credit_reason"
    # A process restart keeps the bound default and the original panel.
    controller.store = Store(ui[2].path)
    sent = await send(ui, callback=default)
    preview = text_methods(sent)[0]
    assert "Пометка: Таков путь" in preview.text and "5 → 15" in preview.text
    assert ui[2].wallet(42) == (5, 0)
    confirmation = next(b for b in buttons(preview) if b.startswith("admin:confirm:"))
    await send(ui, callback=default)
    assert ui[2].wallet(42) == (5, 0)
    await send(ui, callback=confirmation)
    assert ui[2].wallet(42) == (15, 0)
    notices = [row for row in ui[2].admin_events() if row["kind"] == "credit_grant"]
    assert len(notices) == 1 and notices[0]["payload"]["reason"] == "Таков путь"


async def test_set_balance_requires_bounded_integer_and_reason(expanded):
    ui, _, _ = expanded
    await send(ui, callback="admin:credit:42:set")
    for value in ("-1", "100001", "1.0", "٠", "01"):
        await send(ui, value)
        assert ui[2].wallet(42) == (5, 0)
        assert ui[2].admin_session(OWNER)["kind"] == "credit_value"
    await send(ui, "7")
    await send(ui, "перерасчёт")
    await click(ui, "admin:confirm:")
    assert ui[2].wallet(42) == (7, 0)


async def test_restart_consumes_search_and_signed_delta(expanded):
    ui, _, _ = expanded
    await send(ui, callback="admin:search")
    # A newly constructed controller reads the durable session.
    new_router = Router()
    register_admin(new_router, ui[2], OWNER)
    dispatcher = Dispatcher()
    dispatcher.include_router(new_router)
    restarted = dispatcher, ui[1], ui[2]
    sent = await send(restarted, "@target_user")
    assert "target_user" in text_methods(sent)[0].text
    await send(restarted, callback="admin:credit:42:delta")
    await send(restarted, "-2")
    await send(restarted, "ошибка начисления")
    await click(restarted, "admin:confirm:")
    assert ui[2].wallet(42) == (3, 0)


async def test_cancel_and_changed_snapshot_prevent_credit(expanded):
    ui, _, _ = expanded
    await send(ui, callback="admin:credit:42:p5")
    await send(ui, "подарок")
    preview = text_methods(ui[1].sent)[-1]
    confirm = next(b for b in buttons(preview) if b.startswith("admin:confirm:"))
    cancel = next(b for b in buttons(preview) if b.startswith("admin:cancel:"))
    await send(ui, callback=cancel)
    await send(ui, callback=confirm)
    assert ui[2].wallet(42) == (5, 0)
    await send(ui, callback="admin:credit:42:p5")
    await send(ui, "подарок")
    confirm = next(b for b in buttons(text_methods(ui[1].sent)[-1]) if b.startswith("admin:confirm:"))
    order = ui[2].invoice(42, "test", 100, 1)
    ui[2].payment(42, order, "RUB", 100, "changed-snapshot", is_test=True)
    await send(ui, callback=confirm)
    assert ui[2].wallet(42) == (6, 0)


async def test_result_access_default_consent_and_ownership_fail_closed(expanded, monkeypatch):
    ui, _, media = expanded
    job = "b" * 32
    media.save(42, b"result", job + ".jpg")
    detail = {"id": job, "user_id": 42, "status": "done", "preset": "hair", "share_available": False}
    monkeypatch.setattr(ui[2], "admin_job_detail", lambda _: detail)
    sent = await send(ui, callback="admin:result:" + job)
    assert not any(m.__api_method__ == "sendPhoto" for m in sent)
    detail.update(share_available=True, result="99/" + job + ".jpg", expires=9999999999)
    monkeypatch.setattr(ui[2], "admin_share_enabled", lambda _: True)
    sent = await send(ui, callback="admin:result:" + job)
    assert not any(m.__api_method__ == "sendPhoto" for m in sent)


async def test_result_only_current_opt_in_unexpired_and_generated_file(expanded):
    ui, _, media = expanded
    store = ui[2]
    store.admin_set_sharing(42, True)
    job = store.reserve(42, "admin-result", "hair", 1)
    store.admin_capture_job(job, 42, "Улучшить <b>свет</b>")
    assert store.claim(job)
    path = media.save(42, b"offline-result", job + ".jpg")
    store.finish(job, path, {}, None)
    sent = await send(ui, callback="admin:job:" + job)
    assert "Улучшить &lt;b&gt;свет&lt;/b&gt;" in text_methods(sent)[0].text
    sent = await send(ui, callback="admin:result:" + job)
    photos = [m for m in sent if m.__api_method__ == "sendPhoto"]
    assert len(photos) == 1
    assert photos[0].photo.path == media.path(path)
    store.admin_set_sharing(42, False)
    sent = await send(ui, callback="admin:job:" + job)
    assert "Улучшить" not in text_methods(sent)[0].text
    sent = await send(ui, callback="admin:result:" + job)
    assert not any(m.__api_method__ == "sendPhoto" for m in sent)
    store.admin_set_sharing(42, True)
    store.admin_capture_job(job, 42, "Повторное согласие")
    with store.tx() as connection:
        connection.execute("UPDATE admin_job_shares SET expires=0 WHERE job_id=?", (job,))
    sent = await send(ui, callback="admin:result:" + job)
    assert not any(m.__api_method__ == "sendPhoto" for m in sent)


async def test_credit_confirmation_expiry_and_wrong_actor_are_noop(expanded):
    ui, _, _ = expanded
    await send(ui, callback="admin:credit:42:p5")
    await send(ui, "подарок")
    confirmation = next(b for b in buttons(text_methods(ui[1].sent)[-1]) if b.startswith("admin:confirm:"))
    await send(ui, callback=confirmation, user_id=99)
    assert ui[2].wallet(42) == (5, 0)
    with ui[2].tx() as connection:
        connection.execute("UPDATE admin_credit_operations SET expires=0")
    await send(ui, callback=confirmation)
    assert ui[2].wallet(42) == (5, 0)


async def test_notifications_mute_categories_persist(expanded):
    ui, _, _ = expanded
    await send(ui, callback="admin:setting:enabled:0")
    assert ui[2].admin_claim_event() is None
    await send(ui, callback="admin:setting:questions:0")
    reopened = Store(ui[2].path)
    assert not reopened.admin_settings()["enabled"]
    assert not reopened.admin_settings()["questions"]
    await send(ui, callback="admin:setting:enabled:1")
    sent = await send(ui, callback="admin:settings")
    assert "Все уведомления: включены" in text_methods(sent)[0].text


async def test_event_receipts_payment_metadata_and_full_muted_question(expanded):
    ui, _, _ = expanded
    store = ui[2]
    question = "Вопрос <b>" + "&" * 900 + "</b>"
    assert store.admin_capture_question(42, question, 55, 42)
    question_event = store.admin_events()[0]
    await send(ui, callback="admin:setting:enabled:0")
    sent = await send(ui, callback="admin:event:" + question_event["id"])
    assert question in text_methods(sent)[0].text
    assert text_methods(sent)[0].parse_mode is None
    order = store.invoice(42, "real-test", 12345, 10)
    assert store.payment(42, order, "RUB", 12345, "receipt", is_test=False)
    row = store.admin_events()[0]
    with store.tx() as connection:
        connection.execute("UPDATE admin_outbox SET status='sent',message_id=777 WHERE id=?", (row["id"],))
    sent = await send(ui, callback="admin:events:0")
    text = text_methods(sent)[0].text
    assert "777" in text
    assert "123,45 ₽" in text and "Начислено: 10" in text
    sent = await send(ui, callback="admin:event:" + row["id"])
    assert "Начислено генераций: 10" in text_methods(sent)[0].text
    assert order in text_methods(sent)[0].text
    assert order in text_methods(sent)[0].text
    assert "Сообщение в Telegram: 777" in text_methods(sent)[0].text
    sent = await send(ui, callback="admin:event:" + question_event["id"], user_id=99)
    assert not text_methods(sent)


async def test_worst_case_event_list_and_question_detail_fit_telegram(expanded):
    ui, _, _ = expanded
    for index in range(10):
        ui[2].admin_event(f"large-question:{index}", "questions", {"user_id": 42, "text": "<&>" * 333, "status": "s" * 60})
    sent = await send(ui, callback="admin:events:0")
    markup = text_methods(sent)[0]
    decoded = unescape(re.sub(r"<[^>]+>", "", markup.text))
    assert len(decoded) <= 4096
    assert "&lt;" in markup.text
    event = next(row for row in ui[2].admin_events() if row["kind"] == "questions")
    sent = await send(ui, callback="admin:event:" + event["id"])
    detail = text_methods(sent)[0]
    assert "<&>" * 333 in detail.text and len(detail.text) <= 4096
    assert detail.parse_mode is None
