from test_bot_flow import build_ui, feed


async def test_reference_choices_reserve_only_after_selection_and_keep_person(tmp_path):
    ui = build_ui(tmp_path)
    _, bot, store, _, service, provider = ui
    try:
        await feed(ui, callback="preset:reference")
        await feed(ui, photo=True)
        original = service.reusable_inputs(1, 1)
        await feed(ui, photo=True)
        assert service.reusable_inputs(1, 1) == original
        assert not store.jobs(1) and provider.analysis_calls == 0
        keyboard = next(method.reply_markup for method in reversed(bot.sent)
                        if getattr(method, "reply_markup", None)
                        and hasattr(method.reply_markup, "inline_keyboard")
                        and method.reply_markup.inline_keyboard[0][0].callback_data.startswith("refopt:"))
        await feed(ui, callback=keyboard.inline_keyboard[1][0].callback_data)
        assert len(store.jobs(1)) == 1
        await feed(ui, callback=keyboard.inline_keyboard[1][0].callback_data)
        assert len(store.jobs(1)) == 1
        assert provider.calls == 0
    finally:
        await bot.session.close()
