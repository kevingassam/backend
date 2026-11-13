"""Instructor area (section 3): own cohorts only — roster, attendance, materials, session links. No payment data."""

import uuid
from datetime import datetime

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import DB, Locale, StaffUser, own_cohort
from app.core.db import utcnow
from app.core.errors import AppError
from app.core.i18n import pick
from app.models import Attendance, ClassSession, Cohort, Enrollment, Material
from app.schemas.common import Ok
from app.services import materials as mat_svc
from app.services import seats

router = APIRouter(prefix="/instructor", tags=["instructor"])


@router.get("/cohorts")
def my_cohorts(user: StaffUser, db: DB, loc: Locale) -> list[dict]:
    q = select(Cohort).options(selectinload(Cohort.bootcamp)).order_by(Cohort.start_date.desc())
    if user.role != "admin":
        q = q.where(Cohort.instructor_id == user.id)
    out = []
    for c in db.scalars(q):
        out.append({
            "id": str(c.id), "code": c.code, "bootcamp_title": pick(c.bootcamp, "title", loc),
            "start_date": c.start_date.isoformat(), "end_date": c.end_date.isoformat(), "status": c.status,
            "enrolled": seats.confirmed_count(db, c.id), "capacity": c.capacity, "meeting_url": c.meeting_url,
        })
    return out


@router.get("/cohorts/{cohort_id}/roster")
def roster(cohort_id: uuid.UUID, user: StaffUser, db: DB) -> dict:
    c = own_cohort(db, user, cohort_id)
    enrollments = db.scalars(
        select(Enrollment).where(Enrollment.cohort_id == c.id, Enrollment.status.in_(("confirmed", "completed"))).options(selectinload(Enrollment.user))
    )
    sessions = [s for s in c.sessions if s.status != "cancelled"]
    past = [s for s in sessions if s.starts_at <= utcnow()]
    att = {(a.session_id, a.enrollment_id): a.present for a in db.scalars(select(Attendance).where(Attendance.session_id.in_([s.id for s in sessions])))}
    students = []
    for e in enrollments:
        u = e.user
        present = sum(1 for s in past if att.get((s.id, e.id)))
        students.append({
            "enrollment_id": str(e.id), "first_name": u.first_name, "last_name": u.last_name, "email": u.email,
            "status": e.status, "attendance": {str(s.id): att.get((s.id, e.id)) for s in sessions},
            "attendance_rate": round(100 * present / len(past)) if past else None,
        })
    students.sort(key=lambda x: (x["last_name"].lower(), x["first_name"].lower()))
    return {
        "cohort": {"id": str(c.id), "code": c.code, "meeting_url": c.meeting_url},
        "sessions": [
            {"id": str(s.id), "starts_at": s.starts_at.isoformat(), "ends_at": s.ends_at.isoformat(), "title": s.title, "status": s.status, "meeting_url_override": s.meeting_url_override}
            for s in c.sessions
        ],
        "students": students,
    }


class AttendanceIn(BaseModel):
    session_id: uuid.UUID
    records: dict[uuid.UUID, bool]  # enrollment_id → present


@router.post("/cohorts/{cohort_id}/attendance", response_model=Ok)
def take_attendance(cohort_id: uuid.UUID, body: AttendanceIn, user: StaffUser, db: DB) -> Ok:
    c = own_cohort(db, user, cohort_id)
    s = db.get(ClassSession, body.session_id)
    if not s or s.cohort_id != c.id:
        raise AppError("not_found", 404)
    valid = set(db.scalars(select(Enrollment.id).where(Enrollment.cohort_id == c.id)))
    existing = {a.enrollment_id: a for a in db.scalars(select(Attendance).where(Attendance.session_id == s.id))}
    for enr_id, present in body.records.items():
        if enr_id not in valid:
            continue
        if enr_id in existing:
            existing[enr_id].present = present
        else:
            db.add(Attendance(session_id=s.id, enrollment_id=enr_id, present=present))
    db.commit()
    return Ok()


class LinksIn(BaseModel):
    meeting_url: HttpUrl | None = None


@router.patch("/cohorts/{cohort_id}", response_model=Ok)
def update_link(cohort_id: uuid.UUID, body: LinksIn, user: StaffUser, db: DB) -> Ok:
    c = own_cohort(db, user, cohort_id)
    c.meeting_url = str(body.meeting_url) if body.meeting_url else None
    db.commit()
    return Ok()


class SessionLinkIn(BaseModel):
    meeting_url_override: HttpUrl | None = None


@router.patch("/sessions/{session_id}", response_model=Ok)
def update_session_link(session_id: uuid.UUID, body: SessionLinkIn, user: StaffUser, db: DB) -> Ok:
    s = db.get(ClassSession, session_id)
    if not s:
        raise AppError("not_found", 404)
    own_cohort(db, user, s.cohort_id)
    s.meeting_url_override = str(body.meeting_url_override) if body.meeting_url_override else None
    db.commit()
    return Ok()


@router.get("/cohorts/{cohort_id}/materials")
def list_materials(cohort_id: uuid.UUID, user: StaffUser, db: DB) -> list[dict]:
    own_cohort(db, user, cohort_id)
    return [mat_svc.out(m) for m in db.scalars(select(Material).where(Material.cohort_id == cohort_id).order_by(Material.created_at.desc()))]


@router.post("/cohorts/{cohort_id}/materials", status_code=201)
async def add_material(
    cohort_id: uuid.UUID,
    user: StaffUser,
    db: DB,
    title: str = Form(min_length=1, max_length=300),
    type: str = Form(pattern="^(file|link)$"),
    description: str = Form(default="", max_length=5000),
    url: str | None = Form(default=None, max_length=1000),
    visible_from: datetime | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> dict:
    c = own_cohort(db, user, cohort_id)
    m = await mat_svc.create(db, c, user, title=title, mtype=type, description=description, url=url, visible_from=visible_from, upload=file)
    db.commit()
    return mat_svc.out(m)


@router.delete("/materials/{material_id}", response_model=Ok)
def delete_material(material_id: uuid.UUID, user: StaffUser, db: DB) -> Ok:
    m = db.get(Material, material_id)
    if not m:
        raise AppError("not_found", 404)
    own_cohort(db, user, m.cohort_id)
    mat_svc.delete(db, m)
    db.commit()
    return Ok()
