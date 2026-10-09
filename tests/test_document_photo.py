import io
import json

import pytest
from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

import image_studio.document_photo as document_photo
from image_studio.catalog import PRESETS, prompt_for
from image_studio.document_photo import PHOTO_SIZE, prepare_document_sheet
from image_studio.media import Media
from image_studio.provider import ImageResult
from image_studio.service import Service
from image_studio.store import Store


def synthetic_photo(format="PNG", size=(140, 180)):
    source = Image.new("RGB", size)
    source.putdata([
        (x * 255 // size[0], y * 255 // size[1], (x + y) % 256)
        for y in range(size[1]) for x in range(size[0])
    ])
    exif = source.getexif()
    exif[0x010E] = "synthetic private metadata"
    options = {"exif": exif}
    if format == "PNG":
        metadata = PngInfo()
        metadata.add_text("Comment", "synthetic private comment")
        options["pnginfo"] = metadata
    data = io.BytesIO()
    source.save(data, format, **options)
    return data.getvalue()


def assert_sheet(data):
    with Image.open(io.BytesIO(data)) as sheet:
        assert sheet.format == "PNG" and sheet.mode == "RGB"
        assert sheet.size == (826, 1062)
        assert sheet.info["dpi"] == pytest.approx((300, 300), abs=0.01)
        assert not sheet.getexif()
        assert set(sheet.info) == {"dpi"}
        width, height = PHOTO_SIZE
        first = sheet.crop((0, 0, width, height)).tobytes()
        for x, y in ((width, 0), (0, height), (width, height)):
            assert sheet.crop((x, y, x + width, y + height)).tobytes() == first


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP"])
def test_sheet_decodes_to_four_identical_tiles_at_print_dpi_without_source_metadata(format):
    data = synthetic_photo(format)
    output = prepare_document_sheet(data)
    assert_sheet(output)
    assert output == prepare_document_sheet(data)


def test_exif_orientation_is_applied_before_cropping():
    source = Image.new("RGB", (90, 70), "blue")
    ImageDraw.Draw(source).rectangle((0, 0, 44, 69), fill="red")
    exif = source.getexif()
    exif[0x0112] = 6
    exif[0x010E] = "synthetic private metadata"
    data = io.BytesIO()
    source.save(data, "PNG", exif=exif)
    output = prepare_document_sheet(data.getvalue())
    assert_sheet(output)
    with Image.open(io.BytesIO(output)) as sheet:
        assert sheet.getpixel((206, 20)) == (255, 0, 0)
        assert sheet.getpixel((206, 510)) == (0, 0, 255)


def test_center_crop_preserves_geometry_and_original_background():
    source = Image.new("RGB", (210, 90), (40, 90, 140))
    ImageDraw.Draw(source).ellipse((90, 30, 120, 60), fill="white")
    data = io.BytesIO()
    source.save(data, "PNG")
    with Image.open(io.BytesIO(prepare_document_sheet(data.getvalue()))) as sheet:
        assert sheet.getpixel((10, 10)) == (40, 90, 140)
        portrait = sheet.crop((0, 0, *PHOTO_SIZE))
        foreground = portrait.convert("L").point(lambda value: 255 if value > 200 else 0)
        left, top, right, bottom = foreground.getbbox()
        assert abs((right - left) - (bottom - top)) <= 2
        assert (left + right) / 2 == pytest.approx(PHOTO_SIZE[0] / 2, abs=5)


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\n"])
def test_invalid_or_truncated_images_are_rejected(data):
    with pytest.raises(ValueError, match="^(invalid_image|image_size_limit)$"):
        prepare_document_sheet(data)


@pytest.mark.parametrize("format", ["GIF", "BMP"])
def test_unsupported_image_formats_are_rejected(format):
    data = io.BytesIO()
    Image.new("RGB", (8, 8)).save(data, format)
    with pytest.raises(ValueError, match="^invalid_image$"):
        prepare_document_sheet(data.getvalue())


def test_existing_size_and_pixel_limits_are_enforced_before_rendering(monkeypatch):
    data = synthetic_photo()
    monkeypatch.setattr(document_photo, "MAX_BYTES", len(data) - 1)
    with pytest.raises(ValueError, match="^image_size_limit$"):
        prepare_document_sheet(data)
    monkeypatch.setattr(document_photo, "MAX_BYTES", len(data))
    monkeypatch.setattr(document_photo, "MAX_PIXELS", 140 * 180 - 1)
    with pytest.raises(ValueError, match="^invalid_image$"):
        prepare_document_sheet(data)


class SyntheticProvider:
    def __init__(self, data):
        self.data = data
        self.calls = 0
        self.prompts = []

    async def edit(self, images, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        assert len(images) == 1
        return ImageResult(self.data, {"output_tokens": 7}, "synthetic-request")


@pytest.fixture
def workflow(tmp_path):
    store, media = Store(tmp_path / "db.sqlite3"), Media(tmp_path)
    store.consent(1)
    store.grant_pilot(1, 3)
    data = synthetic_photo()
    photo = media.save(1, data, "source.png")
    return store, media, SyntheticProvider(data), photo


@pytest.mark.parametrize("preset, expected_calls", [("document_original", 0), ("document", 1)])
@pytest.mark.parametrize("trial", [False, True])
async def test_single_sheet_uses_one_credit_and_replay_or_failed_delivery_cannot_regenerate(
    workflow, preset, expected_calls, trial
):
    store, media, provider, photo = workflow
    original = media.path(photo).read_bytes()

    async def failed_delivery(*args):
        raise OSError("synthetic delivery failure")

    service = Service(store, media, provider, failed_delivery, trial_access=trial)
    assert service.wallet(1) == (1 if trial else 3, 0)
    job = service.submit(1, "one-sheet", preset, [photo], "Сохранить одежду")
    assert service.wallet(1) == (1 if trial else 3, 1)
    assert store.job(job)["cost"] == 1
    assert await service.process(job)
    result = store.result(1, job)
    assert result["status"] == "generated"
    assert result["result"].endswith(f"/{job}.png") or result["result"].endswith(f"\\{job}.png")
    assert_sheet(media.path(result["result"]).read_bytes())
    assert service.submit(1, "one-sheet", preset, [photo], "Сохранить одежду") == job
    assert not await service.process(job)
    store.delivered(job)
    store.delivered(job)
    assert not await service.process(job)
    assert provider.calls == expected_calls
    assert service.wallet(1) == (0 if trial else 2, 0)
    assert store.wallet(1) == ((3, 0) if trial else (2, 0))
    assert media.path(photo).read_bytes() == original
    assert json.loads(result["usage"]) == ({} if preset == "document_original" else {"output_tokens": 7})
    assert result["request_id"] == (None if preset == "document_original" else "synthetic-request")


@pytest.mark.parametrize("preset, expected_calls", [("document_original", 0), ("document", 1)])
@pytest.mark.parametrize("trial", [False, True])
async def test_render_failure_releases_reservation_once(workflow, preset, expected_calls, trial):
    store, media, provider, photo = workflow
    if preset == "document_original":
        media.path(photo).write_bytes(b"invalid source")
    else:
        provider.data = b"invalid provider output"
    notified = []

    async def notify(user, job):
        notified.append((user, job))

    service = Service(store, media, provider, notify=notify, trial_access=trial)
    job = service.submit(1, "failed-sheet", preset, [photo], "Сохранить одежду")
    assert service.wallet(1) == (1 if trial else 3, 1)
    assert not await service.process(job)
    assert store.job(job)["status"] == "failed"
    assert store.job(job)["error"] == "processing_error"
    assert service.wallet(1) == (1 if trial else 3, 0)
    assert not await service.process(job)
    assert provider.calls == expected_calls
    assert notified == [(1, job)]
    assert not media.path(f"1/{job}.png").exists()


def test_document_modes_are_explicit_and_prompt_requests_one_portrait_without_identity_retouch():
    assert PRESETS["document_original"].label == "Фото на документы · исходник"
    assert PRESETS["document"].label == "Фото на документы"
    assert all(PRESETS[preset].inputs == PRESETS[preset].credits == 1
               for preset in ("document", "document_original"))
    prompt = prompt_for("document", "Сохранить одежду")
    for constraint in ("exactly one", "white background", "7:9", "Do not beautify", "Do not infer gender"):
        assert constraint in prompt
    assert "unless the user explicitly requests" in prompt
    assert "Сохранить одежду" in prompt
