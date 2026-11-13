import pytest

from app.core.config import Settings

SAFE = dict(
    env="production", secret_key="x" * 48, cookie_secure=True, require_admin_mfa=True,
    stripe_secret_key="sk_live_abc", stripe_webhook_secret="whsec_abc", email_backend="smtp",
    frontend_url="https://bootcamps.example.ca", api_base_url="https://api.bootcamps.example.ca",
)


def test_production_settings_accepted_when_safe():
    assert Settings(_env_file=None, **SAFE).env == "production"


@pytest.mark.parametrize("override", [
    {"secret_key": "change-me-in-production-please-32chars-min"},
    {"cookie_secure": False},
    {"stripe_secret_key": "sk_test_abc"},
    {"stripe_webhook_secret": ""},
    {"email_backend": "file"},
    {"frontend_url": "http://localhost:3000"},
])
def test_production_settings_rejected_when_unsafe(override):
    with pytest.raises(ValueError, match="Unsafe production configuration"):
        Settings(_env_file=None, **{**SAFE, **override})


def test_local_settings_not_checked():
    assert Settings(_env_file=None, env="local").env == "local"
