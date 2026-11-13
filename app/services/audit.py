"""Audit trail for prices, refunds, enrollments and roles (EF-72)."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def snapshot(obj: Any, fields: list[str]) -> dict:
    return {f: _jsonable(getattr(obj, f, None)) for f in fields}


def log(
    db: Session,
    actor_id: uuid.UUID | None,
    action: str,
    entity: str,
    entity_id: Any,
    before: dict | None = None,
    after: dict | None = None,
    ip: str | None = None,
) -> None:
    if before is not None and after is not None and before == after:
        return
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity=entity,
            entity_id=str(entity_id),
            before=_jsonable(before),
            after=_jsonable(after),
            ip=ip,
        )
    )
