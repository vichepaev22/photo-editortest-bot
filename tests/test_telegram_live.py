import asyncio
from unittest.mock import AsyncMock

import pytest
from test_bot_flow import build_ui, feed, texts

from image_studio.bot import COMMANDS
from image_studio.telegram_live import progress_text


async def queued(ui):
    await feed(ui, callback="preset:glasses")
    await feed(ui, photo=True)
    sent = await feed(ui, "Круглая <синяя> оправа")
    return ui[2].jobs(1)[0]["id"], sent


async def test_progress_has_no_stop_button_or_command(tmp_path):
    ui = build_ui(tmp_path)
    job, sent = await queued(ui)
    assert "Круглая <синяя> оправа" in texts(sent)
    assert not any("stop" in (b.callback_data or "") for m in sent
                   for row in getattr(getattr(m, "reply_markup", None), "inline_keyboard", []) for b in row)
    assert "Стоп" not in texts(sent) and "stop" not in {c.command for c in COMMANDS}
    assert ui[4].wallet(1) == (1, 1)
    assert ui[2].job(job)["status"] == "queued" and ui[5].calls == 0


@pytest.mark.parametrize("running", [False, True])
async def test_old_stop_and_new_request_never_interrupt_or_hide_delivery(tmp_path, running):
    ui = build_ui(tmp_path)
    _, _, store, _, service, provider = ui
    job, _ = await queued(ui)
    started, release = asyncio.Event(), asyncio.Event()
    original = provider.edit

    async def blocked(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    provider.edit = blocked
    service.deliver, service.notify = AsyncMock(), AsyncMock()
    task = asyncio.create_task(service.process(job)) if running else None
    if task:
        await started.wait()
    await feed(ui, "/stop")
    await feed(ui, callback=f"live:stop:{job}")
    await feed(ui, "Ещё одно пожелание")
    await feed(ui, callback="preset:hair")
    assert store.job(job)["status"] == ("running" if running else "queued")
    assert store.job(job)["telegram_silent"] == 0
    assert service.wallet(1) == (1, 1)
    release.set()
    assert await task if task else await service.process(job)
    service.deliver.assert_awaited_once()
    service.notify.assert_not_called()
    assert service.wallet(1) == (0, 0) and provider.calls == 1
    assert store.job(job)["status"] == "delivered"
    assert service.media.path(store.result(1, job)["result"]).is_file()


async def test_live_dialogue_one_message_real_stages_and_shutdown(tmp_path):
    ui = build_ui(tmp_path)
    job, _ = await queued(ui)
    live = ui[0]["telegram_live"]
    reply = live.replies[job]
    bot = AsyncMock()
    reply.bot = bot
    live.stage(1, job, "generating")
    assert reply.stage == "generating"
    live.stage(2, job, "reference")
    assert reply.stage == "generating"
    task = asyncio.create_task(live.worker())
    for _ in range(15):
        if bot.send_rich_message_draft.called and bot.send_chat_action.called:
            break
        await asyncio.sleep(0.01)
    assert bot.send_chat_action.call_args.args == (1, "typing")
    payload = bot.send_rich_message_draft.call_args.kwargs
    assert "Создаём изображение" in payload["rich_message"].html
    assert payload["rich_message"].html.startswith("<tg-thinking>")
    assert payload["draft_id"] == reply.processing.draft_id
    assert payload["can_stop"] is False and payload["keep_on_stop"] is False
    bot.edit_message_text.assert_not_called()
    await live.interrupt(1)
    assert reply.task.done() and reply.processing.closed
    assert ui[2].job(job)["status"] == "queued" and ui[4].wallet(1) == (1, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not live.replies and reply.task.done()
    count = bot.send_chat_action.call_count
    await asyncio.sleep(0.01)
    assert bot.send_chat_action.call_count == count


def test_plan_matches_requested_function_and_is_not_fabricated_reasoning():
    text = progress_text("glasses", "Тонкая оправа", "generating")
    assert "Очки" in text and "Тонкая оправа" in text
    assert "Причёска" not in text and "мысл" not in text and "%" not in text
