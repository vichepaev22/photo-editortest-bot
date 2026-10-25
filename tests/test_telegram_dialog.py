import io

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from PIL import Image
from test_bot_flow import TrackedBot, feed

from image_studio.bot import build_dispatcher
from image_studio.config import Settings
from image_studio.media import Media
from image_studio.photo_dialog import PhotoDialog
from image_studio.provider import MockProvider
from image_studio.service import Service
from image_studio.store import Store


def ui(tmp_path, bot=None):
    store, media = Store(tmp_path / "db.sqlite3"), Media(tmp_path)
    provider = MockProvider()
    store.consent(1)
    store.grant_pilot(1, 10)
    service = Service(store, media, provider)
    dp = build_dispatcher(
        Settings(dialog_enabled=True, mini_app_url="https://example.com/studio"), store, media, service
    )
    return dp, bot or TrackedBot(), store, media, service, provider


async def test_quick_grouped_native_card_and_photo_dialog_confirm(tmp_path):
    state = ui(tmp_path)
    await feed(state, "Привет")
    assert state[1].sent[-1].text.startswith("Привет")
    await feed(state, callback="dlg:more")
    rich = state[1].sent[-1].rich_message.html
    assert "Новый образ" in rich and "Работа с фотографией" in rich and "web_app" in rich
    assert rich.count("dlg:pick:") == 8
    await feed(state, "Смени фон на парк")
    await feed(state, photo=True)
    assert not state[2].jobs()
    card = state[1].sent[-1]
    confirm = card.reply_markup.inline_keyboard[0][0].callback_data
    assert confirm.startswith("dlg:confirm:") and "Расход: 1" in card.text
    await feed(state, callback=confirm)
    assert len(state[2].jobs()) == 1 and state[5].calls == 0
    await feed(state, callback=confirm)
    assert len(state[2].jobs()) == 1
    assert state[0]["telegram_live"].replies
    # Ordinary legacy source selection still exists and clears the dialog safely.
    await feed(state, callback="preset:glasses")
    assert "Шаг 1" in state[1].sent[-1].text


async def test_start_entry_after_last_collage_and_consent(tmp_path, monkeypatch):
    state = ui(tmp_path)

    async def intro(message, keyboard, **kwargs):
        await message.answer("third collage marker")

    monkeypatch.setattr("image_studio.bot.send_onboarding", intro)
    await feed(state, "/start")
    assert state[1].sent[-1].__api_method__ == "sendRichMessage"
    assert state[1].sent[-2].text == "third collage marker"
    await feed(state, "/start", user=2)
    assert state[1].sent[-1].reply_markup.inline_keyboard[0][0].callback_data == "consent"
    await feed(state, callback="consent", user=2)
    assert state[1].sent[-1].__api_method__ == "sendRichMessage"
    assert not state[2].jobs()


@pytest.mark.parametrize("uncertain", [False, True])
async def test_rich_fallback_only_after_definite_rejection(tmp_path, uncertain):
    class RejectRich(TrackedBot):
        async def __call__(self, method, request_timeout=None):
            if method.__api_method__ == "sendRichMessage":
                error = TelegramNetworkError if uncertain else TelegramBadRequest
                raise error(method=method, message="rich method unsupported")
            return await super().__call__(method, request_timeout)

    state = ui(tmp_path, RejectRich())
    dialog = state[0]["photo_dialog"]
    message = __import__("test_bot_flow").own_photo().as_(state[1])
    if uncertain:
        with pytest.raises(TelegramNetworkError):
            await dialog.show(message, 1, PhotoDialog.entry())
        assert not state[1].sent
    else:
        await dialog.show(message, 1, PhotoDialog.entry())
        assert state[1].sent[-1].__api_method__ == "sendMessage"
        assert state[1].sent[-1].reply_markup.inline_keyboard[0][0].text == "👓 Очки"


async def test_caption_questions_and_foreign_reply_do_not_create(tmp_path):
    state = ui(tmp_path)
    core = state[0]["photo_dialog"].core
    b = io.BytesIO()
    Image.new("RGB", (64, 64)).save(b, "JPEG")
    p = state[3].save(1, b.getvalue())
    await core.message(1, "Смени фон на парк", images=[p])
    token = core.current(1).token
    await feed(state, "А сколько вариантов можно?")
    assert core.current(1).token == token and not state[2].jobs()
    await feed(state, "возьми это фото", reply=__import__("test_bot_flow").own_photo(user=2))
    assert "недоступно" in state[1].sent[-1].text
    assert not state[2].jobs()


async def test_global_dialog_keeps_bonus_shop_private_and_does_not_award_mission(tmp_path):
    owner = 3975497
    state = ui(tmp_path)
    _, bot, store, media, _, provider = state
    store.consent(owner)
    service = Service(store, media, provider, trial_access=True, unlimited_user_id=owner)
    settings = Settings(dialog_enabled=True, bonus_shop_enabled=True, admin_user_id=owner,
                        owner_unlimited_testing=True, trial_access=True)
    dp = build_dispatcher(settings, store, media, service)
    core = dp['photo_dialog'].core
    b = io.BytesIO()
    Image.new('RGB', (64, 64)).save(b, 'JPEG')
    p = media.save(owner, b.getvalue())
    await core.message(owner, 'Смени фон на парк', images=[p])
    card = await core.action(owner, 'dlg:confirm:' + core.current(owner).token)
    await service.process(card.job)
    store.delivered(card.job)
    await dp['bonus_shop'].after_job(bot, owner, card.job)
    assert store.bonus_snapshot(owner)['points'] == 0
    assert dp['bonus_shop'].allowed(owner) and not dp['bonus_shop'].allowed(1)
