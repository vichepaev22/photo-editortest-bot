import io
import json
import os
import time
from pathlib import Path

import pytest
from PIL import Image

from image_studio.media import MAX_BYTES, Media, normalize
from image_studio.provider import MockProvider
from image_studio.service import Service
from image_studio.store import DomainError, Store


def image(format="JPEG"):
    result = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(result, format)
    return result.getvalue()


@pytest.fixture
def workflow(tmp_path):
    store, media = Store(tmp_path / "db.sqlite3"), Media(tmp_path)
    store.consent(1)
    store.grant_demo(1)
    provider = MockProvider()
    provider.calls = 0
    service = Service(store, media, provider)
    return store, media, provider, service


def reopen(workflow):
    store, media, provider, _ = workflow
    return Service(Store(store.path), Media(media.root.parent), provider)


def expire(path):
    timestamp = time.time() - 86401
    os.utime(path, (timestamp, timestamp))


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP"])
def test_remember_private_originals_survive_restart_without_spending(workflow, format):
    store, media, provider, service = workflow
    original = media.save(1, image(format), "original." + format.lower())
    service.remember_inputs(1, [original])
    assert service.reusable_inputs(1, 1) == [original]
    assert reopen(workflow).reusable_inputs(1, 1) == [original]
    assert service.reusable_inputs(1, 2) == []
    assert store.wallet(1) == (3, 0) and store.jobs(1) == [] and provider.calls == 0
    records = list(media.user_dir(1).glob("*.json"))
    assert len(records) == 1
    metadata = json.loads(records[0].read_text())
    assert metadata == {"inputs": [original]}
    assert not Path(metadata["inputs"][0]).is_absolute()


def test_merge_remembers_exact_group_not_one_person_or_unrelated_old_sources(workflow):
    _, media, _, service = workflow
    first, second = media.save(1, image()), media.save(1, image())
    service.remember_inputs(1, [first])
    assert service.reusable_inputs(1, 1) == [first]
    service.remember_inputs(1, [first, second])
    assert service.reusable_inputs(1, 2) == [first, second]
    assert service.reusable_inputs(1, 1) == []
    assert reopen(workflow).reusable_inputs(1, 2) == [first, second]


def test_submit_rechecks_expired_reused_photo_before_reserving_trial(workflow):
    store, media, provider, _ = workflow
    service = Service(store, media, provider, trial_access=True)
    store.grant_trial(1)
    original = media.save(1, image())
    timestamp = time.time() - 86340
    os.utime(media.path(original), (timestamp, timestamp))
    service.remember_inputs(1, [original])
    selected = service.reusable_inputs(1, 1)
    assert selected == [original]
    before = store.wallet(1, trial=True)

    # Selection succeeded, but the user describes the next edit after expiry.
    # The file can still exist while cleanup is delayed by other work.
    expire(media.path(original))
    with pytest.raises(DomainError, match="^invalid_inputs$"):
        service.submit(1, "expired-selection", "glasses", selected, "Круглая оправа")

    assert store.wallet(1, trial=True) == before
    assert store.wallet(1) == (3, 0) and store.jobs(1) == [] and provider.calls == 0


async def test_existing_job_payload_restores_original_and_never_output(workflow):
    store, media, provider, service = workflow
    original = media.save(1, image())
    job = service.submit(1, "old-hair-job", "hair", [original], "Short hair")
    assert await service.process(job)
    restarted = reopen(workflow)
    assert restarted.reusable_inputs(1, 1) == [original]
    output = store.result(1, job)["result"]
    assert output not in restarted.reusable_inputs(1, 1)
    assert provider.calls == 1 and store.wallet(1) == (2, 0)
    with pytest.raises(DomainError, match="^invalid_inputs$"):
        restarted.remember_inputs(1, [output])


def test_recent_upload_takes_precedence_over_existing_job_payload(workflow):
    _, media, _, service = workflow
    old, recent = media.save(1, image()), media.save(1, image())
    service.submit(1, "old", "hair", [old], "Short")
    service.remember_inputs(1, [recent])
    assert reopen(workflow).reusable_inputs(1, 1) == [recent]


def test_delete_and_revoke_remove_or_block_private_metadata_without_quota_change(workflow):
    store, media, _, service = workflow
    original = media.save(1, image())
    service.remember_inputs(1, [original])
    store.revoke_consent(1)
    assert service.reusable_inputs(1, 1) == []
    with pytest.raises(DomainError, match="^consent_required$"):
        service.remember_inputs(1, [original])
    store.consent(1)
    assert service.reusable_inputs(1, 1) == [original]
    service.delete(1)
    assert not media.path("1").exists()
    assert reopen(workflow).reusable_inputs(1, 1) == []
    store.consent(1)
    assert service.reusable_inputs(1, 1) == [] and store.wallet(1) == (3, 0)


