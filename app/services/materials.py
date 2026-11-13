"""Course material per cohort (EF-52) and "new material" notifications (section 7)."""

from datetime import datetime

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import urls
from app.core.db import utcnow
from app.core.errors import AppError
from app.core.i18n import pick
from app.models import Cohort, Enrollment, Material, User
from app.services import jobs, storage


async def create(
    db: Session, cohort: Cohort, author: User, *, title: str, mtype: str, description: str,
    url: str | None, visible_from: datetime | None, upload: UploadFile | None,
) -> Material:
    m = Material(cohort_id=cohort.id, title=title, description=description, type=mtype, visible_from=visible_from, created_by_id=author.id)
    if mtype == "file":
        if not upload:
            raise AppError("validation_error", 422, details=[{"field": "file", "message": "required"}])
        key, size = await storage.save_private_file(upload, f"materials/{cohort.id}")
        m.storage_key, m.size_bytes, m.filename, m.content_type = key, size, upload.filename, upload.content_type
    else:
        if not url or not url.startswith(("http://", "https://")):
            raise AppError("validation_error", 422, details=[{"field": "url", "message": "invalid url"}])
        m.url = url
    db.add(m)
    db.flush()
    if not visible_from or visible_from <= utcnow():
        notify_students(db, m)
    return m


def notify_students(db: Session, m: Material) -> None:
    if m.notified_at:
        return
    m.notified_at = utcnow()
    cohort = db.get(Cohort, m.cohort_id)
    for e in db.scalars(select(Enrollment).where(Enrollment.cohort_id == m.cohort_id, Enrollment.status.in_(("confirmed", "completed")))):
        u = e.user
        if u.deleted_at or not (u.email_prefs or {}).get("materials", True):
            continue
        loc = u.locale
        jobs.send_email(
            db, "material_published", u.email, loc,
            {"first_name": u.first_name, "bootcamp_title": pick(cohort.bootcamp, "title", loc), "material_title": m.title, "dashboard_url": urls.front("dashboard", loc)},
            user_id=u.id, dedupe_key=f"material:{m.id}:{u.id}",
        )


def notify_due(db: Session) -> int:
    """Worker: materials scheduled with visible_from that just became visible."""
    due = list(db.scalars(select(Material).where(Material.notified_at.is_(None), Material.visible_from <= utcnow())))
    for m in due:
        notify_students(db, m)
    return len(due)


def delete(db: Session, m: Material) -> None:
    if m.type == "file":
        storage.delete_private(m.storage_key)
    db.delete(m)


def out(m: Material) -> dict:
    return {
        "id": str(m.id), "title": m.title, "description": m.description, "type": m.type, "url": m.url,
        "filename": m.filename, "size_bytes": m.size_bytes,
        "visible_from": m.visible_from.isoformat() if m.visible_from else None, "created_at": m.created_at.isoformat(),
    }
