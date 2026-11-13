from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_SECRET_KEY = "change-me-in-production-please-32chars-min"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    env: str = "local"  # local | staging | production | test
    database_url: str = "postgresql+psycopg://bootcamps:bootcamps@localhost:5432/bootcamps"

    # URLs
    api_base_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"
    cors_origins: list[str] = ["http://localhost:3000"]
    trust_proxy: bool = False  # true behind the load balancer / Next.js proxy that sets X-Forwarded-For

    # Auth
    secret_key: str = DEFAULT_SECRET_KEY
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    cookie_secure: bool = False
    cookie_domain: str | None = None
    reset_token_minutes: int = 60
    verify_token_hours: int = 48
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    require_admin_mfa: bool = True

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    currency: str = "cad"

    # Email
    email_backend: str = "file"  # file | smtp | console
    email_from: str = "Bootcamps <no-reply@example.com>"
    admin_alert_email: str = "admin@example.com"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_tls: bool = False

    # Storage
    storage_dir: Path = BASE_DIR / "var"
    signed_url_seconds: int = 600
    max_upload_mb: int = 50

    # Worker
    worker_poll_seconds: float = 2.0

    @model_validator(mode="after")
    def _check_production(self) -> "Settings":
        """Refuse to start in production with unsafe or missing settings."""
        if self.env != "production":
            return self
        problems = []
        if self.secret_key == DEFAULT_SECRET_KEY or len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be a random string of 32+ characters")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true (HTTPS)")
        if not self.require_admin_mfa:
            problems.append("REQUIRE_ADMIN_MFA must be true")
        if not self.stripe_secret_key.startswith("sk_live_"):
            problems.append("STRIPE_SECRET_KEY must be a live key (sk_live_…)")
        if not self.stripe_webhook_secret:
            problems.append("STRIPE_WEBHOOK_SECRET is required")
        if self.email_backend != "smtp":
            problems.append("EMAIL_BACKEND must be smtp")
        if "localhost" in self.frontend_url or "localhost" in self.api_base_url:
            problems.append("FRONTEND_URL / API_BASE_URL must be public URLs")
        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self

    @property
    def mail_dir(self) -> Path:
        return self.storage_dir / "mail"

    @property
    def private_dir(self) -> Path:
        return self.storage_dir / "private"

    @property
    def public_dir(self) -> Path:
        return self.storage_dir / "public"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