@pytest.mark.parametrize("broken", ["missing", "expired", "foreign", "traversal", "absolute", "invalid"])
def test_remember_rejects_invalid_owned_image_references(workflow, broken):
    _, media, _, service = workflow
    original = media.save(1, image())
    candidate = original
    if broken == "missing":
        media.path(original).unlink()
    elif broken == "expired":
        expire(media.path(original))
    elif broken == "foreign":
        candidate = media.save(2, image())
    elif broken == "traversal":
        candidate = "1/../" + original.replace("\\", "/")
    elif broken == "absolute":
        candidate = str(media.path(original))
    else:
        media.path(original).write_bytes(b"not-an-image")
    with pytest.raises(DomainError, match="^invalid_inputs$"):
        service.remember_inputs(1, [candidate])
    assert service.reusable_inputs(1, 1) == []


@pytest.mark.parametrize("broken", ["missing", "expired", "foreign", "traversal", "invalid", "expired-record"])
def test_invalid_recent_metadata_or_original_never_falls_back_to_old_job(workflow, broken):
    _, media, _, service = workflow
    old, recent = media.save(1, image()), media.save(1, image())
    service.submit(1, "old", "hair", [old], "Short")
    service.remember_inputs(1, [recent])
    record = next(path for path in media.user_dir(1).glob("*.json") if path.stem not in {
        job["id"] for job in service.store.jobs(1)
    })
    if broken == "missing":
        media.path(recent).unlink()
    elif broken == "expired":
        expire(media.path(recent))
    elif broken == "foreign":
        record.write_text(json.dumps({"inputs": [media.save(2, image())]}))
    elif broken == "traversal":
        record.write_text(json.dumps({"inputs": ["../2/source.jpg"]}))
    elif broken == "invalid":
        record.write_text("not-json")
    else:
        expire(record)
    assert service.reusable_inputs(1, 1) == []
    assert reopen(workflow).reusable_inputs(1, 1) == []


@pytest.mark.parametrize("broken", ["missing", "expired-record", "expired-original", "foreign-input",
                                    "output-input", "invalid-record", "oversized-record"])
def test_restart_job_fallback_requires_fresh_bounded_owned_payload_and_original(workflow, broken):
    store, media, _, service = workflow
    original = media.save(1, image())
    job = service.submit(1, "old", "hair", [original], "Short")
    payload = media.path(f"1/{job}.json")
    if broken == "missing":
        payload.unlink()
    elif broken == "expired-record":
        expire(payload)
    elif broken == "expired-original":
        expire(media.path(original))
    elif broken == "foreign-input":
        payload.write_text(json.dumps({"inputs": [media.save(2, image())]}))
    elif broken == "output-input":
        output = media.save(1, image(), job + ".jpg")
        payload.write_text(json.dumps({"inputs": [output]}))
    elif broken == "invalid-record":
        payload.write_text(json.dumps({"inputs": "1/file.jpg"}))
    else:
        payload.write_bytes(b"x" * 16385)
    assert reopen(workflow).reusable_inputs(1, 1) == []
    assert store.wallet(1) == (3, 1)


def test_foreign_job_payload_cannot_be_used_even_if_copy_points_to_own_file(workflow):
    store, media, _, service = workflow
    own = media.save(1, image())
    store.consent(2)
    store.grant_demo(2)
    other = media.save(2, image())
    job = service.submit(2, "foreign", "hair", [other], "Short")
    media.save(1, json.dumps({"inputs": [own]}).encode(), job + ".json")
    assert reopen(workflow).reusable_inputs(1, 1) == []


@pytest.mark.parametrize("count", [0, 3, True, "1", 1.0, None])
def test_invalid_count_and_duplicate_merge_never_reuse(workflow, count):
    _, media, _, service = workflow
    original = media.save(1, image())
    service.remember_inputs(1, [original])
    assert service.reusable_inputs(1, count) == []
    with pytest.raises(DomainError, match="^invalid_inputs$"):
        service.remember_inputs(1, [original, original])


def test_decimal_ten_mb_boundary_accepts_actual_image_and_rejects_next_byte():
    source = image()
    exact = source + b"\0" * (10_000_000 - len(source))
    assert normalize(exact).startswith(b"\xff\xd8")
    with pytest.raises(ValueError, match="^image_size_limit$"):
        normalize(exact + b"\0")
    assert MAX_BYTES == 10_000_000
