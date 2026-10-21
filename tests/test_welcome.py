import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import SendMessageDraft

from image_studio.bot import MAIN
from image_studio.streaming_answer import animated_welcome


def message():
    return SimpleNamespace(bot=AsyncMock(), chat=SimpleNamespace(id=1), message_id=9,
                           answer=AsyncMock(return_value="saved"))


async def test_welcome_drafts_grow_without_stop_and_persist_one_final_with_keyboard():
    msg, pause = message(), AsyncMock()
    text = "Образ · ваша фотостудия ✨\n\n" + "Добро пожаловать. " * 24
    assert await animated_welcome(msg, text, MAIN, pause=pause) == "saved"
    calls = msg.bot.send_message_draft.call_args_list
    assert len(calls) == 4 and len({c.kwargs["draft_id"] for c in calls}) == 1
    assert calls[0].kwargs["draft_id"] != 0
    assert [len(c.kwargs["text"]) for c in calls] == sorted({len(c.kwargs["text"]) for c in calls})
    assert calls[-1].kwargs["text"] == text
    assert all(text.startswith(c.kwargs["text"]) and c.kwargs["can_stop"] is False
               and c.kwargs["parse_mode"] is None for c in calls)
    msg.answer.assert_awaited_once_with(text, reply_markup=MAIN, parse_mode=None)
    msg.bot.edit_message_text.assert_not_called()


@pytest.mark.parametrize("rate_limit", [False, True])
async def test_draft_unavailable_or_429_still_delivers_complete_greeting(rate_limit):
    msg, pause = message(), AsyncMock()
    method = SendMessageDraft(chat_id=1, draft_id=1, text="test")
    msg.bot.send_message_draft.side_effect = (
        TelegramRetryAfter(method=method, message="flood", retry_after=5) if rate_limit else
        TelegramBadRequest(method=method, message="method unavailable")
    )
    await animated_welcome(msg, "Полное приветствие", MAIN, pause=pause)
    msg.answer.assert_awaited_once_with("Полное приветствие", reply_markup=MAIN, parse_mode=None)
    if rate_limit:
        assert 5 in [c.args[0] for c in pause.call_args_list]


async def test_shutdown_during_greeting_leaves_no_typing_task_or_late_send():
    msg = message()
    waiting = asyncio.Event()

    async def pause(_):
        waiting.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(animated_welcome(msg, "Приветствие для пользователя", MAIN, pause=pause))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    msg.answer.assert_not_called()
    count = msg.bot.send_chat_action.call_count
    await asyncio.sleep(0)
    assert msg.bot.send_chat_action.call_count == count
