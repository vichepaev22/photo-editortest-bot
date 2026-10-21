from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from test_bot_flow import build_ui, feed
from test_bot_ui import OfflineBot

from image_studio.bot import MAIN
from image_studio.onboarding import send_onboarding


def incoming(bot):
    from datetime import datetime, timezone

    from aiogram.types import Chat, Message

    return Message(message_id=7, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private")).as_(bot)


async def test_three_animated_pairs_in_order_original_pngs_and_footer_only_at_end():
    bot, pause = OfflineBot(), AsyncMock()
    await send_onboarding(incoming(bot), MAIN, notice="Права на фото · доступно 1", pause=pause)
    permanent = [m for m in bot.sent if m.__api_method__ in {"sendMessage", "sendPhoto"}]
    assert [m.__api_method__ for m in permanent] == ["sendMessage", "sendPhoto"] * 3
    texts = [m.text for m in permanent if m.__api_method__ == "sendMessage"]
    assert "Привет! Я Образ" in texts[0] and "👕" in texts[0] and "👓" in texts[0]
    assert "И это ещё не всё" in texts[1] and "две фотографии" in texts[1]
    assert "похожий кадр с тобой" in texts[2] and "35 × 45 мм" in texts[2]
    assert "Права на фото" not in texts[0] + texts[1] and "Права на фото" in texts[2]
    assert permanent[0].reply_markup == MAIN and permanent[4].reply_markup == MAIN
    images = [Path(m.photo.path) for m in permanent if m.__api_method__ == "sendPhoto"]
    assert [p.name for p in images] == ["obraz-start-01-style.png", "obraz-start-02-photo.png",
                                       "obraz-start-03-tools.png"]
    assert all(p.is_file() and 0 < p.stat().st_size < 10_000_000 for p in images)
    previews = [m for m in bot.sent if m.__api_method__ == "sendMessageDraft"]
    assert len(previews) == 12 and len({m.draft_id for m in previews}) == 3
    assert all(m.can_stop is False and m.parse_mode is None for m in previews)
    assert [m.text for m in previews[3::4]] == texts
    assert [c.args[0] for c in pause.call_args_list].count(2) == 2


@pytest.mark.parametrize("failure", ["rate_limit", "network"])
async def test_photo_backoff_or_uncertain_send_keeps_remaining_text_and_keyboard(failure):
    class FailingPhotoBot(OfflineBot):
        attempts = 0

        async def __call__(self, method, request_timeout=None):
            if method.__api_method__ == "sendPhoto":
                self.attempts += 1
                if self.attempts == 1:
                    if failure == "rate_limit":
                        raise TelegramRetryAfter(method=method, message="flood", retry_after=7)
                    raise TelegramNetworkError(method=method, message="uncertain upload")
            return await super().__call__(method, request_timeout)

    bot, pause = FailingPhotoBot(), AsyncMock()
    await send_onboarding(incoming(bot), MAIN, notice="Согласие и баланс", pause=pause)
    texts = [m for m in bot.sent if m.__api_method__ == "sendMessage"]
    assert len(texts) == 3 and "Согласие и баланс" in texts[-1].text
    assert texts[-1].reply_markup == MAIN
    assert bot.attempts == (4 if failure == "rate_limit" else 3)
    if failure == "rate_limit":
        assert 7 in [c.args[0] for c in pause.call_args_list]


async def test_new_user_consent_follows_third_photo_and_repeat_start_does_not_spend_or_grant(tmp_path, monkeypatch):
    from image_studio.onboarding import send_onboarding as real_onboarding

    async def fast_onboarding(message, keyboard, **kwargs):
        return await real_onboarding(message, keyboard, **kwargs, pause=AsyncMock())

    monkeypatch.setattr("image_studio.bot.send_onboarding", fast_onboarding)
    ui = build_ui(tmp_path)
    sent = await feed(ui, "/start", user=2)
    photos = [i for i, m in enumerate(sent) if m.__api_method__ == "sendPhoto"]
    consent = [i for i, m in enumerate(sent)
               if any(b.callback_data == "consent"
                      for row in getattr(getattr(m, "reply_markup", None), "inline_keyboard", []) for b in row)]
    assert len(photos) == 3 and len(consent) == 1 and consent[0] > photos[-1]
    assert not ui[2].has_consent(2)
    await feed(ui, callback="consent", user=2)
    wallet = ui[4].wallet(2)
    repeated = await feed(ui, "/start", user=2)
    assert not any(b.callback_data == "consent" for m in repeated
                   for row in getattr(getattr(m, "reply_markup", None), "inline_keyboard", []) for b in row)
    assert ui[4].wallet(2) == wallet and ui[2].jobs(2) == [] and ui[5].calls == 0
