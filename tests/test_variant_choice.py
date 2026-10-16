import json

import pytest
from test_bot_flow import build_ui, button_data, feed, texts


@pytest.mark.parametrize("preset,count", [("hair", 1), ("hair", 9), ("glasses", 3), ("glasses", 6), ("glasses", 12)])
async def test_count_choice_waits_then_submits_one_result(tmp_path, preset, count):
    ui = build_ui(tmp_path)
    await feed(ui, callback="preset:" + preset)
    prompt = await feed(ui, photo=True)
    assert "Скопировать пример" in str(prompt) and "вставьте" in texts(prompt)
    before = ui[4].wallet(1)
    sent = await feed(ui, "Натуральный образ", message_id=200, variant_count=None)
    assert "Сколько вариантов" in texts(sent)
    assert not ui[2].jobs(1) and ui[4].wallet(1) == before
    options = [b.callback_data for m in sent
               for row in getattr(getattr(m, "reply_markup", None), "inline_keyboard", [])
               for b in row if b.callback_data and b.callback_data.startswith("variants:")]
    assert {b.rsplit(":", 1)[1] for b in options} == {"1", "3", "6", "9", "12"}
    assert all(len(b.encode()) <= 64 for b in options)
    selected = next(b for b in options if b.endswith(":" + str(count)))
    await feed(ui, callback=selected, user=2)
    assert not ui[2].jobs(1)
    await feed(ui, callback=selected)
    record = ui[2].jobs(1)[0]
    payload = json.loads(ui[3].path("1/" + record["id"] + ".json").read_text())
    assert f"ровно {count} вариантов" in payload["prompt"]
    assert ("причёску" if preset == "hair" else "очки") in payload["prompt"]
    assert record["cost"] == 1 and record["request_key"].endswith(":200")
    await feed(ui, callback=selected)
    assert len(ui[2].jobs(1)) == 1


async def test_new_description_and_photo_expire_old_count_buttons(tmp_path):
    ui = build_ui(tmp_path)
    await feed(ui, callback="preset:glasses")
    await feed(ui, photo=True)
    first = button_data(await feed(ui, "Круглая оправа", variant_count=None), "variants:")
    await feed(ui, "Прямоугольная оправа", variant_count=None)
    await feed(ui, callback=first)
    assert not ui[2].jobs(1)
    second = button_data(await feed(ui, "Тонкая оправа", variant_count=None), "variants:")
    await feed(ui, photo=True)
    await feed(ui, callback=second)
    assert not ui[2].jobs(1)
