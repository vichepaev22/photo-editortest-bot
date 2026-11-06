import base64
import io
import json

import httpx
import pytest
from openai import AsyncOpenAI
from PIL import Image

from image_studio.catalog import prompt_for
from image_studio.config import Settings
from image_studio.hairstyle import HAIR_RULES, requested_hair_style
from image_studio.media import Media
from image_studio.photo_dialog import PhotoDialog
from image_studio.provider import MockProvider, OpenAIProvider
from image_studio.service import Service
from image_studio.store import Store


@pytest.mark.parametrize('text,expected', [
    ('Мужская стрижка, длинные волосы', 'men'),
    ('Женская короткая стрижка', 'women'),
    ('Универсальные причёски', 'unisex'),
    ('Не женские, а мужские причёски', 'men'),
    ('Мужские или женские', None),
    ('Не женские причёски', None),
    ('Не хочу женские причёски', None),
    ('Каре до плеч', None),
])
def test_only_explicit_user_preference_is_selected(text, expected):
    assert requested_hair_style(text) == expected


def make_dialog(tmp_path):
    store, media = Store(tmp_path/'db.sqlite3'), Media(tmp_path)
    store.consent(1)
    store.grant_pilot(1, 10)
    provider = MockProvider()
    service = Service(store, media, provider)
    data = io.BytesIO()
    Image.new('RGB', (256, 256), 'white').save(data, 'JPEG')
    photo = media.save(1, data.getvalue())
    return PhotoDialog(store, media, service), store, media, provider, service, photo


async def test_style_buttons_preserve_wish_and_free_choice_then_submit_once(tmp_path):
    core, store, media, provider, service, photo = make_dialog(tmp_path)
    before = store.wallet(1)
    wish = 'Покажи причёски с длинными волосами'
    card = await core.message(1, wish, images=[photo])
    assert all(label in str(card.rows) for label in ['Мужские', 'Женские', 'Универсальные'])
    old = core.current(1).token
    await core.action(1, f'dlg:hair:{old}:men')
    assert core.current(1).description == wish
    assert store.wallet(1) == before and not store.jobs() and provider.calls == 0
    with pytest.raises(ValueError):
        await core.action(1, f'dlg:hair:{old}:women')
    await core.action(1, f'dlg:count:{core.current(1).token}:6')
    token = core.current(1).token
    preview = core.render(1, core.current(1))
    assert 'Причёски: мужские' in preview.text
    card = await core.action(1, 'dlg:confirm:'+token)
    payload = json.loads(media.path(f'1/{card.job}.json').read_text(encoding='utf-8'))
    assert 'Категория причёсок: мужские.' in payload['prompt']
    assert wish in payload['prompt'] and 'Ровно 6 вариантов' in payload['prompt']
    assert HAIR_RULES in payload['prompt']
    assert await service.process(card.job)
    await core.action(1, 'dlg:confirm:'+token)
    assert provider.calls == 1 and len(store.jobs()) == 1
    assert store.wallet(1)[0] == before[0]-1


async def test_explicit_style_skips_question_and_can_be_changed_without_losing_wish(tmp_path):
    core, store, _, _, _, photo = make_dialog(tmp_path)
    wish = 'Примерь мужские причёски с длинными волосами'
    card = await core.message(1, wish, images=[photo])
    assert 'Сколько вариантов' in card.text
    assert core.current(1).hair_style == 'men'
    await core.message(1, 'женские')
    assert core.current(1).hair_style == 'women'
    assert core.current(1).description == wish
    await core.action(1, 'dlg:pick:glasses')
    assert core.current(1).hair_style is None and not store.jobs()


def test_prompt_rules_cover_hair_and_combined_edit_without_changing_other_presets():
    assert HAIR_RULES in prompt_for('hair', 'Женская стрижка')
    assert HAIR_RULES in prompt_for('look', 'Мужские причёски и очки')
    assert HAIR_RULES not in prompt_for('glasses', 'Круглая оправа')


async def test_image26_request_uses_installed_sdk_medium_and_single_jpeg(tmp_path):
    captured = []

    async def transport(request):
        captured.append(await request.aread())
        return httpx.Response(200, json={'created': 1, 'data': [
            {'b64_json': base64.b64encode(b'synthetic-image').decode()}
        ]})

    source = tmp_path/'input.jpg'
    Image.new('RGB', (32, 32)).save(source)
    client = AsyncOpenAI(api_key='synthetic-no-network', max_retries=0,
                         http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    provider = OpenAIProvider('synthetic', Settings().image_model, client=client)
    try:
        result = await provider.edit([source], prompt_for('hair', 'Мужская стрижка'))
        assert result.data == b'synthetic-image' and len(captured) == 1
        body = captured[0]
        for field, value in [('model', 'gpt-image-2.6-sunburst'), ('quality', 'medium'),
                             ('n', '1'), ('size', '1024x1024'), ('output_format', 'jpeg')]:
            assert f'name="{field}"\r\n\r\n{value}'.encode() in body
        assert b'name="complexity"' not in body
    finally:
        await provider.close()
