import io
from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, ReplyKeyboardMarkup, Update, User
from PIL import Image

from image_studio.bot import build_dispatcher
from image_studio.config import Settings
from image_studio.media import Media
from image_studio.provider import MockProvider
from image_studio.service import Service
from image_studio.store import Store


class OfflineBot(Bot):
    def __init__(self):
        super().__init__("123456789:ABCdefghijklmnopqrstuvwxyz123456789")
        self.sent = []
        self.sequence = 0

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        if method.__api_method__ == "answerCallbackQuery":
            return True
        return Message(message_id=900, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private"))

    async def download(self, file, destination=None, **kwargs):
        buf = io.BytesIO()
        Image.new("RGB", (256, 256), "#c8b9a0").save(buf, "JPEG")
        destination.write(buf.getvalue())
        return destination


@pytest.fixture
def ui(tmp_path):
    store, media, provider = Store(tmp_path / "db.sqlite3"), Media(tmp_path), MockProvider()
    store.consent(1)
    store.grant_demo(1)
    service = Service(store, media, provider)
    bot = OfflineBot()
    dp = build_dispatcher(Settings(demo_credits=True), store, media, service)
    return dp, bot, store, media, service, provider


async def send(ui, text=None, callback=None, user_id=1, photo=False):
    dp, bot, *_ = ui
    user = User(id=user_id, is_bot=False, first_name="Тест")
    bot.sequence += 1
    mid = bot.sequence
    fields = {"photo": [PhotoSize(file_id="test", file_unique_id="test", width=256, height=256)]}
    message = Message(
        message_id=mid,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=user,
        text=text,
        **(fields if photo else {}),
    )
    update = (
        Update(update_id=mid, message=message)
        if callback is None
        else Update(
            update_id=mid,
            callback_query=CallbackQuery(
                id=str(mid),
                from_user=user,
                chat_instance="test",
                message=message,
                data=callback,
            ),
        )
    )
    bot.sent.clear()
    await dp.feed_update(bot, update)
    return bot.sent


def confirmation(sent):
    for method in sent:
        markup = getattr(method, "reply_markup", None)
        if markup and hasattr(markup, "inline_keyboard"):
            for row in markup.inline_keyboard:
                for button in row:
                    if button.callback_data and button.callback_data.startswith("confirm:"):
                        return button
    return None


async def test_bottom_navigation_after_start_and_consent(ui):
    sent = await send(ui, "/start")
    keyboards = [
        m.reply_markup for m in sent if isinstance(getattr(m, "reply_markup", None), ReplyKeyboardMarkup)
    ]
    assert len(keyboards) == 1
    keyboard = keyboards[0]
    assert keyboard.resize_keyboard and keyboard.is_persistent
    assert len(keyboard.keyboard) == 3
    assert {b.text for row in keyboard.keyboard for b in row} == {
        "📸 Изменить фото",
        "🧩 Объединить фото",
        "💎 Мои попытки",
        "🖼 Мои результаты",
        "❓ Как пользоваться",
        "💬 Поддержка",
    }
    sent = await send(ui, callback="consent", user_id=2)
    assert any(isinstance(getattr(m, "reply_markup", None), ReplyKeyboardMarkup) for m in sent)


async def test_navigation_does_not_become_description_and_home_expires_confirmation(ui):
    await send(ui, callback="preset:hair")
    await send(ui, photo=True)
    for label in ["💎 Мои попытки", "❓ Как пользоваться", "💬 Поддержка", "🖼 Мои результаты"]:
        assert confirmation(await send(ui, label)) is None
    button = confirmation(await send(ui, "Каре до плеч"))
    assert button is not None and button.style == "success" and "1" in button.text
    await send(ui, callback="nav:home")
    await send(ui, callback=button.callback_data)
    assert ui[2].jobs(1) == [] and ui[5].calls == 0


async def test_merge_shortcut_requires_two_distinct_photos(ui):
    await send(ui, "🧩 Объединить фото")
    await send(ui, photo=True)
    assert confirmation(await send(ui, "На одной фотографии")) is None
    await send(ui, photo=True)
    button = confirmation(await send(ui, "Вместе в парке"))
    assert button and "2" in button.text
    await send(ui, callback=button.callback_data)
    assert len(ui[2].jobs(1)) == 1 and ui[2].wallet(1) == (3, 2)


async def test_owned_result_button_reuses_file_and_rejects_foreign_owner(ui):
    _, _, store, media, service, provider = ui
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), "#c8b9a0").save(buf, "JPEG")
    photo = media.save(1, buf.getvalue())
    job = service.submit(1, "result-test", "hair", [photo], "Каре")
    await service.process(job)
    calls = provider.calls
    own = await send(ui, callback="result:" + job)
    assert any(m.__api_method__ == "sendDocument" for m in own)
    foreign = await send(ui, callback="result:" + job, user_id=2)
    assert not any(m.__api_method__ == "sendDocument" for m in foreign)
    assert provider.calls == calls


async def test_navigation_labels_do_not_bypass_consent(ui):
    sent = await send(ui, "🧩 Объединить фото", user_id=2)
    assert any(
        "услов" in getattr(m, "text", "").lower() or "согласи" in getattr(m, "text", "").lower() for m in sent
    )
    assert ui[2].jobs(2) == []
