"""Current versions of the legal texts, recorded with each consent (EF-88)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Page

KEYS = {"terms": "terms", "refund_policy": "refunds", "privacy": "privacy"}


def consent_versions(db: Session) -> dict[str, str]:
    pages = {p.key: p.version for p in db.scalars(select(Page).where(Page.key.in_(KEYS.values())))}
    out = {ctype: str(pages.get(key, 1)) for ctype, key in KEYS.items()}
    out["marketing"] = "1"
    return out
