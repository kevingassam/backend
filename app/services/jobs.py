"""Background job queue stored in PostgreSQL, processed by `python -m app.worker`.

Web requests only insert a row (never send mail inline). The worker claims jobs with
SELECT ... FOR UPDATE SKIP LOCKED so several workers can run safely.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import Job


def enqueue(db: Session, job_type: str, payload: dict[str, Any], run_at: datetime | None = None, max_attempts: int = 5) -> Job:
    job = Job(type=job_type, payload=payload, run_at=run_at or utcnow(), max_attempts=max_attempts)
    db.add(job)
    db.flush()
    return job


def send_email(
    db: Session,
    template: str,
    to: str,
    locale: str,
    context: dict[str, Any],
    *,
    user_id: Any = None,
    attachments: list[dict] | None = None,
    dedupe_key: str | None = None,
    delay: timedelta | None = None,
) -> Job:
    """Queue a transactional email. `attachments` items: {"kind": "invoice"|"ics_order"|"ics_session"|"certificate", "id": ...}."""
    return enqueue(
        db,
        "send_email",
        {
            "template": template,
            "to": to,
            "locale": locale,
            "context": context,
            "user_id": str(user_id) if user_id else None,
            "attachments": attachments or [],
            "dedupe_key": dedupe_key,
        },
        run_at=utcnow() + delay if delay else None,
    )
