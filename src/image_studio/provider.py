import base64
import io
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from PIL import Image, ImageDraw


@dataclass
class ImageResult:
    data: bytes
    usage: dict
    request_id: str | None = None


class ProviderError(Exception):
    pass


class ImageProvider(Protocol):
    async def edit(self, images: list[Path], prompt: str) -> ImageResult: ...
    async def close(self): ...


class OpenAIProvider:
    def __init__(self, key: str, model: str, quality: str = "medium", client=None):
        self.model, self.quality = model, quality
        self.client = client or AsyncOpenAI(api_key=key, max_retries=0, timeout=180)

    async def edit(self, images, prompt):
        try:
            with ExitStack() as stack:
                files = [stack.enter_context(path.open("rb")) for path in images]
                response = await self.client.images.edit(
                    model=self.model,
                    image=files,
                    prompt=prompt,
                    n=1,
                    quality=self.quality,
                    size="1024x1024",
                    output_format="jpeg",
                )
            if not response.data or not response.data[0].b64_json:
                raise ProviderError("missing_output")
            data = base64.b64decode(response.data[0].b64_json, validate=True)
            usage = response.usage.model_dump() if response.usage else {}
            return ImageResult(data, usage, getattr(response, "_request_id", None))
        except APIConnectionError:
            raise ProviderError("provider_connection_or_timeout") from None
        except APIStatusError as exc:
            if exc.status_code == 401:
                code = "provider_authentication"
            elif exc.status_code == 429:
                code = "provider_quota" if exc.code == "insufficient_quota" else "provider_rate_limit"
            else:
                code = "provider_rejected" if exc.status_code in {400, 403, 422} else "provider_unavailable"
            raise ProviderError(code) from None
        except (ValueError, TypeError):
            raise ProviderError("invalid_provider_response") from None

    async def close(self):
        await self.client.close()


class MockProvider:
    calls = 0

    async def edit(self, images, prompt):
        self.calls += 1
        with Image.open(images[0]) as source:
            output = source.convert("RGB")
        draw = ImageDraw.Draw(output)
        draw.rectangle((0, 0, output.width, 55), fill="#172f23")
        draw.text((15, 12), "DEMO / NO AI EDIT / TEST CREDITS", fill="white")
        data = io.BytesIO()
        output.save(data, "JPEG")
        return ImageResult(data.getvalue(), {}, "offline-demo")

    async def close(self):
        pass
