import asyncio
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import SendRichMessageDraft
from aiogram.types import InputRichMessage, ReplyParameters, User
from test_bot_flow import own_photo
from test_telegram_dialog import ui

from image_studio.bot import StepMessages, deliver_result
from image_studio.streaming_answer import StreamingAnswer
from image_studio.telegram_processing import ProcessingStatus, request_reply


def method():
    return SendRichMessageDraft(chat_id=1, draft_id=2, rich_message=InputRichMessage(html="test"))


async def test_real_stage_time_escaping_and_one_draft_with_safe_fallback():
    bot, now = AsyncMock(), [100.0]
    legacy = StreamingAnswer(bot, 1, 55, interval=0)
    status = ProcessingStatus(bot, 1, "Получаю <фото>", clock=lambda: now[0], fallback=legacy)
    await status.publish()
    now[0] += 6
    status.step("Создаю изображение")
    await status.publish()
    calls = bot.send_rich_message_draft.call_args_list
    assert len({c.kwargs["draft_id"] for c in calls}) == 1
    assert "&lt;фото&gt;" in calls[-1].kwargs["rich_message"].html
    assert "✓ Получаю &lt;фото&gt; · 6 с" in calls[-1].kwargs["rich_message"].html
    assert "Обрабатываю запрос · 6 с" in calls[-1].kwargs["rich_message"].html
    assert all(c.kwargs["can_stop"] is False for c in calls)
    bot.send_rich_message_draft.side_effect = TelegramBadRequest(method=method(), message="unsupported")
    await status.publish()
    await status.publish()
    assert status.mode == "plain" and bot.send_message_draft.call_args.kwargs["parse_mode"] is None
    bot.send_message_draft.side_effect = TelegramBadRequest(method=method(), message="unsupported")
    await status.publish()
    await status.publish()
    assert bot.edit_message_text.call_args.kwargs["message_id"] == 55
    bot.send_message.assert_not_called()


async def test_flood_wait_survives_stage_changes_and_closed_status_stays_closed():
    bot, now = AsyncMock(), [10.0]
    status = ProcessingStatus(bot, 1, "Получаю фото", clock=lambda: now[0])
    bot.send_rich_message_draft.side_effect = TelegramRetryAfter(method=method(), message="flood", retry_after=9)
    assert await status.publish() == 9
    status.step("Подготавливаю фото")
    now[0] += 8
    await status.publish()
    assert bot.send_rich_message_draft.call_count == 1
    now[0] += 1
    bot.send_rich_message_draft.side_effect = None
    await status.publish()
    assert bot.send_rich_message_draft.call_count == 2
    await status.close()
    await status.publish()
    assert bot.send_rich_message_draft.call_count == 2


def test_reply_never_anchors_to_other_user_chat_or_bot():
    source = own_photo(message_id=77)
    assert request_reply(source, 1).message_id == 77
    assert request_reply(source, 1).allow_sending_without_reply is True
    assert request_reply(own_photo(user=2), 1) is None
    assert request_reply(own_photo(chat=2), 1) is None
    bot_message = source.model_copy(update={"from_user": User(id=100, is_bot=True, first_name="Bot"),
                                            "reply_to_message": source, "message_id": 90})
    assert request_reply(bot_message, 1).message_id == 77


async def test_delayed_dialog_uses_native_status_and_one_persisted_reply(tmp_path, monkeypatch):
    state = ui(tmp_path)
    dialog, bot = state[0]["photo_dialog"], state[1]
    from image_studio.photo_dialog import Card

    original = ProcessingStatus.__init__

    def immediate(self, *args, **kwargs):
        original(self, *args, **kwargs, grace=0)

    monkeypatch.setattr(ProcessingStatus, "__init__", immediate)

    async def interpret(*args, **kwargs):
        for _ in range(20):
            if any(m.__api_method__ == "sendRichMessageDraft" for m in bot.sent):
                break
            await asyncio.sleep(0.01)
        return Card("Подскажу, что изменить. " * 12)

    dialog.core.message = interpret
    source = own_photo(message_id=77).model_copy(update={"photo": None, "text": "Что можно сделать?"}).as_(bot)
    await dialog.receive(source)
    rich = [m for m in bot.sent if m.__api_method__ == "sendRichMessageDraft"]
    drafts = [m for m in bot.sent if m.__api_method__ == "sendMessageDraft"]
    final = [m for m in bot.sent if m.__api_method__ == "sendMessage"]
    assert len(rich) == 1 and len(drafts) == 2 and len(final) == 1
    assert all(m.draft_id == rich[0].draft_id for m in drafts)
    assert final[0].reply_parameters.message_id == 77
    count = len(bot.sent)
    await asyncio.sleep(0.01)
    assert len(bot.sent) == count and state[5].calls == 0 and not state[2].jobs()


async def test_final_file_is_anchored_without_extra_generation(tmp_path):
    state = ui(tmp_path)
    _, bot, store, media, service, provider = state
    import io

    from PIL import Image

    data = io.BytesIO()
    Image.new("RGB", (64, 64)).save(data, "JPEG")
    source = media.save(1, data.getvalue())
    job = service.submit(1, "test-native-delivery", "glasses", [source], "Круглая оправа")
    assert await service.process(job)
    wallet = service.wallet(1)
    await deliver_result(bot, store, StepMessages(store), 1, job, media.path(store.job(job)["result"]),
                         demo=True, reply_parameters=ReplyParameters(message_id=77, allow_sending_without_reply=True))
    document = next(m for m in bot.sent if m.__api_method__ == "sendDocument")
    assert document.reply_parameters.message_id == 77
    assert provider.calls == 1 and service.wallet(1) == wallet
