"""Locale detection, API message catalog and locale-aware formatting (EF-84)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from babel.dates import format_date, format_datetime, format_time
from babel.numbers import format_currency
from fastapi import Request

LOCALES = ("fr", "en")
DEFAULT_LOCALE = "fr"
BUSINESS_TZ = "America/Toronto"  # heure de l'Est (Montréal)

_BABEL = {"fr": "fr_CA", "en": "en_CA"}


def norm_locale(value: str | None) -> str:
    if not value:
        return DEFAULT_LOCALE
    value = value.strip().lower()[:2]
    return value if value in LOCALES else DEFAULT_LOCALE


def request_locale(request: Request) -> str:
    header = request.headers.get("accept-language", "")
    for part in header.split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LOCALES:
            return code
    return DEFAULT_LOCALE


def money(cents: int, locale: str) -> str:
    # 1 299,00 $ (fr) / $1,299.00 (en)
    return format_currency(cents / 100, "CAD", locale=_BABEL[norm_locale(locale)], format_type="standard").replace(
        "\xa0", " "
    ).replace(" ", " ").replace("CA$", "$").replace("$ CA", "$")


def fmt_date(d: date | datetime, locale: str, tz: str | None = None, fmt: str = "long") -> str:
    if isinstance(d, datetime) and tz:
        d = d.astimezone(ZoneInfo(tz))
    return format_date(d, format=fmt, locale=_BABEL[norm_locale(locale)])


def fmt_datetime(d: datetime, locale: str, tz: str = BUSINESS_TZ) -> str:
    loc = norm_locale(locale)
    local = d.astimezone(ZoneInfo(tz))
    day = format_date(local, format="full", locale=_BABEL[loc])
    time = format_time(local, format="short", locale=_BABEL[loc])
    tzname = local.tzname() or ""
    return f"{day}, {time} ({tzname})" if loc == "en" else f"{day} à {time} ({tzname})"


def fmt_dt_short(d: datetime, locale: str, tz: str = BUSINESS_TZ) -> str:
    return format_datetime(d.astimezone(ZoneInfo(tz)), format="medium", locale=_BABEL[norm_locale(locale)])


def pick(obj: object, field: str, locale: str) -> str:
    """Return obj.<field>_<locale>, falling back to French (the reference language)."""
    loc = norm_locale(locale)
    val = getattr(obj, f"{field}_{loc}", None)
    if not val and loc != "fr":
        val = getattr(obj, f"{field}_fr", None)
    return val or ""


MESSAGES: dict[str, dict[str, str]] = {
    "validation_error": {"fr": "Certaines données sont invalides.", "en": "Some data is invalid."},
    "unauthorized": {"fr": "Veuillez vous connecter.", "en": "Please sign in."},
    "forbidden": {"fr": "Accès refusé.", "en": "Access denied."},
    "not_found": {"fr": "Ressource introuvable.", "en": "Resource not found."},
    "method_not_allowed": {"fr": "Méthode non permise.", "en": "Method not allowed."},
    "http_error": {"fr": "Erreur.", "en": "Error."},
    "rate_limited": {
        "fr": "Trop de tentatives. Réessayez dans quelques minutes.",
        "en": "Too many attempts. Please try again in a few minutes.",
    },
    "csrf_failed": {"fr": "Jeton CSRF invalide. Rechargez la page.", "en": "Invalid CSRF token. Reload the page."},
    "email_taken": {"fr": "Un compte existe déjà avec ce courriel.", "en": "An account already exists with this email."},
    "invalid_credentials": {"fr": "Courriel ou mot de passe incorrect.", "en": "Incorrect email or password."},
    "account_locked": {
        "fr": "Compte verrouillé temporairement après plusieurs tentatives. Réessayez dans {minutes} minutes.",
        "en": "Account temporarily locked after several attempts. Try again in {minutes} minutes.",
    },
    "invalid_token": {"fr": "Lien invalide ou expiré.", "en": "Invalid or expired link."},
    "weak_password": {
        "fr": "Le mot de passe doit contenir au moins 10 caractères.",
        "en": "Password must be at least 10 characters long.",
    },
    "wrong_password": {"fr": "Mot de passe actuel incorrect.", "en": "Current password is incorrect."},
    "mfa_required": {
        "fr": "L'authentification à deux facteurs est requise.",
        "en": "Two-factor authentication is required.",
    },
    "mfa_invalid": {"fr": "Code de vérification invalide.", "en": "Invalid verification code."},
    "google_not_configured": {
        "fr": "La connexion Google n'est pas configurée.",
        "en": "Google sign-in is not configured.",
    },
    "google_failed": {"fr": "La connexion Google a échoué.", "en": "Google sign-in failed."},
    "cohort_unavailable": {
        "fr": "Cette cohorte n'est pas ouverte aux inscriptions.",
        "en": "This cohort is not open for registration.",
    },
    "cohort_full": {"fr": "Cette cohorte est complète.", "en": "This cohort is full."},
    "already_enrolled": {
        "fr": "Vous êtes déjà inscrit à cette cohorte.",
        "en": "You are already enrolled in this cohort.",
    },
    "cart_empty": {"fr": "Votre panier est vide.", "en": "Your cart is empty."},
    "promo_invalid": {"fr": "Ce code promo n'est pas valide.", "en": "This promo code is not valid."},
    "promo_expired": {"fr": "Ce code promo est expiré.", "en": "This promo code has expired."},
    "promo_used_up": {
        "fr": "Ce code promo a atteint sa limite d'utilisation.",
        "en": "This promo code has reached its usage limit.",
    },
    "promo_already_used": {"fr": "Vous avez déjà utilisé ce code promo.", "en": "You have already used this promo code."},
    "promo_not_applicable": {
        "fr": "Ce code promo ne s'applique à aucun bootcamp de votre panier.",
        "en": "This promo code does not apply to any bootcamp in your cart.",
    },
    "terms_required": {
        "fr": "Vous devez accepter les conditions de vente et la politique de remboursement.",
        "en": "You must accept the terms of sale and the refund policy.",
    },
    "billing_address_required": {
        "fr": "L'adresse de facturation est requise.",
        "en": "A billing address is required.",
    },
    "seats_unavailable": {
        "fr": "Il n'y a plus de place dans certaines cohortes de votre panier.",
        "en": "Some cohorts in your cart no longer have seats available.",
    },
    "payments_not_configured": {
        "fr": "Le paiement en ligne n'est pas encore configuré.",
        "en": "Online payment is not configured yet.",
    },
    "stripe_error": {
        "fr": "Le service de paiement est indisponible. Réessayez.",
        "en": "The payment service is unavailable. Please try again.",
    },
    "refund_too_large": {
        "fr": "Le montant dépasse le solde remboursable.",
        "en": "Amount exceeds the refundable balance.",
    },
    "order_not_refundable": {"fr": "Cette commande ne peut pas être remboursée.", "en": "This order cannot be refunded."},
    "waitlist_already": {"fr": "Vous êtes déjà sur la liste d'attente.", "en": "You are already on the waitlist."},
    "waitlist_not_full": {
        "fr": "Des places sont disponibles : ajoutez la cohorte au panier.",
        "en": "Seats are available: add the cohort to your cart.",
    },
    "offer_invalid": {"fr": "Cette offre est invalide ou expirée.", "en": "This offer is invalid or has expired."},
    "email_not_verified": {
        "fr": "Vérifiez votre courriel pour accéder aux liens des séances.",
        "en": "Verify your email to access session links.",
    },
    "french_required": {
        "fr": "Les champs français obligatoires doivent être remplis avant la publication : {fields}.",
        "en": "Required French fields must be filled before publishing: {fields}.",
    },
    "slug_taken": {"fr": "Ce slug est déjà utilisé.", "en": "This slug is already in use."},
    "conflict": {"fr": "Conflit avec des données existantes.", "en": "Conflict with existing data."},
    "request_exists": {"fr": "Une demande est déjà en cours.", "en": "A request is already pending."},
    "invalid_state": {"fr": "Action impossible dans l'état actuel.", "en": "Action not possible in the current state."},
    "file_too_large": {"fr": "Fichier trop volumineux.", "en": "File too large."},
    "spam_detected": {"fr": "Message refusé.", "en": "Message rejected."},
    "not_completed": {"fr": "Attestation non disponible.", "en": "Certificate not available."},
}


def t(code: str, locale: str = DEFAULT_LOCALE, **params: object) -> str:
    entry = MESSAGES.get(code)
    if not entry:
        return code
    text = entry.get(norm_locale(locale)) or entry["fr"]
    try:
        return text.format(**params)
    except (KeyError, IndexError):
        return text
