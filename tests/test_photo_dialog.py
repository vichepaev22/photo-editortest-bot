import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from image_studio.media import Media
from image_studio.photo_dialog import PhotoDialog
from image_studio.provider import MockProvider
from image_studio.service import Service
from image_studio.store import Store


@pytest.fixture
def dialog(tmp_path):
    store, media, provider = Store(tmp_path / "db.sqlite3"), Media(tmp_path), MockProvider()
    store.consent(1)
    store.grant_pilot(1, 10)
    service = Service(store, media, provider)
    return PhotoDialog(store, media, service), store, media, provider, service


def photo(media, user=1):
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), "white").save(buf, "JPEG")
    return media.save(user, buf.getvalue())


async def test_photo_first_and_text_first_get_same_ready_preview(dialog):
    core, store, media, provider, _ = dialog
    assert "Что" in (await core.message(1, "", images=[photo(media)])).text
    card = await core.message(1, "Примерь круглые очки")
    assert "вариантов" in card.text
    token = core.current(1).token
    card = await core.action(1, f"dlg:count:{token}:3")
    assert "1 генерац" in card.text and provider.calls == 0 and not store.jobs()
    core.clear(1)
    await core.message(1, "Смени фон на кофейню")
    assert core.current(1).preset == "background"
    card = await core.message(1, "", images=[photo(media)])
    assert "Создать" in str(card.rows) and not store.jobs()


async def test_questions_dont_spend_and_two_inputs_are_required(dialog):
    core, store, media, _, _ = dialog
    before = store.wallet(1)
    assert "генерац" in (await core.message(1, "Сколько стоит?")).text
    assert store.wallet(1) == before and not store.jobs()
    await core.message(1, "Объедини два фото в общий портрет")
    card = await core.message(1, "", images=[photo(media)])
    assert "втор" in card.text.lower() and not any("confirm" in data for row in card.rows for _, data in row)
    card = await core.message(1, "", images=[photo(media)])
    assert any("confirm" in data for row in card.rows for _, data in row)


async def test_confirm_is_idempotent_even_after_controller_restart(dialog):
    core, store, media, provider, service = dialog
    await core.message(1, "Улучши свет и чёткость", images=[photo(media)])
    token = core.current(1).token
    card = await core.action(1, "dlg:confirm:" + token)
    assert card.job and len(store.jobs()) == 1 and provider.calls == 0
    restarted = PhotoDialog(store, media, service)
    await restarted.action(1, "dlg:confirm:" + token)
    assert len(store.jobs()) == 1
    assert await service.process(card.job)
    await restarted.action(1, "dlg:confirm:" + token)
    assert len(store.jobs()) == 1 and provider.calls == 1


async def test_consent_foreign_photo_expiry_and_stale_confirm(dialog):
    core, store, media, _, _ = dialog
    with pytest.raises(ValueError):
        await core.message(2, "Смени фон", images=[photo(media)])
    store.consent(2)
    with pytest.raises(ValueError):
        await core.message(2, "Смени фон", images=[photo(media)])
    await core.message(1, "Смени фон на парк", images=[photo(media)])
    old = core.current(1).token
    await core.message(1, "Смени фон на студию")
    with pytest.raises(ValueError):
        await core.action(1, "dlg:confirm:" + old)
    assert not store.jobs()


async def test_spoken_variant_counts_preserve_description_without_charging(dialog):
    core, store, media, _, _ = dialog
    await core.message(1, 'Примерь круглые очки', images=[photo(media)])
    for words, number in [('один вариант', 1), ('три варианта', 3), ('давай шесть', 6),
                          ('девять вариантов', 9), ('покажи двенадцать вариантов', 12)]:
        card = await core.message(1, words)
        assert core.current(1).count == number
        assert core.current(1).description == 'Примерь круглые очки'
        assert 'Расход: 1' in card.text
    assert not store.jobs()


async def test_short_category_request_asks_for_details_instead_of_confirming(dialog):
    core, store, media, _, _ = dialog
    await core.message(1, '', images=[photo(media)])
    card = await core.message(1, 'А теперь фон')
    assert 'Какой фон' in card.text
    assert not any(data.startswith('dlg:confirm:') for row in card.rows for _, data in row)
    assert not store.jobs()


async def test_combined_edit_and_source_choice_are_explicit(dialog):
    core, store, media, _, service = dialog
    original = photo(media)
    service.remember_inputs(1, [original])
    job = service.submit(1, "earlier", "background", [original], "фон кофейня")
    await service.process(job)
    card = await core.message(1, "Поменяй причёску и добавь очки")
    assert core.current(1).preset == "look" and "результат" in str(card.rows).lower()
    assert len(core.current(1).sources) == 2
    assert len(store.jobs()) == 1


async def test_bounded_llm_json_meter_and_duplicate_message(dialog):
    core, store, _, _, _ = dialog
    reply = SimpleNamespace(
        output_text=json.dumps({"intent": "edit", "preset": "look", "answer": "Подготовим портрет"}),
        usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 100, "output_tokens": 25}),
    )
    create = AsyncMock(return_value=reply)
    core.client = SimpleNamespace(
        with_options=lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create))
    )
    await core.message(1, "Хочу журнальный портрет")
    assert core.current(1).preset == "look" and create.await_count == 1
    kwargs = create.await_args.kwargs
    assert kwargs["store"] is False and kwargs["max_output_tokens"] == 400 and "tools" not in kwargs
    assert core.seen(1, "same") is False and core.seen(1, "same") is True
    with store.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM photo_dialog_events WHERE kind='llm'").fetchone()[0] == 1
    assert not store.jobs()


async def test_llm_quota_invalid_json_and_failure_keep_buttons_without_charge(dialog):
    core, store, _, _, _ = dialog
    create = AsyncMock(side_effect=TimeoutError("offline timeout"))
    core.client = SimpleNamespace(
        with_options=lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create))
    )
    card = await core.message(1, "Журнальный портрет")
    assert card.rows and not store.jobs()
    with store.tx() as c:
        c.executemany(
            "INSERT INTO photo_dialog_events VALUES(?,1,?,'llm',NULL)",
            [(f"quota-{n}", __import__("time").time()) for n in range(29)],
        )
    await core.message(1, "Иной портрет")
    assert create.await_count == 1 and not store.jobs()
    with store.tx() as c:
        assert (
            c.execute("SELECT usage FROM photo_dialog_events WHERE usage IS NOT NULL")
            .fetchone()[0]
            .find("failed")
            >= 0
        )
    core.clear(1)
    assert core.current(1) is None


async def test_expired_original_and_session_never_submit(dialog):
    import os
    import time

    core, store, media, _, _ = dialog
    path = photo(media)
    await core.message(1, "Смени фон на парк", images=[path])
    token = core.current(1).token
    old = time.time() - 86410
    os.utime(media.path(path), (old, old))
    with pytest.raises(ValueError):
        await core.action(1, "dlg:confirm:" + token)
    core.current(1).created -= 1801
    with pytest.raises(ValueError):
        await core.action(1, "dlg:confirm:" + token)
    assert not store.jobs()
