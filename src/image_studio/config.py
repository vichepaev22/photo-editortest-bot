import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

MODELS = {
    "gpt-image-2.5-flare",
    "gpt-image-2.5-flare-2026-09-08",
    "gpt-image-2.5-sunburst",
    "gpt-image-2.5-sunburst-2026-09-08",
}


@dataclass
class Settings:
    data_dir: Path = Path("data")
    image_provider: str = "mock"
    image_model: str = "gpt-image-2.5-flare-2026-09-08"
    quality: str = "medium"
    bot_token: str = ""
    expected_bot_username: str = ""
    openai_key: str = ""
    demo_credits: bool = False
    trial_access: bool = False
    support_contact: str = ""
    billing_enabled: bool = False
    shop_id: str = ""
    shop_key: str = ""
    return_url: str = "https://example.com/payment-result"
    admin_token: str = ""
    admin_user_id: int = 0
    vat_code: int = 1
    studio_enabled: bool = True
    studio_port: int = 8089
    mini_app_url: str = ""

    def validate(self):
        if type(self.admin_user_id) is not int or not 0 <= self.admin_user_id < 2**52:
            raise ValueError("invalid_admin_user_id")
        if self.image_provider not in {"mock", "openai"}:
            raise ValueError("invalid_provider")
        if self.image_model not in MODELS or self.quality not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("invalid_image_parameters")
        if self.image_provider == "openai" and not self.openai_key:
            raise ValueError("missing_openai_key")
        if self.trial_access and self.image_provider != "openai":
            raise ValueError("trial_requires_openai")
        if self.trial_access and self.billing_enabled:
            raise ValueError("trial_requires_billing_disabled")
        if self.billing_enabled and (not self.shop_id or not self.shop_key or len(self.admin_token) < 32):
            raise ValueError("billing_credentials_required")
        if self.billing_enabled and not self.return_url.startswith("https://"):
            raise ValueError("https_return_url_required")
        if not 1 <= self.vat_code <= 12:
            raise ValueError("invalid_vat_code")
        if not 1024 <= self.studio_port <= 65535:
            raise ValueError("invalid_studio_port")
        if self.mini_app_url:
            url = urlsplit(self.mini_app_url)
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
                raise ValueError("https_mini_app_url_required")
        return self

    @classmethod
    def from_env(cls):
        load_dotenv()
        return cls(
            data_dir=Path(os.getenv("DATA_DIR", "data")),
            image_provider=os.getenv("IMAGE_PROVIDER", "mock"),
            image_model=os.getenv("IMAGE_MODEL", "gpt-image-2.5-flare-2026-09-08"),
            quality=os.getenv("IMAGE_QUALITY", "medium"),
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            expected_bot_username=os.getenv("TELEGRAM_BOT_USERNAME", "").lstrip("@"),
            openai_key=os.getenv("OPENAI_API_KEY", ""),
            demo_credits=os.getenv("ENABLE_DEMO_CREDITS", "false").lower() == "true",
            trial_access=os.getenv("ENABLE_TRIAL_ACCESS", "false").lower() == "true",
            support_contact=os.getenv("SUPPORT_CONTACT", ""),
            billing_enabled=os.getenv("BILLING_ENABLED", "false").lower() == "true",
            shop_id=os.getenv("YOOKASSA_SHOP_ID", ""),
            shop_key=os.getenv("YOOKASSA_SECRET_KEY", ""),
            return_url=os.getenv("YOOKASSA_RETURN_URL", "https://example.com/payment-result"),
            admin_token=os.getenv("BILLING_ADMIN_TOKEN", ""),
            admin_user_id=int(os.getenv("ADMIN_TELEGRAM_ID", "0")),
            vat_code=int(os.getenv("YOOKASSA_VAT_CODE", "1")),
            studio_enabled=os.getenv("STUDIO_ENABLED", "true").lower() == "true",
            studio_port=int(os.getenv("STUDIO_PORT", "8089")),
            mini_app_url=os.getenv("MINI_APP_URL", ""),
        ).validate()
