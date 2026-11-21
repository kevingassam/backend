"""Localized frontend URLs used in emails and Stripe redirects (slugs translated, section 4)."""

from app.core.config import settings
from app.core.i18n import norm_locale

PATHS = {
    "home": {"fr": "", "en": ""},
    "dashboard": {"fr": "tableau-de-bord", "en": "dashboard"},
    "cart": {"fr": "panier", "en": "cart"},
    "checkout": {"fr": "paiement", "en": "checkout"},
    "confirmation": {"fr": "paiement/confirmation", "en": "checkout/confirmation"},
    "terms": {"fr": "conditions", "en": "terms"},
    "privacy": {"fr": "confidentialite", "en": "privacy"},
    "refunds": {"fr": "remboursements", "en": "refunds"},
    "verify_email": {"fr": "verifier-courriel", "en": "verify-email"},
    "confirm_email": {"fr": "confirmer-courriel", "en": "confirm-email"},
    "reset_password": {"fr": "reinitialiser-mot-de-passe", "en": "reset-password"},
    "waitlist_offer": {"fr": "liste-attente", "en": "waitlist"},
    "invitation": {"fr": "invitation", "en": "invitation"},
    "bootcamps": {"fr": "bootcamps", "en": "bootcamps"},
    "login": {"fr": "connexion", "en": "login"},
    "certifications": {"fr": "certifications", "en": "certifications"},
}


def front(name: str, locale: str, suffix: str = "", query: str = "") -> str:
    loc = norm_locale(locale)
    path = PATHS[name][loc]
    url = f"{settings.frontend_url}/{loc}"
    if path:
        url += f"/{path}"
    if suffix:
        url += f"/{suffix}"
    if query:
        url += f"?{query}"
    return url


def admin(path: str = "") -> str:
    return f"{settings.frontend_url}/admin{('/' + path) if path else ''}"
