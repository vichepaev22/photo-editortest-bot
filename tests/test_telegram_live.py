import asyncio
from unittest.mock import AsyncMock

import pytest
from test_bot_flow import build_ui, feed, texts

from image_studio.provider import ProviderError
from image_studio.store import DomainError, Store
from image_studio.telegram_live import progress_text


async def queued(ui):
    await feed(ui, callback="preset:glasses")
    await feed(ui, photo=True)
    sent = await feed(ui, "Круглая <синяя> оправа")
    return ui[2].jobs(1)[0]["id"], sent


async def test_stop_queued_owns_job_returns_reserve_once_and_never_calls_image(tmp_path):
    ui = build_ui(tmp_path)
    job, sent = await queued(ui)
    assert "Круглая <синяя> оправа" in texts(sent)
    assert any(b.callback_data == f"live:stop:{job}" for m in sent
               for row in getattr(getattr(m, "reply_markup", None), "inline_keyboard", []) for b in row)
    with pytest.raises(DomainError, match="unknown_job"):
        ui[2].stop_telegram_job(2, job)
    assert ui[4].wallet(1) == (1, 1)
    stopped = await feed(ui, callback=f"live:stop:{job}")
    assert "Попытка не потрачена" in texts(stopped)
    assert ui[4].wallet(1) == (1, 0)
    assert ui[2].stop_telegram_job(1, job) == "inactive"
    assert not await ui[4].process(job) and ui[5].calls == 0
    assert Store(ui[2].path).job(job)["telegram_silent"] == 1


@pytest.mark.parametrize("failure", [False, True])
async def test_stop_inflight_survives_restart_and_preserves_spend_or_failure_refund(tmp_path, failure):
    ui = build_ui(tmp_path)
    _, _, store, _, service, provider = ui
    job, _ = await queued(ui)
    started, release = asyncio.Event(), asyncio.Event()
    original = provider.edit

    async def blocked(*args, **kwargs):
        started.set()
        await release.wait()
        if failure:
            raise ProviderError("provider_rate_limit")
        return await original(*args, **kwargs)

    provider.edit = blocked
    service.deliver, service.notify = AsyncMock(), AsyncMock()
    task = asyncio.create_task(service.process(job))
    await started.wait()
    assert store.job(job)["status"] == "running"
    stopped = await feed(ui, "/stop")
    assert "Создание уже запущено" in texts(stopped)
    assert service.wallet(1) == (1, 1)
    service.store = Store(store.path)
    assert service.store.job(job)["telegram_silent"] == 1
    release.set()
    assert await task is not failure
    service.deliver.assert_not_called()
    service.notify.assert_not_called()
    assert service.wallet(1) == ((1, 0) if failure else (0, 0))
    if not failure:
        result = service.store.result(1, job)
        assert service.media.path(result["result"]).is_file() and provider.calls == 1
        await feed(ui, callback=f"result:{job}")
        assert store.job(job)["status"] == "delivered"


async def test_new_request_supersedes_queued_response_without_late_delivery(tmp_path):
    ui = build_ui(tmp_path)
    old, _ = await queued(ui)
    await feed(ui, callback="preset:hair")
    assert ui[2].job(old)["error"] == "user_cancelled"
    await feed(ui, photo=True)
    await feed(ui, "Каре")
    new = next(j for j in ui[2].jobs(1) if j["id"] != old)
    await feed(ui, callback=f"live:stop:{old}")
    assert ui[2].job(new["id"])["status"] == "queued"
    assert not await ui[4].process(old)
    assert await ui[4].process(new["id"]) and ui[5].calls == 1


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
        if bot.edit_message_text.called and bot.send_chat_action.called:
            break
        await asyncio.sleep(0.01)
    assert bot.send_chat_action.call_args.args == (1, "typing")
    assert "Создаём изображение" in bot.edit_message_text.call_args.kwargs["text"]
    assert bot.edit_message_text.call_args.kwargs["message_id"] == reply.message_id
    assert bot.edit_message_text.call_args.kwargs["parse_mode"] is None
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
