import io
import shutil
import time
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 24_000_000


def normalize(data: bytes) -> bytes:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("image_size_limit")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"} or source.width * source.height > MAX_PIXELS:
                raise ValueError("invalid_image")
            source.load()
            oriented = ImageOps.exif_transpose(source)
            oriented.thumbnail((2048, 2048))
            image = oriented.convert("RGB")
            result = io.BytesIO()
            image.save(result, "JPEG", quality=92, exif=b"")
            return result.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("invalid_image") from None


class Media:
    def __init__(self, data_dir: Path):
        self.root = (data_dir / "media").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("unsafe_media_path")
        return path

    def user_dir(self, user: int) -> Path:
        if not isinstance(user, int) or user < 0:
            raise ValueError("invalid_user")
        path = self.path(str(user))
        path.mkdir(exist_ok=True)
        return path

    def save(self, user: int, data: bytes, name=None) -> str:
        directory = self.user_dir(user)
        target = self.path(f"{user}/{name or uuid.uuid4().hex + '.jpg'}")
        if target.parent != directory:
            raise ValueError("unsafe_media_path")
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(data)
        temp.replace(target)
        return str(target.relative_to(self.root))

    def delete_user(self, user):
        target = self.path(str(user))
        # Resolve and verify the complete target immediately before recursive removal.
        if target.exists() and target.is_dir() and target.is_relative_to(self.root) and target != self.root:
            shutil.rmtree(target)

    def cleanup(self, protected_users=(), ttl=86400):
        now = time.time()
        protected = {str(user) for user in protected_users}
        count = 0
        for owner in self.root.iterdir():
            if owner.name in protected or not owner.is_dir() or owner.is_symlink():
                continue
            for file in owner.iterdir():
                path = self.path(str(file.relative_to(self.root)))
                if path.is_file() and now - path.stat().st_mtime > ttl:
                    path.unlink()
                    count += 1
        return count
