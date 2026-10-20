import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Dispatcher, F, Router
from PIL import Image
from test_admin import OfflineBot, send, text_methods

from image_studio.media import Media
from image_studio.owner_quality import OwnerQuality, register_owner_quality
from image_studio.provider import MockProvider, OpenAIProvider
from image_studio.service import Service
from image_studio.store import DomainError, Store


@pytest.fixture
def trial(tmp_path):
    store, media = Store(tmp_path / "quality.db"), Media(tmp_path)
    store.consent(1)
    store.grant_pilot(1, 3)
    photo = io.BytesIO()
    Image.new("RGB", (256, 256), "white").save(photo, "JPEG")
    return store, media, MockProvider(), [media.save(1, photo.getvalue())]


async def test_owner_quality_is_frozen_per_job_and_survives_restart(trial):
    store,media,_,photos=trial

    class Spy(MockProvider):
        async def edit(self,images,prompt,*,quality=None):
            self.selected=quality
            return await super().edit(images,prompt,quality=quality)

    provider=Spy()
    service=Service(store,media,provider,trial_access=True,unlimited_user_id=1)
    service.owner_quality.select(1,"high")
    job=service.submit(1,"same","hair",photos[:1],"каре")
    service.owner_quality.select(1,"max")
    assert service.submit(1,"same","hair",photos[:1],"каре")==job
    payload=json.loads(media.path(f"1/{job}.json").read_text())
    assert payload["owner_quality"]=="high"
    restarted=Service(Store(store.path),media,provider,trial_access=True,unlimited_user_id=1)
    assert restarted.owner_quality.current(1)=="max"
    assert await restarted.process(job)
    assert provider.selected=="high" and store.job(job)["quota_exempt"]==1
    with pytest.raises(DomainError):
        restarted.owner_quality.select(2,"max")
    assert restarted.owner_quality.current(2) is None
    assert provider.calls==1


async def test_regular_job_ignores_forged_quality_preference(trial):
    store,media,provider,photos=trial
    service=Service(store,media,provider,trial_access=True,unlimited_user_id=2)
    with store.tx() as c:
        c.execute("INSERT INTO owner_image_quality VALUES(1,'max')")
    job=service.submit(1,"regular","hair",photos[:1],"каре")
    assert "owner_quality" not in json.loads(media.path(f"1/{job}.json").read_text())
    assert await service.process(job) and provider.calls==1


async def test_private_quality_controls_deny_others_and_allow_repeat(trial):
    store,media,provider,_=trial
    service=Service(store,media,provider,trial_access=True,unlimited_user_id=1)
    router=Router()
    router.message.filter(F.chat.type=="private")
    router.callback_query.filter(F.message.chat.type=="private")
    register_owner_quality(router,service)
    dp=Dispatcher()
    dp.include_router(router)
    ui=(dp,OfflineBot(),store)
    await send(ui,"/quality",user_id=1)
    assert "только для владельца" in text_methods(ui[1].sent)[0].text
    await send(ui,callback="owner_quality:max",user_id=2)
    assert service.owner_quality.current(1) is None
    await send(ui,callback="owner_quality:high",user_id=1,chat_type="group")
    assert service.owner_quality.current(1) is None
    await send(ui,callback="owner_quality:high",user_id=1)
    assert service.owner_quality.current(1)=="high"
    ui[1].unchanged=True
    await send(ui,callback="owner_quality:high",user_id=1)
    assert service.owner_quality.current(1)=="high" and provider.calls==0


def test_invalid_quality_cannot_be_saved(trial):
    store,_,_,_=trial
    preference=OwnerQuality(store,1)
    with pytest.raises(DomainError):
        preference.select(1,"ultra")


async def test_openai_receives_explicit_quality_without_changing_default(trial):
    _, media, _, photos = trial
    response = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(media.path(photos[0]).read_bytes()).decode())], usage=None)
    edit = AsyncMock(return_value=response)
    provider = OpenAIProvider("offline", "gpt-image-2.5-sunburst-2026-09-08",
                              client=SimpleNamespace(images=SimpleNamespace(edit=edit)))
    await provider.edit([media.path(photos[0])], "test", quality="max")
    assert edit.await_args.kwargs["quality"] == "max"
    assert edit.await_args.kwargs["model"] == "gpt-image-2.5-sunburst-2026-09-08"
    assert edit.await_args.kwargs["size"] == "1024x1024"
    await provider.edit([media.path(photos[0])], "test")
    assert edit.await_args.kwargs["quality"] == "medium"
