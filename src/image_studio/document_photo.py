import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from .media import MAX_BYTES, MAX_PIXELS

DOCUMENT_PRESETS = frozenset({"document", "document_original"})
PHOTO_SIZE = (413, 531)
SHEET_SIZE = (PHOTO_SIZE[0] * 2, PHOTO_SIZE[1] * 2)
PRINT_DPI = 300


def prepare_document_sheet(data: bytes) -> bytes:
    """Crop one portrait and repeat it identically on a lossless 35x45 mm 2x2 sheet."""
    if not data or len(data) > MAX_BYTES:
        raise ValueError("image_size_limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"} or source.width * source.height > MAX_PIXELS:
                    raise ValueError("invalid_image")
                source.load()
                oriented = ImageOps.exif_transpose(source)
                portrait = ImageOps.fit(oriented.convert("RGB"), PHOTO_SIZE, method=Image.Resampling.LANCZOS)
        # A fresh canvas drops source EXIF, comments and color-profile metadata.
        sheet = Image.new("RGB", SHEET_SIZE)
        for x, y in ((0, 0), (PHOTO_SIZE[0], 0), (0, PHOTO_SIZE[1]), PHOTO_SIZE):
            sheet.paste(portrait, (x, y))
        output = io.BytesIO()
        sheet.save(output, "PNG", dpi=(PRINT_DPI, PRINT_DPI))
        return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("invalid_image") from None
