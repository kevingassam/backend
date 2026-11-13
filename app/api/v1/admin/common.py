import re
import unicodedata
from datetime import UTC
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value[:150] or "item"


def apply(obj: Any, data: dict[str, Any]) -> None:
    for k, v in data.items():
        setattr(obj, k, v)


def paginate(db: Session, query, page: int, page_size: int, mapper) -> dict:
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = db.scalars(query.offset((page - 1) * page_size).limit(page_size))
    return {"items": [mapper(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def dt(v) -> str | None:
    """ISO 8601 in UTC."""
    if not v:
        return None
    if hasattr(v, "tzinfo") and v.tzinfo is not None:
        v = v.astimezone(UTC)
    return v.isoformat()
