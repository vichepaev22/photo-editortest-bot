import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from image_studio.bot import MAIN, NAV, StepMessages, build_dispatcher, deliver_result
from image_studio.config import Settings
from image_studio.media import Media
from image_studio.provider import MockProvider
from image_studio.service import Service
from image_studio.store import Store
from image_studio.subscriptions import CHANNELS, CHECK_CALLBACK, OFFER_TEXT


class SubscriptionBot(Bot):
    def __init__(self):
        super().__init__("123456789:ABCdefghijklmnopqrstuvwxyz123456789")
        self.sent, self.member_requests = [], []
        self.sequence = 0
        self.members = {}
        self.fail_at = None
        self.fail_edit = ""
        self.fail_document = False
        self.fail_offer = False

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        api = method.__api_method__
        if api == "getChatMember":
            key = method.chat_id, method.user_id
            self.member_requests.append(key)
            if key == self.fail_at:
                raise OSError("private payload must not appear in replies")
            return self.members.get(key, SimpleNamespace(
                status="administrator" if method.user_id == self.id else "member",
            ))
        if api == "answerCallbackQuery":
            return True
        if api == "editMessageText" and self.fail_edit:
            raise TelegramBadRequest(method, self.fail_edit)
        if api == "sendDocument" and self.fail_document:
            raise OSError("offline result delivery failure")
        if api == "sendMessage" and method.text == OFFER_TEXT and self.fail_offer:
            raise OSError("offline optional offer delivery failure")
        return Message(message_id=getattr(method, "message_id", None) or 900,
                       date=datetime.now(timezone.utc), chat=Chat(id=method.chat_id, type="private"))


@pytest.fixture
def ui(tmp_path):
    store, media, provider = Store(tmp_path / "db.sqlite3"), Media(tmp_path), MockProvider()
    store.consent(1)
    service = Service(store, media, provider, trial_access=True)
    service.wallet(1)
    settings = Settings(image_provider="openai", trial_access=True)
    return build_dispatcher(settings, store, media, service), SubscriptionBot(), store, service, provider


async def feed(ui, *, text=None, callback=None, user=1, chat=None, chat_type="private", sender_bot=False):
    dp, bot, *_ = ui
    bot.sequence += 1
    actor = User(id=user, first_name="Тест", is_bot=sender_bot)
    message = Message(message_id=77, date=datetime.now(timezone.utc),
                      chat=Chat(id=chat if chat is not None else user, type=chat_type),
                      from_user=User(id=bot.id, first_name="Бот", is_bot=True) if callback else actor,
                      text=text)
    update = Update(update_id=bot.sequence, message=message) if callback is None else Update(
        update_id=bot.sequence, callback_query=CallbackQuery(id=str(bot.sequence), from_user=actor,
        chat_instance="offline", message=message, data=callback))
    bot.sent.clear()
    await dp.feed_update(bot, update)
    return list(bot.sent)


def texts(sent):
    return "\n".join(getattr(method, "text", "") or "" for method in sent)


def edits(sent):
    return [method for method in sent if method.__api_method__ == "editMessageText"]


async def test_balance_and_bonus_show_direct_verification_without_changing_main(ui):
    main_rows = [[button.text for button in row] for row in MAIN.keyboard]
    balance = await feed(ui, text=NAV["balance"])
    bonus = await feed(ui, text="/bonus")
    for sent in (balance, bonus):
        messages = [method for method in sent if method.__api_method__ == "sendMessage"]
        assert len(messages) == 1
        buttons = [button for row in messages[0].reply_markup.inline_keyboard for button in row]
        assert {button.url for button in buttons if button.url} == {
            "https://t.me/FutureDarkSide", "https://t.me/nofuturenews",
        }
        assert any(button.callback_data == CHECK_CALLBACK for button in buttons)
        assert "1 бесплатная успешная генерация" in messages[0].text
    assert ui[1].member_requests == [] and ui[3].wallet(1) == (1, 0)
    assert [[button.text for button in row] for row in MAIN.keyboard] == main_rows


@pytest.mark.parametrize("status,is_member", [
    ("creator", None), ("administrator", None), ("member", None), ("restricted", True),
])
async def test_membership_grants_once_and_edits_same_message_for_authenticated_actor(ui, status, is_member):
    _, bot, store, service, provider = ui
    bot.members[CHANNELS[1], 1] = SimpleNamespace(status=status, is_member=is_member)
    sent = await feed(ui, callback=CHECK_CALLBACK)
    assert store.has_channel_bonus(1) and service.wallet(1) == (2, 0)
    assert bot.member_requests == [*( (channel, bot.id) for channel in CHANNELS),
                                   *( (channel, 1) for channel in CHANNELS)]
    assert len(edits(sent)) == 1 and edits(sent)[0].chat_id == 1 and edits(sent)[0].message_id == 77
    assert "Начислена 1 дополнительная генерация" in texts(sent)
    assert not any(method.__api_method__ == "sendMessage" for method in sent)
    sent = await feed(ui, callback=CHECK_CALLBACK)
    assert "Повторная выдача" in texts(sent) and len(edits(sent)) == 1
    assert len(bot.member_requests) == 4 and service.wallet(1) == (2, 0)
    assert provider.calls == 0 and store.jobs(1) == []


