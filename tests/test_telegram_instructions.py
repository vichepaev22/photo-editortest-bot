import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramRetryAfter
from test_bot_flow import TrackedBot, feed, own_photo
from test_telegram_dialog import ui

from image_studio.bot import MAIN
from image_studio.telegram_attention import EntryAttention
from image_studio.telegram_instructions import PANEL_TEXT, InstructionGuide, is_menu_request


async def test_entry_has_instruction_and_photo_invitation_in_both_renderers(tmp_path):
    state = ui(tmp_path)
    adapter = state[0]["photo_dialog"]
    card = adapter.core.entry()
    html = adapter.rich(card).html
    assert "guide:open" in html and "nav:home" not in html
    assert "или просто пришли фотографию" in html
    rows = adapter.keyboard(card).inline_keyboard
    assert rows[-1][0].text == "📖 Инструкция" and rows[-1][0].callback_data == "guide:open"


async def test_instruction_pairs_correct_assets_keyboard_only_at_end():
    bot = TrackedBot()
    guide = InstructionGuide(MAIN, pause=AsyncMock())
    await guide.show(own_photo().as_(bot))
    permanent = [m for m in bot.sent if m.__api_method__ in {"sendMessage", "sendPhoto"}]
    assert [m.__api_method__ for m in permanent] == ["sendMessage", "sendPhoto"] * 3
    photos = [m for m in permanent if m.__api_method__ == "sendPhoto"]
    assert [Path(m.photo.path).name for m in photos] == [
        "obraz-guide-01-photo.png",
        "obraz-guide-02-studio.png",
        "obraz-guide-03-menu.png",
    ]
    assert all(Path(m.photo.path).is_file() for m in photos)
    assert all(m.reply_markup is None for m in permanent[:-1])
    assert permanent[-1].reply_markup == MAIN and permanent[-1].caption == PANEL_TEXT
    texts = [m.text for m in permanent if m.__api_method__ == "sendMessage"]
    assert "Отправить" in texts[0] and "внизу слева" in texts[1]
    assert "Открой основное меню" in texts[2]
    assert any(m.__api_method__ == "sendMessageDraft" for m in bot.sent)


async def test_spoken_menu_bypasses_llm_and_wallet_and_question_is_not_command(tmp_path):
    state = ui(tmp_path)
    core = state[0]["photo_dialog"].core
    core.message = AsyncMock(side_effect=AssertionError("menu must not reach image dialog"))
    before = state[4].wallet(1)
    for phrase in (
        "Открой основное меню",
        "открой меню!",
        "Покажите главное меню, пожалуйста.",
        "Пожалуйста, открой основное меню",
    ):
        await feed(state, phrase)
        assert state[1].sent[-1].reply_markup == MAIN
        assert "или просто пришли фотографию" in state[1].sent[-1].text
    core.message.assert_not_awaited()
    assert not state[2].jobs() and state[4].wallet(1) == before
    assert not is_menu_request("Как открыть основное меню?")


@pytest.mark.parametrize("rich", [True, False])
async def test_finite_highlights_preserve_targets_and_finish_neutral(tmp_path, rich):
    state = ui(tmp_path)
    adapter = state[0]["photo_dialog"]
    attention = EntryAttention(adapter, pause=AsyncMock())
    await attention.play(own_photo().as_(state[1]), adapter.core.entry(), rich=rich)
    edits = state[1].sent
    assert len(edits) == 6
    expected = ["dlg:pick:glasses", "dlg:pick:hair", "dlg:more", "studio", "guide:open", None]
    if rich:
        for method, key in zip(edits, expected):
            html = method.rich_message.html
            assert html.count('style="success"') == bool(key)
            if key and key != "studio":
                assert f'data="{key}" style="success"' in html
            assert "https://example.com/studio" in html and "guide:open" in html
    else:
        for method, key in zip(edits, expected):
            rows = method.reply_markup.inline_keyboard
            active = [b for row in rows for b in row if b.style == "success"]
            assert len(active) == bool(key)
            if active:
                assert (active[0].callback_data or "studio") == key
    assert not state[2].jobs()


async def test_user_action_cancels_attention_before_showing_main_menu(tmp_path):
    state = ui(tmp_path)
    adapter = state[0]["photo_dialog"]
    painted = asyncio.Event()
    calls = 0

    async def pause(_):
        nonlocal calls
        calls += 1
        if calls > 1:
            painted.set()
            await asyncio.Event().wait()

    adapter.attention.pause = pause
    adapter.attention.start(own_photo().as_(state[1]), adapter.core.entry(), rich=False)
    await painted.wait()
    await feed(state, "Открой основное меню")
    assert not adapter.attention.tasks
    assert state[1].sent[-1].reply_markup == MAIN
    assert all(b.style is None for row in state[1].sent[-2].reply_markup.inline_keyboard for b in row)
    assert not state[2].jobs()


async def test_instruction_can_be_interrupted_without_clearing_photo_draft(tmp_path):
    state = ui(tmp_path)
    guide = state[0]["instruction_guide"]
    core = state[0]["photo_dialog"].core
    await core.action(1, "dlg:pick:glasses")
    token = core.current(1).token
    started = asyncio.Event()

    async def blocked(message):
        started.set()
        await asyncio.Event().wait()

    guide.show = blocked
    await feed(state, callback="guide:open")
    await started.wait()
    await feed(state, "Привет")
    assert not guide.tasks and core.current(1).token == token
    assert not state[2].jobs()


async def test_rate_limited_attention_stops_without_send_or_rapid_retry(tmp_path):
    class Limited(TrackedBot):
        attempts = 0

        async def __call__(self, method, request_timeout=None):
            if method.__api_method__.startswith("editMessage"):
                self.attempts += 1
                raise TelegramRetryAfter(method=method, message="flood", retry_after=30)
            return await super().__call__(method, request_timeout)

    state = ui(tmp_path, Limited())
    adapter = state[0]["photo_dialog"]
    attention = EntryAttention(adapter, pause=AsyncMock())
    await attention.play(own_photo().as_(state[1]), adapter.core.entry(), rich=False)
    assert state[1].attempts == 1 and not state[1].sent and not state[2].jobs()
