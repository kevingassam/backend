"""Business settings editable in admin (EF-73), with defaults."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SiteSettings

DEFAULTS: dict[str, Any] = {
    "company_name": "Bootcamps",
    "company_address": "",
    "contact_email": "info@example.com",
    "contact_phone": "",
    "gst_number": "",  # TPS
    "qst_number": "",  # TVQ
    "hst_number": "",
    "social_links": {"linkedin": "", "facebook": "", "instagram": "", "youtube": "", "x": ""},
    "privacy_officer_name": "",
    "privacy_officer_email": "",
    "default_registration_deadline_days": 1,
    "seat_hold_minutes": 30,
    "waitlist_offer_hours": 48,
    "few_seats_threshold": 5,
    "multi_discount_enabled": True,
    "multi_discount_percent": 10,
    "multi_discount_min_items": 2,
    "join_button_minutes_before": 15,
    "tax_enabled": True,
}

PUBLIC_KEYS = (
    "company_name", "company_address", "contact_email", "contact_phone", "social_links",
    "privacy_officer_name", "privacy_officer_email", "multi_discount_enabled", "multi_discount_percent",
    "multi_discount_min_items", "few_seats_threshold", "join_button_minutes_before",
    "gst_number", "qst_number", "hst_number",
)


def get_all(db: Session) -> dict[str, Any]:
    row = db.scalar(select(SiteSettings).limit(1))
    data = dict(DEFAULTS)
    if row:
        data.update(row.data or {})
    return data


def get(db: Session, key: str) -> Any:
    return get_all(db)[key]


def public(db: Session) -> dict[str, Any]:
    data = get_all(db)
    return {k: data[k] for k in PUBLIC_KEYS}


def update(db: Session, values: dict[str, Any]) -> dict[str, Any]:
    row = db.scalar(select(SiteSettings).limit(1))
    if not row:
        row = SiteSettings(data={})
        db.add(row)
    merged = dict(row.data or {})
    for k, v in values.items():
        if k in DEFAULTS:
            merged[k] = v
    # Stripe Checkout sessions live at least 30 minutes: never hold seats for less.
    merged["seat_hold_minutes"] = max(30, min(1440, int(merged.get("seat_hold_minutes", 30))))
    row.data = merged
    db.flush()
    return get_all(db)