@pytest.mark.parametrize("channel,status", [
    (CHANNELS[0], "member"), (CHANNELS[1], "left"), (CHANNELS[1], "restricted"),
])
async def test_bot_without_admin_in_either_channel_never_checks_user_or_grants(ui, channel, status):
    _, bot, store, service, _ = ui
    bot.members[channel, bot.id] = SimpleNamespace(status=status, is_member=True)
    sent = await feed(ui, callback=CHECK_CALLBACK)
    assert all(user == bot.id for _, user in bot.member_requests)
    assert not store.has_channel_bonus(1) and service.wallet(1) == (1, 0)
    assert "не удалось проверить" in texts(sent) and len(edits(sent)) == 1


@pytest.mark.parametrize("stage", ["bot", "user"])
async def test_api_error_fails_closed_without_private_payload(ui, stage):
    _, bot, store, service, _ = ui
    bot.fail_at = CHANNELS[1], bot.id if stage == "bot" else 1
    sent = await feed(ui, callback=CHECK_CALLBACK)
    assert not store.has_channel_bonus(1) and service.wallet(1) == (1, 0)
    assert "не удалось проверить" in texts(sent) and "private payload" not in texts(sent)
    assert len(edits(sent)) == 1


@pytest.mark.parametrize("status,is_member", [
    ("left", None), ("kicked", None), ("restricted", False), ("restricted", None), ("unknown", True),
])
async def test_user_missing_either_membership_never_gets_bonus(ui, status, is_member):
    _, bot, store, service, _ = ui
    bot.members[CHANNELS[1], 1] = SimpleNamespace(status=status, is_member=is_member)
    sent = await feed(ui, callback=CHECK_CALLBACK)
    assert not store.has_channel_bonus(1) and service.wallet(1) == (1, 0)
    assert "Не подтверждена подписка на: @nofuturenews" in texts(sent) and len(edits(sent)) == 1
    assert CHANNELS[0] not in texts(sent) and len(bot.member_requests) == 4


async def test_missing_both_channels_are_named(ui):
    for channel in CHANNELS:
        ui[1].members[channel, 1] = SimpleNamespace(status="left")
    sent = await feed(ui, callback=CHECK_CALLBACK)
    assert "Не подтверждена подписка на: @FutureDarkSide, @nofuturenews" in texts(sent)
    assert not ui[2].has_channel_bonus(1) and len(ui[1].member_requests) == 4


@pytest.mark.parametrize("user,chat,chat_type,sender_bot", [
    (1, 2, "private", False), (1, -5, "group", False), (1, 1, "private", True),
])
async def test_foreign_private_chat_group_and_bot_actor_cannot_claim(ui, user, chat, chat_type, sender_bot):
    sent = await feed(ui, callback=CHECK_CALLBACK, user=user, chat=chat,
                      chat_type=chat_type, sender_bot=sender_bot)
    assert ui[1].member_requests == [] and not ui[2].has_channel_bonus(1)
    assert not edits(sent) and not any(method.__api_method__ == "sendMessage" for method in sent)


async def test_consent_required_before_membership_or_award(ui):
    _, bot, store, service, _ = ui
    store.revoke_consent(1)
    sent = await feed(ui, callback=CHECK_CALLBACK)
    assert bot.member_requests == [] and not store.has_channel_bonus(1)
    assert service.wallet(1) == (1, 0) and "Сначала подтвердите" in texts(sent)
    assert len(edits(sent)) == 1


async def test_manual_gift_can_earn_bonus_once_and_no_refill(ui):
    _, bot, store, service, _ = ui
    assert store.grant_manual(1, 10, "a" * 32)
    before = service.wallet(1)
    sent = await feed(ui, text=NAV["balance"])
    assert any(button.callback_data == CHECK_CALLBACK for method in sent
               for row in getattr(method.reply_markup, "inline_keyboard", []) for button in row)
    await feed(ui, callback=CHECK_CALLBACK)
    assert service.wallet(1) == (before[0] + 1, 0) and store.has_channel_bonus(1)
    await feed(ui, callback=CHECK_CALLBACK)
    assert service.wallet(1) == (before[0] + 1, 0) and len(bot.member_requests) == 4


async def test_active_reservation_blocks_bonus_until_generation_finishes(ui):
    _, _, store, service, _ = ui
    job = store.reserve(1, "active", "hair", 1, trial=True)
    sent = await feed(ui, callback=CHECK_CALLBACK)
    assert not store.has_channel_bonus(1) and service.wallet(1) == (1, 1)
    assert "Дождитесь завершения" in texts(sent) and len(edits(sent)) == 1
    store.fail(job, "offline")
    await feed(ui, callback=CHECK_CALLBACK)
    assert store.has_channel_bonus(1) and service.wallet(1) == (2, 0)


