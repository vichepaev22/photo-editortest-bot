import io
import json

import pytest
from PIL import Image

from image_studio.catalog import PRESETS
from image_studio.media import Media
from image_studio.provider import MockProvider
from image_studio.service import Service
from image_studio.store import Store


async def test_reference_job_is_one_use_and_replay_does_not_analyze_twice(tmp_path):
    store, media, provider = Store(tmp_path / "state.db"), Media(tmp_path), MockProvider()
    store.consent(1)
    store.grant_demo(1)
    service = Service(store, media, provider)
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), "white").save(buffer, "JPEG")
    inputs = [media.save(1, buffer.getvalue()) for _ in range(2)]
    before = store.wallet(1)
    job = service.submit(1, "reference-one", "reference", inputs, "Сохранить мою одежду")
    assert service.submit(1, "reference-one", "reference", inputs, "Сохранить мою одежду") == job
    assert await service.process(job)
    assert not await service.process(job)
    payload = json.loads(media.path(f"1/{job}.json").read_text())
    assert payload["reference_recipe"]["schema_version"] == "reference-v1"
    assert "Preserve the user's original clothing" in payload["prompt"]
    assert provider.analysis_calls == 1 and provider.calls == 1
    assert store.wallet(1) == (before[0] - 1, before[1])
    assert PRESETS["reference"].credits == 1


async def test_analysis_failure_never_calls_image_and_restores_use(tmp_path):
    from image_studio.provider import ProviderError

    class FailedAnalysis(MockProvider):
        async def analyze_reference(self, image):
            raise ProviderError("reference_analysis_failed")

    store, media, provider = Store(tmp_path / "state.db"), Media(tmp_path), FailedAnalysis()
    store.consent(1)
    store.grant_demo(1)
    service = Service(store, media, provider)
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), "white").save(buffer, "JPEG")
    inputs = [media.save(1, buffer.getvalue()) for _ in range(2)]
    before = store.wallet(1)
    job = service.submit(1, "reference-failure", "reference", inputs, "Повторить образ")
    assert not await service.process(job)
    assert provider.calls == 0
    assert store.wallet(1) == before


def test_recipe_cannot_contain_identity_or_unbounded_fields():
    from image_studio.reference import Recipe, recipe_data

    fields = dict(pose="standing", framing="portrait", lighting="soft", background="studio",
                  palette="neutral", wardrobe="jacket", mood="calm")
    assert recipe_data(Recipe(**fields))["schema_version"] == "reference-v1"
    with pytest.raises(ValueError):
        Recipe(**fields, identity="person from reference")
    with pytest.raises(ValueError):
        recipe_data(Recipe(**(fields | {"pose": "x" * 1001})))
