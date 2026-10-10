from types import SimpleNamespace

import pytest
from test_bot_flow import build_ui, feed, texts

from image_studio.admin_delivery import AdminDelivery


def questions(store):
    return [row for row in store.admin_events() if row["kind"] == "questions"]


async def test_unknown_question_captured_once_outside_edit_and_navigation(tmp_path):
    ui = build_ui(tmp_path, admin_user_id=99)
    response = texts(await feed(ui, "Как предложить улучшение?", message_id=200))
    assert "передано владельцу" in response
    assert questions(ui[2])[0]["payload"]["text"] == "Как предложить улучшение?"
    await feed(ui, "Как предложить улучшение?", message_id=200)
    await feed(ui, "/help")
    await feed(ui, callback="nav:balance")
    assert len(questions(ui[2])) == 1


async def test_active_draft_and_admin_session_are_never_questions(tmp_path):
    ui = build_ui(tmp_path, admin_user_id=99)
    await feed(ui, callback="preset:hair")
    await feed(ui, "Фото ещё не загружено")
    await feed(ui, photo=True)
    await feed(ui, "Каре", message_id=300)
    assert len(ui[2].jobs(1)) == 1 and questions(ui[2]) == []
    ui[2].admin_session_set(99, "search", {})
    await feed(ui, "1", user=99)
    assert questions(ui[2]) == []


async def test_sharing_explicit_default_off_and_no_retrospective_capture(tmp_path):
    ui = build_ui(tmp_path, admin_user_id=99)
    response = texts(await feed(ui, "/sharing"))
    assert "выключена" in response and not ui[2].admin_share_enabled(1)
    await feed(ui, callback="sharing:on")
    assert ui[2].admin_share_enabled(1)
    await feed(ui, callback="preset:hair")
    await feed(ui, photo=True)
    await feed(ui, "Каре без чёлки", message_id=300)
    job = ui[2].jobs(1)[0]["id"]
    assert ui[2].admin_job_detail(job)["description"] == "Каре без чёлки"
    await feed(ui, callback="sharing:off")
    assert "description" not in ui[2].admin_job_detail(job)
    await feed(ui, callback="sharing:on")
    assert "description" not in ui[2].admin_job_detail(job)
    # Duplicate description does not share an old job after consent was revoked.
    await feed(ui, "Каре без чёлки", message_id=300)
    assert "description" not in ui[2].admin_job_detail(job)


async def test_delete_revokes_sharing_and_removes_description(tmp_path):
    ui = build_ui(tmp_path, admin_user_id=99)
    await feed(ui, callback="sharing:on")
    await feed(ui, callback="preset:hair")
    await feed(ui, photo=True)
    await feed(ui, "Каре", message_id=300)
    job = ui[2].jobs(1)[0]["id"]
    assert await ui[4].process(job)
    await feed(ui, "/delete")
    assert not ui[2].admin_share_enabled(1)
    assert "description" not in ui[2].admin_job_detail(job)
    with ui[2].tx() as db:
        assert db.execute("SELECT COUNT(*) FROM admin_job_shares").fetchone()[0] == 0


async def test_capture_storage_error_does_not_fail_user_generation(tmp_path, monkeypatch):
    ui = build_ui(tmp_path, admin_user_id=99)
    await feed(ui, callback="sharing:on")

    def broken(*args):
        raise OSError("private user payload")

    monkeypatch.setattr(ui[2], "admin_capture_job", broken)
    await feed(ui, callback="preset:hair")
    await feed(ui, photo=True)
    response = texts(await feed(ui, "Каре", message_id=300))
    assert "Создаём" in response and len(ui[2].jobs(1)) == 1
    job = ui[2].jobs(1)[0]["id"]
    assert await ui[4].process(job)
    assert ui[2].job(job)["status"] == "generated"
    assert ui[4].wallet(1) == (0, 0)


async def test_real_store_outbox_delivery_metadata_without_description(tmp_path):
    ui = build_ui(tmp_path, admin_user_id=99)
    ui[2].admin_set_sharing(1, True)
    await feed(ui, callback="preset:hair")
    await feed(ui, photo=True)
    await feed(ui, "Секретное описание", message_id=300)
    job = ui[2].jobs(1)[0]["id"]
    assert await ui[4].process(job)
    sent = []

    class Receiver:
        async def send_message(self, target, text, **kwargs):
            sent.append((target, text))
            return SimpleNamespace(message_id=len(sent))

    delivery = AdminDelivery(Receiver(), ui[2], 99)
    while await delivery.process_one():
        pass
    assert len(sent) == 3 and all(target == 99 for target, _ in sent)
    assert all("Секретное описание" not in text for _, text in sent)
    assert {row["status"] for row in ui[2].admin_events()} == {"sent"}


@pytest.mark.parametrize("text", ["Описание", "/неизвестная"])
async def test_notifications_disabled_owner_does_not_receive_questions(tmp_path, text):
    ui = build_ui(tmp_path)  # no configured owner: ordinary help still works
    await feed(ui, text)
    assert questions(ui[2]) == []
