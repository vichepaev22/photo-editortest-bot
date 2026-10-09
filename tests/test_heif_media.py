import io
from unittest.mock import MagicMock

import pytest
from PIL import Image, ImageCms, ImageDraw

import image_studio.media as media


@pytest.fixture(scope="module")
def synthetic_heic():
    """A non-personal HEIC with HEIF rotation, ICC and private metadata, encoded in memory."""
    with Image.new("RGB", (3000, 2000), "blue") as source:
        ImageDraw.Draw(source).rectangle((0, 0, 1499, 1999), fill="red")
        exif = source.getexif()
        exif[0x0112] = 6
        exif[0x010E] = "synthetic private metadata"
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        data = io.BytesIO()
        source.save(
            data,
            "HEIF",
            quality=90,
            exif=exif.tobytes(),
            xmp=b"<private>synthetic metadata</private>",
            icc_profile=profile,
        )
    return data.getvalue()


def test_heic_decodes_rotated_pixels_to_bounded_jpeg_without_metadata(synthetic_heic):
    assert synthetic_heic[4:12] == b"ftypheic"
    assert len(synthetic_heic) < media.MAX_BYTES
    with Image.open(io.BytesIO(synthetic_heic)) as source:
        assert source.format == "HEIF" and source.size == (2000, 3000)
        assert source.info["original_orientation"] == 6
        assert source.info["exif"] and source.info["xmp"] and source.info["icc_profile"]
    output = media.normalize(synthetic_heic)
    with Image.open(io.BytesIO(output)) as result:
        result.load()
        assert result.format == "JPEG" and result.mode == "RGB"
        assert result.size == (1365, 2048)
        assert not result.getexif()
        assert not ({"exif", "xmp", "icc_profile", "comment"} & result.info.keys())
        top = result.getpixel((result.width // 2, 100))
        bottom = result.getpixel((result.width // 2, result.height - 100))
        assert top[0] > 240 and top[1] < 15 and top[2] < 15
        assert bottom[2] > 240 and bottom[0] < 15 and bottom[1] < 15


def test_heif_uses_primary_image_in_multi_image_container():
    data = io.BytesIO()
    with Image.new("RGB", (16, 16), "red") as first, Image.new("RGB", (32, 16), "blue") as primary:
        first.save(data, "HEIF", save_all=True, append_images=[primary], primary_index=1, quality=90)
    with Image.open(io.BytesIO(media.normalize(data.getvalue()))) as result:
        result.load()
        assert result.size == (32, 16)
        assert result.getpixel((16, 8))[2] > 240


def test_truncated_heic_is_rejected_with_safe_error(synthetic_heic):
    with pytest.raises(ValueError, match="^invalid_image$"):
        media.normalize(synthetic_heic[:64])


def test_heic_byte_limit_is_checked_before_open(synthetic_heic, monkeypatch):
    monkeypatch.setattr(media, "MAX_BYTES", len(synthetic_heic) - 1)
    opener = MagicMock()
    monkeypatch.setattr(media.Image, "open", opener)
    with pytest.raises(ValueError, match="^image_size_limit$"):
        media.normalize(synthetic_heic)
    opener.assert_not_called()


def test_heic_pixel_limit_is_checked_before_decode(synthetic_heic, monkeypatch):
    monkeypatch.setattr(media, "MAX_PIXELS", 6_000_000 - 1)
    loader = MagicMock(side_effect=AssertionError("full HEIC must not be decoded"))
    monkeypatch.setattr("pillow_heif.as_plugin.HeifImageFile.load", loader)
    with pytest.raises(ValueError, match="^image_resolution_limit$"):
        media.normalize(synthetic_heic)
    loader.assert_not_called()


@pytest.mark.parametrize("error", [ValueError, RuntimeError, SyntaxError, EOFError])
def test_heif_decode_error_does_not_leak_native_exception(error, monkeypatch):
    source = MagicMock()
    source.__enter__.return_value = source
    source.format, source.width, source.height = "HEIF", 16, 16
    source.load.side_effect = error("synthetic native decoder detail")
    monkeypatch.setattr(media.Image, "open", lambda _: source)
    with pytest.raises(ValueError, match="^invalid_image$"):
        media.normalize(b"image header")


def test_existing_jpeg_exif_rotation_is_preserved():
    data = io.BytesIO()
    with Image.new("RGB", (120, 80), "red") as source:
        exif = source.getexif()
        exif[0x0112] = 6
        source.save(data, "JPEG", exif=exif)
    with Image.open(io.BytesIO(media.normalize(data.getvalue()))) as result:
        result.load()
        assert result.size == (80, 120) and not result.getexif()