async def test_concurrent_claims_cannot_grant_twice(ui):
    await asyncio.gather(feed(ui, callback=CHECK_CALLBACK), feed(ui, callback=CHECK_CALLBACK))
    assert ui[2].has_channel_bonus(1) and ui[3].wallet(1) == (2, 0)
    with ui[2].tx() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events WHERE kind='channel_bonus'").fetchone()[0] == 1
    assert ui[4].calls == 0


@pytest.mark.parametrize("error", ["Bad Request: message is not modified", "Bad Request: message to edit not found"])
async def test_status_edit_error_does_not_send_new_message(ui, error):
    await feed(ui, callback=CHECK_CALLBACK)
    ui[1].fail_edit = error
    sent = await feed(ui, callback=CHECK_CALLBACK)
    assert ui[3].wallet(1) == (2, 0) and len(ui[1].member_requests) == 4
    assert not any(method.__api_method__ == "sendMessage" for method in sent)


def finished_trial(ui, *, exempt=False):
    _, _, store, service, _ = ui
    path = service.media.save(1, b"offline image", "base-result.jpg")
    job = store.reserve(1, "base-for-offer", "hair", 1, trial=True, quota_exempt=exempt)
    assert store.claim(job)
    store.finish(job, path, {}, None)
    return job, service.media.path(path)


def offers(bot):
    return [method for method in bot.sent if method.__api_method__ == "sendMessage" and method.text == OFFER_TEXT]


async def test_successful_base_delivery_offers_once_after_result_even_after_restart_and_download(ui):
    _, bot, store, service, provider = ui
    job, path = finished_trial(ui)
    assert not offers(bot) and store.job(job)["status"] == "generated"
    await deliver_result(bot, store, StepMessages(store), 1, job, path)
    assert len(offers(bot)) == 1 and store.job(job)["status"] == "delivered"
    assert bot.sent.index(offers(bot)[0]) > next(
        index for index, method in enumerate(bot.sent) if method.__api_method__ == "sendDocument"
    )
    assert any(button.callback_data == CHECK_CALLBACK
               for row in offers(bot)[0].reply_markup.inline_keyboard for button in row)
    restarted = Store(store.path)
    new_service = Service(restarted, service.media, provider, trial_access=True)
    settings = Settings(image_provider="openai", trial_access=True)
    restored_ui = build_dispatcher(settings, restarted, service.media, new_service), bot, restarted, new_service, provider
    bot.sent.clear()
    await deliver_result(bot, restarted, StepMessages(restarted), 1, job, path)
    assert not offers(bot)
    await feed(restored_ui, text="/result " + job)
    assert not offers(bot) and provider.calls == 0
    await feed(restored_ui, callback=CHECK_CALLBACK)
    assert restarted.has_channel_bonus(1) and new_service.wallet(1) == (1, 0)
    with restarted.tx() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events WHERE kind='channel_bonus_offer'").fetchone()[0] == 1


async def test_result_delivery_failure_waits_until_manual_retry_before_offer(ui):
    _, bot, store, _, provider = ui
    job, path = finished_trial(ui)
    bot.fail_document = True
    with pytest.raises(OSError):
        await deliver_result(bot, store, StepMessages(store), 1, job, path)
    assert store.job(job)["status"] == "generated" and not offers(bot)
    bot.fail_document = False
    await feed(ui, text="/result " + job)
    assert store.job(job)["status"] == "delivered" and len(offers(bot)) == 1 and provider.calls == 0


@pytest.mark.parametrize("skip", ["owner", "manual_after_base", "claimed_bonus", "demo"])
async def test_owner_manual_gift_claimed_bonus_and_demo_do_not_get_auto_offer(ui, skip):
    _, bot, store, _, _ = ui
    job, path = finished_trial(ui, exempt=skip == "owner")
    if skip == "manual_after_base":
        store.grant_manual(1, 10, "b" * 32)
    elif skip == "claimed_bonus":
        store.grant_channel_bonus(1)
    await deliver_result(bot, store, StepMessages(store), 1, job, path,
                         demo=skip == "demo", unlimited=skip == "owner")
    assert not offers(bot)


async def test_offer_send_failure_preserves_result_and_does_not_repeat_offer(ui):
    _, bot, store, _, _ = ui
    job, path = finished_trial(ui)
    bot.fail_offer = True
    await deliver_result(bot, store, StepMessages(store), 1, job, path)
    assert store.job(job)["status"] == "delivered" and len(offers(bot)) == 1
    bot.sent.clear()
    bot.fail_offer = False
    await deliver_result(bot, store, StepMessages(store), 1, job, path)
    assert not offers(bot)
