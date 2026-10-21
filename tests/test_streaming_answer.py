import asyncio
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import EditMessageText

from image_studio.streaming_answer import StreamingAnswer, telegram_text


async def test_one_plain_message_real_chunks_and_clean_final():
    bot = AsyncMock()
    answer = StreamingAnswer(bot, 1, 91, interval=0)

    async def chunks():
        yield "<b>это пользовательский текст</b> " + "а" * 310
        yield "\n\n\nконец   "

    final = await answer.stream(chunks(), summary="→ Изменить фон")
    calls = bot.edit_message_text.call_args_list
    assert len(calls) == 2
    assert all(c.kwargs["chat_id"] == 1 and c.kwargs["message_id"] == 91 for c in calls)
    assert all(c.kwargs["parse_mode"] is None for c in calls)
    assert "<b>" in calls[-1].kwargs["text"]
    assert calls[0].kwargs["text"].endswith(" …")
    assert calls[-1].kwargs["text"].endswith("конец") and final.endswith("конец")
    count = bot.send_chat_action.call_count
    await asyncio.sleep(0)
    assert bot.send_chat_action.call_count == count


async def test_cancel_closes_source_typing_and_never_finishes():
    bot = AsyncMock()
    started, closed, typing = asyncio.Event(), asyncio.Event(), asyncio.Event()
    bot.send_chat_action.side_effect = lambda *a, **kw: typing.set()

    async def chunks():
        try:
            started.set()
            await asyncio.Event().wait()
            yield "поздний ответ"
        finally:
            closed.set()

    answer = StreamingAnswer(bot, 1, 91, interval=0, typing_interval=0.005)
    task = asyncio.create_task(answer.stream(chunks()))
    await started.wait()
    await typing.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    bot.edit_message_text.assert_not_called()
    count = bot.send_chat_action.call_count
    await asyncio.sleep(0.012)
    assert count > 0 and bot.send_chat_action.call_count == count


async def test_429_obeys_provider_pause_and_edit_throttle(monkeypatch):
    bot = AsyncMock()
    method = EditMessageText(chat_id=1, message_id=91, text="текст")
    bot.edit_message_text.side_effect = [TelegramRetryAfter(method=method, message="flood", retry_after=7),
                                         True, True]
    sleep = AsyncMock()
    monkeypatch.setattr("image_studio.streaming_answer.asyncio.sleep", sleep)
    answer = StreamingAnswer(bot, 1, 91)
    await answer.edit("первый")
    await answer.edit("второй")
    delays = [c.args[0] for c in sleep.call_args_list]
    assert 7 in delays and any(1 <= n <= 1.2 for n in delays)
    assert bot.edit_message_text.call_count == 3


async def test_not_modified_is_not_retried_and_other_400_propagates():
    bot = AsyncMock()
    method = EditMessageText(chat_id=1, message_id=91, text="текст")
    bot.edit_message_text.side_effect = TelegramBadRequest(method=method, message="message is not modified")
    answer = StreamingAnswer(bot, 1, 91, interval=0)
    await answer.edit("тот же")
    await answer.edit("тот же")
    assert bot.edit_message_text.call_count == 1
    bot.edit_message_text.side_effect = TelegramBadRequest(method=method, message="message to edit not found")
    with pytest.raises(TelegramBadRequest):
        await answer.edit("другой")


def test_unicode_limit_is_explicit_and_never_splits_surrogate_pair():
    text = telegram_text("✨🚀" * 3000)
    assert len(text.encode("utf-16-le")) <= 8192
    assert text.endswith("[Текст сокращён]") and "�" not in text
