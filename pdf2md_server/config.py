from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(item.strip().lower() for item in (value or "").split(",") if item.strip())


@dataclass(frozen=True)
class ProviderSettings:
    name: str
    issuer: str
    client_id: str | None
    client_secret: str | None
    scopes: tuple[str, ...] = ("openid", "email", "profile")

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from environment variables.

    The production deployment supplies provider credentials through its secret
    manager. No credentials belong in the repository.
    """

    public_base_url: str
    issuer_url: str
    resource_url: str
    database_path: Path
    session_secret: str
    max_file_bytes: int = 25 * 1024 * 1024
    daily_quota_bytes: int = 100 * 1024 * 1024
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 30 * 24 * 3600
    oauth_state_ttl_seconds: int = 600
    allowed_download_hosts: tuple[str, ...] = ()
    providers: tuple[ProviderSettings, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        public_base_url = os.getenv("PDF2MD_PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
        issuer_url = os.getenv("PDF2MD_ISSUER_URL", public_base_url).rstrip("/")
        resource_url = os.getenv("PDF2MD_RESOURCE_URL", f"{public_base_url}/mcp").rstrip("/")
        database_path = Path(os.getenv("PDF2MD_DATABASE_PATH", "./data/pdf2md.sqlite3")).expanduser()
        session_secret = os.getenv("PDF2MD_SESSION_SECRET", "")

        providers = (
            ProviderSettings(
                "google",
                os.getenv("PDF2MD_GOOGLE_ISSUER", "https://accounts.google.com").rstrip("/"),
                os.getenv("PDF2MD_GOOGLE_CLIENT_ID"),
                os.getenv("PDF2MD_GOOGLE_CLIENT_SECRET"),
            ),
            ProviderSettings(
                "microsoft",
                os.getenv("PDF2MD_MICROSOFT_ISSUER", "https://login.microsoftonline.com/common/v2.0").rstrip("/"),
                os.getenv("PDF2MD_MICROSOFT_CLIENT_ID"),
                os.getenv("PDF2MD_MICROSOFT_CLIENT_SECRET"),
            ),
            ProviderSettings(
                "apple",
                os.getenv("PDF2MD_APPLE_ISSUER", "https://appleid.apple.com").rstrip("/"),
                os.getenv("PDF2MD_APPLE_CLIENT_ID"),
                os.getenv("PDF2MD_APPLE_CLIENT_SECRET"),
            ),
        )

        return cls(
            public_base_url=public_base_url,
            issuer_url=issuer_url,
            resource_url=resource_url,
            database_path=database_path,
            session_secret=session_secret,
            max_file_bytes=int(os.getenv("PDF2MD_MAX_FILE_BYTES", str(25 * 1024 * 1024))),
            daily_quota_bytes=int(os.getenv("PDF2MD_DAILY_QUOTA_BYTES", str(100 * 1024 * 1024))),
            access_token_ttl_seconds=int(os.getenv("PDF2MD_ACCESS_TOKEN_TTL", "3600")),
            refresh_token_ttl_seconds=int(os.getenv("PDF2MD_REFRESH_TOKEN_TTL", str(30 * 24 * 3600))),
            oauth_state_ttl_seconds=int(os.getenv("PDF2MD_OAUTH_STATE_TTL", "600")),
            allowed_download_hosts=_csv(os.getenv("PDF2MD_ALLOWED_DOWNLOAD_HOSTS")),
            providers=providers,
        )

    def validate(self, *, production: bool = False) -> None:
        if production and (not self.session_secret or len(self.session_secret) < 32):
            raise ValueError("PDF2MD_SESSION_SECRET must contain at least 32 characters in production")
        if production and not self.public_base_url.startswith("https://"):
            raise ValueError("PDF2MD_PUBLIC_BASE_URL must use HTTPS in production")
        if production and not self.issuer_url.startswith("https://"):
            raise ValueError("PDF2MD_ISSUER_URL must use HTTPS in production")
        if self.max_file_bytes <= 0 or self.daily_quota_bytes <= 0:
            raise ValueError("PDF size and daily quota must be positive")
        if self.max_file_bytes > self.daily_quota_bytes:
            raise ValueError("PDF2MD_MAX_FILE_BYTES cannot exceed PDF2MD_DAILY_QUOTA_BYTES")

    def provider(self, name: str) -> ProviderSettings | None:
        return next((item for item in self.providers if item.name == name), None)

    @property
    def enabled_providers(self) -> tuple[ProviderSettings, ...]:
        return tuple(item for item in self.providers if item.enabled)

