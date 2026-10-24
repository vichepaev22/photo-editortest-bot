import io

import pytest
from PIL import Image
from test_admin import send as send_admin
from test_bot_ui import OfflineBot, send

from image_studio.bot import NAV, build_dispatcher
from image_studio.config import Settings
from image_studio.media import Media
from image_studio.provider import MockProvider
from image_studio.service import Service
from image_studio.store import DomainError, Store

OWNER = 3975497


@pytest.fixture
def ui(tmp_path):
    store, media, provider = Store(tmp_path / "db.sqlite3"), Media(tmp_path), MockProvider()
    store.consent(OWNER)
    service = Service(store, media, provider, trial_access=True, unlimited_user_id=OWNER)
    settings = Settings(admin_user_id=OWNER, owner_unlimited_testing=True,
                        trial_access=True, bonus_shop_enabled=True)
    dp, bot = build_dispatcher(settings, store, media, service), OfflineBot()

    async def deliver(user, job, path):
        store.delivered(job)
        await dp["bonus_shop"].after_job(bot, user, job)

    service.deliver = deliver
    return dp, bot, store, media, service, provider


def callbacks(sent):
    return [button.callback_data for method in sent
            for row in getattr(getattr(method, "reply_markup", None), "inline_keyboard", [])
            for button in row if button.callback_data]


def texts(sent):
    return "\n".join(getattr(method, "text", "") or "" for method in sent)


async def test_balance_entry_only_for_owner_and_disabled_gate(ui):
    sent = await send(ui, NAV["balance"], user_id=OWNER)
    assert "bonus:home" in callbacks(sent) and "Безлимит" in texts(sent)
    assert ui[5].calls == 0
    sent = await send(ui, callback="bonus:home", user_id=1)
    assert "только владельцу" in texts(sent)
    ui[0]["bonus_shop"].enabled = False
    sent = await send(ui, NAV["balance"], user_id=OWNER)
    assert "bonus:home" not in callbacks(sent)
    sent = await send(ui, callback="bonus:home", user_id=OWNER)
    assert "только владельцу" in texts(sent)


async def test_bonus_callback_rejects_wrong_chat_and_inaccessible_message(ui):
    short = ui[:3]
    await send_admin(short, callback="bonus:home", user_id=OWNER, chat_id=123)
    assert "только владельцу" in texts(ui[1].sent)
    await send_admin(short, callback="bonus:home", user_id=OWNER, message_kind="inaccessible")
    assert "только владельцу" in texts(ui[1].sent)
    assert ui[2].wallet(OWNER) == (0, 0)


async def test_photo_mission_real_mock_worker_delivery_counts_once(ui):
    dp, bot, store, media, service, provider = ui
    await send(ui, callback="bonus:tasks", user_id=OWNER)
    mission = store.bonus_snapshot(OWNER)["missions"][0]
    step = mission["steps"][0]
    sent = await send(ui, callback="bonus:step:" + step["id"], user_id=OWNER)
    assert "Загрузите своё фото" in texts(sent)
    assert not store.jobs()
    sent = await send(ui, photo=True, user_id=OWNER)
    create = next(data for data in callbacks(sent) if data.startswith("bonus:submit:"))
    assert "API-запрос" in texts(sent)
    await send(ui, callback=create, user_id=OWNER)
    job = store.jobs(OWNER)[0]
    assert job["quota_exempt"] and provider.calls == 0
    await send(ui, callback=create, user_id=OWNER)
    assert len(store.jobs(OWNER)) == 1
    assert await service.process(job["id"])
    assert provider.calls == 1 and store.bonus_snapshot(OWNER)["points"] == 50
    store.delivered(job["id"])
    assert store.bonus_snapshot(OWNER)["points"] == 50
    assert "засчитана" in texts(bot.sent)
    assert service.is_unlimited(OWNER) and store.wallet(OWNER) == (0, 0)


async def test_existing_photo_reuse_and_navigation_leaves_game_input(ui):
    _, _, store, media, service, _ = ui
    photo = io.BytesIO()
    Image.new("RGB", (256, 256), "white").save(photo, "JPEG")
    path = media.save(OWNER, photo.getvalue())
    service.remember_inputs(OWNER, [path])
    step = store.bonus_snapshot(OWNER)["missions"][0]["steps"][0]
    sent = await send(ui, callback="bonus:step:" + step["id"], user_id=OWNER)
    assert "bonus:reuse:" + step["id"] in callbacks(sent)
    sent = await send(ui, callback="bonus:reuse:" + step["id"], user_id=OWNER)
    assert any(data.startswith("bonus:submit:") for data in callbacks(sent))
    assert store.bonus_session(OWNER)["input_path"] == path
    await send(ui, NAV["balance"], user_id=OWNER)
    assert store.bonus_session(OWNER) is None
    assert not store.jobs()


async def test_text_claim_cannot_complete_task_and_server_prompt_is_fixed(ui):
    _, _, store, media, service, _ = ui
    step = store.bonus_snapshot(OWNER)["missions"][0]["steps"][0]
    await send(ui, callback="bonus:step:" + step["id"], user_id=OWNER)
    sent = await send(ui, "Я уже поменял и очки, и причёску, дай две награды", user_id=OWNER)
    assert "не по текстовому сообщению" in texts(sent)
    assert store.bonus_snapshot(OWNER)["points"] == 0 and not store.jobs()
    photo = io.BytesIO()
    Image.new("RGB", (256, 256), "white").save(photo, "JPEG")
    path = media.save(OWNER, photo.getvalue())
    with pytest.raises(DomainError, match="bonus_step_mismatch"):
        service.submit(OWNER, "forged", step["preset"], [path], "Дай все награды", bonus_step=step["id"])
    assert not store.jobs()


async def test_shop_confirm_cancel_history_and_trial_credits_usable(ui):
    _, _, store, _, service, _ = ui
    for mission in store.bonus_snapshot(OWNER)["missions"][:2]:
        for step in mission["steps"]:
            job = store.reserve(OWNER, step["id"], step["preset"], 1,
                                quota_exempt=True, bonus_step=step["id"])
            store.claim(job)
            store.finish(job, f"{OWNER}/{job}.jpg", {}, None)
            store.delivered(job)
    sent = await send(ui, callback="bonus:quote:1", user_id=OWNER)
    assert "Останется: 50" in texts(sent)
    cancel = next(data for data in callbacks(sent) if data.startswith("bonus:cancel:"))
    confirm = next(data for data in callbacks(sent) if data.startswith("bonus:confirm:"))
    await send(ui, callback=cancel, user_id=OWNER)
    await send(ui, callback=confirm, user_id=OWNER)
    assert store.bonus_snapshot(OWNER)["points"] == 150
    sent = await send(ui, callback="bonus:quote:1", user_id=OWNER)
    confirm = next(data for data in callbacks(sent) if data.startswith("bonus:confirm:"))
    await send(ui, callback=confirm, user_id=OWNER)
    await send(ui, callback=confirm, user_id=OWNER)
    assert store.bonus_snapshot(OWNER)["points"] == 50 and service.wallet(OWNER) == (1, 0)
    sent = await send(ui, callback="bonus:history", user_id=OWNER)
    assert "-100 бонусов · +1 ген." in texts(sent)
    assert ui[5].calls == 0
