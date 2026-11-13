"""Admin: categories, bootcamps (EF-61), cohorts (EF-62), sessions (EF-63)."""

import uuid
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Query, Request, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import DB, AdminUser, StaffUser, own_cohort
from app.api.v1.admin.common import apply, dt, slugify
from app.core import urls
from app.core.db import utcnow
from app.core.errors import AppError
from app.core.html import clean
from app.core.i18n import fmt_datetime, pick
from app.core.security import client_ip
from app.models import Bootcamp, Category, ClassSession, Cohort, Enrollment, FaqItem, SlugRedirect, SyllabusItem
from app.schemas.common import Ok
from app.services import audit, calendar, catalog, jobs, seats, site_settings, storage, waitlist

router = APIRouter(tags=["admin"])


# --- Categories --------------------------------------------------------------------------


class CategoryIn(BaseModel):
    name_fr: str = Field(min_length=1, max_length=100)
    name_en: str = Field(default="", max_length=100)
    slug_fr: str | None = Field(default=None, max_length=120)
    slug_en: str | None = Field(default=None, max_length=120)
    position: int = 0


def category_out(c: Category) -> dict:
    return {"id": str(c.id), "name_fr": c.name_fr, "name_en": c.name_en, "slug_fr": c.slug_fr, "slug_en": c.slug_en, "position": c.position}


@router.get("/admin/categories")
def list_categories(db: DB, _: AdminUser) -> list[dict]:
    return [category_out(c) for c in db.scalars(select(Category).order_by(Category.position, Category.name_fr))]


@router.post("/admin/categories", status_code=201)
def create_category(body: CategoryIn, db: DB, _: AdminUser) -> dict:
    c = Category(name_fr=body.name_fr, name_en=body.name_en, slug_fr=slugify(body.slug_fr or body.name_fr), slug_en=slugify(body.slug_en or body.name_en or body.name_fr), position=body.position)
    db.add(c)
    _commit_unique(db)
    return category_out(c)


@router.put("/admin/categories/{cid}")
def update_category(cid: uuid.UUID, body: CategoryIn, db: DB, _: AdminUser) -> dict:
    c = _get(db, Category, cid)
    apply(c, {"name_fr": body.name_fr, "name_en": body.name_en, "position": body.position})
    c.slug_fr = slugify(body.slug_fr or body.name_fr)
    c.slug_en = slugify(body.slug_en or body.name_en or body.name_fr)
    _commit_unique(db)
    return category_out(c)


@router.delete("/admin/categories/{cid}", response_model=Ok)
def delete_category(cid: uuid.UUID, db: DB, _: AdminUser) -> Ok:
    db.delete(_get(db, Category, cid))
    db.commit()
    return Ok()


def _get(db, model, oid):
    obj = db.get(model, oid)
    if not obj:
        raise AppError("not_found", 404)
    return obj


def _commit_unique(db) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError("slug_taken" if "slug" in str(exc.orig) else "conflict", 409) from exc


# --- Bootcamps --------------------------------------------------------------------------------


class SyllabusIn(BaseModel):
    week: int = Field(ge=1, le=104)
    title_fr: str = Field(min_length=1, max_length=200)
    title_en: str = Field(default="", max_length=200)
    content_fr: str = ""
    content_en: str = ""


class BootcampFaqIn(BaseModel):
    question_fr: str = Field(min_length=1, max_length=500)
    question_en: str = Field(default="", max_length=500)
    answer_fr: str = Field(min_length=1)
    answer_en: str = ""


class BootcampIn(BaseModel):
    title_fr: str = Field(min_length=1, max_length=200)
    title_en: str = Field(default="", max_length=200)
    slug_fr: str | None = Field(default=None, max_length=160)
    slug_en: str | None = Field(default=None, max_length=160)
    tagline_fr: str = Field(default="", max_length=300)
    tagline_en: str = Field(default="", max_length=300)
    summary_fr: str = ""
    summary_en: str = ""
    description_fr: str = ""
    description_en: str = ""
    outcomes_fr: list[str] = []
    outcomes_en: list[str] = []
    prerequisites_fr: str = ""
    prerequisites_en: str = ""
    preparation_fr: str = ""
    preparation_en: str = ""
    tools: list[str] = []
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    duration_weeks: int = Field(default=1, ge=1, le=104)
    language: Literal["fr", "en", "bilingual"] = "fr"
    category_id: uuid.UUID | None = None
    instructor_id: uuid.UUID | None = None
    image_url: str | None = Field(default=None, max_length=500)
    image_alt_fr: str = Field(default="", max_length=300)
    image_alt_en: str = Field(default="", max_length=300)
    keywords: list[str] = []
    featured: bool = False
    taxable: bool = True
    seo_title_fr: str = Field(default="", max_length=200)
    seo_title_en: str = Field(default="", max_length=200)
    seo_description_fr: str = Field(default="", max_length=400)
    seo_description_en: str = Field(default="", max_length=400)
    position: int = 0
    syllabus: list[SyllabusIn] = []
    faq: list[BootcampFaqIn] = []


RICH = ["description_fr", "description_en", "prerequisites_fr", "prerequisites_en", "preparation_fr", "preparation_en"]
FR_REQUIRED = ["title_fr", "tagline_fr", "summary_fr", "description_fr", "prerequisites_fr"]
EN_PAIRS = ["title", "tagline", "summary", "description", "prerequisites"]


def bootcamp_out(db, b: Bootcamp) -> dict:
    faq = db.scalars(select(FaqItem).where(FaqItem.bootcamp_id == b.id).order_by(FaqItem.position))
    fields = {c.key: getattr(b, c.key) for c in Bootcamp.__table__.columns if c.key not in ("id", "created_at", "updated_at")}
    fields = {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in fields.items()}
    return {
        "id": str(b.id), **fields, "created_at": dt(b.created_at), "updated_at": dt(b.updated_at),
        "syllabus": [{"week": s.week, "title_fr": s.title_fr, "title_en": s.title_en, "content_fr": s.content_fr, "content_en": s.content_en} for s in b.syllabus],
        "faq": [{"question_fr": f.question_fr, "question_en": f.question_en, "answer_fr": f.answer_fr, "answer_en": f.answer_en} for f in faq],
        "cohorts_count": len(b.cohorts),
        "missing_fr": [f for f in FR_REQUIRED if not (getattr(b, f) or "").strip()] + ([] if b.syllabus else ["syllabus"]),
        "missing_en": [f"{p}_en" for p in EN_PAIRS if not (getattr(b, f"{p}_en") or "").strip()],
    }


def _save_bootcamp(db, b: Bootcamp, body: BootcampIn, actor_id, ip) -> None:
    data = body.model_dump(exclude={"syllabus", "faq", "slug_fr", "slug_en"})
    for f in RICH:
        data[f] = clean(data[f])
    new_fr = slugify(body.slug_fr or body.title_fr)
    new_en = slugify(body.slug_en or body.title_en or body.title_fr)
    for loc, new in (("fr", new_fr), ("en", new_en)):
        old = getattr(b, f"slug_{loc}", None)
        if old and old != new and b.status == "published":
            db.add(SlugRedirect(bootcamp_id=b.id, locale=loc, old_slug=old))  # 301 when a slug changes
        setattr(b, f"slug_{loc}", new)
    apply(b, data)
    for s in list(b.syllabus):
        db.delete(s)
    b.syllabus = []
    for i, s in enumerate(body.syllabus):
        item = s.model_dump()
        item["content_fr"], item["content_en"] = clean(item["content_fr"]), clean(item["content_en"])
        b.syllabus.append(SyllabusItem(position=i, **item))
    if b.id:
        for f in db.scalars(select(FaqItem).where(FaqItem.bootcamp_id == b.id)):
            db.delete(f)
    db.flush()
    for i, f in enumerate(body.faq):
        faq = f.model_dump()
        faq["answer_fr"], faq["answer_en"] = clean(faq["answer_fr"]), clean(faq["answer_en"])
        db.add(FaqItem(bootcamp_id=b.id, position=i, theme_fr="", theme_en="", **faq))


@router.get("/admin/bootcamps")
def list_bootcamps(db: DB, _: AdminUser, status: str | None = None, q: str | None = None) -> list[dict]:
    query = select(Bootcamp).options(selectinload(Bootcamp.cohorts), selectinload(Bootcamp.category)).order_by(Bootcamp.position, Bootcamp.title_fr)
    if status:
        query = query.where(Bootcamp.status == status)
    if q:
        query = query.where(or_(Bootcamp.title_fr.ilike(f"%{q}%"), Bootcamp.title_en.ilike(f"%{q}%")))
    out = []
    for b in db.scalars(query):
        out.append({
            "id": str(b.id), "title_fr": b.title_fr, "title_en": b.title_en, "slug_fr": b.slug_fr, "status": b.status,
            "level": b.level, "category": b.category.name_fr if b.category else None, "featured": b.featured,
            "cohorts": len(b.cohorts), "upcoming_cohorts": len(catalog.upcoming_cohorts(b)), "updated_at": dt(b.updated_at),
        })
    return out


@router.post("/admin/bootcamps", status_code=201)
def create_bootcamp(body: BootcampIn, request: Request, db: DB, user: AdminUser) -> dict:
    b = Bootcamp(id=uuid.uuid4(), status="draft")
    db.add(b)  # fields are set by _save_bootcamp before anything is flushed
    _save_bootcamp(db, b, body, user.id, client_ip(request))
    _commit_unique(db)
    db.refresh(b)
    return bootcamp_out(db, b)


@router.get("/admin/bootcamps/{bid}")
def get_bootcamp(bid: uuid.UUID, db: DB, _: AdminUser) -> dict:
    return bootcamp_out(db, _get(db, Bootcamp, bid))


@router.put("/admin/bootcamps/{bid}")
def update_bootcamp(bid: uuid.UUID, body: BootcampIn, request: Request, db: DB, user: AdminUser) -> dict:
    b = _get(db, Bootcamp, bid)
    _save_bootcamp(db, b, body, user.id, client_ip(request))
    if b.status == "published":
        _check_publishable(b)
    _commit_unique(db)
    db.refresh(b)
    return bootcamp_out(db, b)


def _check_publishable(b: Bootcamp) -> None:
    """EF-82: a bootcamp cannot be published while a French field is empty."""
    missing = [f for f in FR_REQUIRED if not (getattr(b, f) or "").strip()]
    if not b.syllabus:
        missing.append("syllabus")
    if missing:
        raise AppError("french_required", 422, details={"fields": missing}, fields=", ".join(missing))


class StatusIn(BaseModel):
    status: Literal["draft", "published", "archived"]


@router.post("/admin/bootcamps/{bid}/status")
def set_status(bid: uuid.UUID, body: StatusIn, db: DB, _: AdminUser) -> dict:
    b = _get(db, Bootcamp, bid)
    if body.status == "published":
        _check_publishable(b)
    b.status = body.status
    db.commit()
    return bootcamp_out(db, b)


@router.post("/admin/bootcamps/{bid}/duplicate", status_code=201)
def duplicate_bootcamp(bid: uuid.UUID, db: DB, _: AdminUser) -> dict:
    src = _get(db, Bootcamp, bid)
    data = {c.key: getattr(src, c.key) for c in Bootcamp.__table__.columns if c.key not in ("id", "created_at", "updated_at", "status", "slug_fr", "slug_en")}
    suffix = uuid.uuid4().hex[:4]
    b = Bootcamp(**data, status="draft", slug_fr=f"{src.slug_fr}-copie-{suffix}", slug_en=f"{src.slug_en}-copy-{suffix}")
    b.title_fr = f"{src.title_fr} (copie)"
    b.title_en = f"{src.title_en} (copy)" if src.title_en else ""
    db.add(b)
    db.flush()
    for s in src.syllabus:
        b.syllabus.append(SyllabusItem(week=s.week, position=s.position, title_fr=s.title_fr, title_en=s.title_en, content_fr=s.content_fr, content_en=s.content_en))
    for f in db.scalars(select(FaqItem).where(FaqItem.bootcamp_id == src.id)):
        db.add(FaqItem(bootcamp_id=b.id, position=f.position, question_fr=f.question_fr, question_en=f.question_en, answer_fr=f.answer_fr, answer_en=f.answer_en))
    db.commit()
    return bootcamp_out(db, b)


@router.get("/admin/bootcamps/{bid}/preview")
def preview_bootcamp(bid: uuid.UUID, db: DB, _: AdminUser, locale: Literal["fr", "en"] = "fr") -> dict:
    """Preview before publishing, same shape as the public page."""
    return catalog.detail(db, _get(db, Bootcamp, bid), locale)


@router.post("/admin/uploads/image")
async def upload_image(_: AdminUser, file: UploadFile = File(...)) -> dict:
    return {"url": await storage.save_public_image(file)}


# --- Cohorts ------------------------------------------------------------------------------------


class Slot(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")


class CohortIn(BaseModel):
    bootcamp_id: uuid.UUID
    code: str = Field(min_length=1, max_length=40)
    start_date: date
    end_date: date
    schedule: list[Slot] = []
    time_zone: str = "America/Toronto"
    capacity: int = Field(ge=1, le=10000)
    price_cents: int = Field(ge=0)
    early_bird_price_cents: int | None = Field(default=None, ge=0)
    early_bird_until: datetime | None = None
    registration_deadline: datetime | None = None
    instructor_id: uuid.UUID | None = None
    meeting_url: str | None = Field(default=None, max_length=500)
    language: Literal["fr", "en", "bilingual"] = "fr"
    status: Literal["draft", "open", "closed", "cancelled"] = "open"

    @field_validator("end_date")
    @classmethod
    def after_start(cls, v: date, info) -> date:
        start = info.data.get("start_date")
        if start and v < start:
            raise ValueError("end_date must be after start_date")
        return v


PRICE_FIELDS = ["price_cents", "early_bird_price_cents", "early_bird_until", "capacity"]


def cohort_out(db, c: Cohort) -> dict:
    confirmed = seats.confirmed_count(db, c.id)
    holds = seats.active_holds_count(db, c.id)
    from app.models import WaitlistEntry

    waiting = db.scalar(select(WaitlistEntry.id).where(WaitlistEntry.cohort_id == c.id, WaitlistEntry.status.in_(("waiting", "offered"))).limit(1)) is not None
    return {
        "id": str(c.id), "bootcamp_id": str(c.bootcamp_id), "bootcamp_title": c.bootcamp.title_fr, "code": c.code,
        "start_date": c.start_date.isoformat(), "end_date": c.end_date.isoformat(), "schedule": c.schedule,
        "schedule_text": calendar.schedule_text(c, "fr"), "time_zone": c.time_zone, "capacity": c.capacity,
        "price_cents": c.price_cents, "early_bird_price_cents": c.early_bird_price_cents, "early_bird_until": dt(c.early_bird_until),
        "registration_deadline": dt(c.registration_deadline), "instructor_id": str(c.instructor_id) if c.instructor_id else None,
        "meeting_url": c.meeting_url, "language": c.language, "status": c.status,
        "enrolled": confirmed, "holds": holds, "seats_left": max(0, c.capacity - confirmed - holds),
        "fill_rate": round(100 * confirmed / c.capacity) if c.capacity else 0, "has_waitlist": waiting,
        "sessions_count": len([s for s in c.sessions if s.status != "cancelled"]),
        "display_status": seats.display_status(c, max(0, c.capacity - confirmed - holds), int(site_settings.get(db, "few_seats_threshold"))),
    }


def _default_deadline(db, start: date, tz: str) -> datetime:
    """EF-13: by default registration closes 1 day before the start (configurable)."""
    days = int(site_settings.get(db, "default_registration_deadline_days"))
    return datetime.combine(start - timedelta(days=days), time(23, 59), tzinfo=ZoneInfo(tz))


@router.get("/admin/cohorts")
def list_cohorts(db: DB, _: AdminUser, bootcamp_id: uuid.UUID | None = None, upcoming: bool = False) -> list[dict]:
    q = select(Cohort).options(selectinload(Cohort.bootcamp), selectinload(Cohort.sessions)).order_by(Cohort.start_date.desc())
    if bootcamp_id:
        q = q.where(Cohort.bootcamp_id == bootcamp_id)
    if upcoming:
        q = q.where(Cohort.end_date >= utcnow().date())
    return [cohort_out(db, c) for c in db.scalars(q)]


@router.post("/admin/cohorts", status_code=201)
def create_cohort(body: CohortIn, request: Request, db: DB, user: AdminUser) -> dict:
    _get(db, Bootcamp, body.bootcamp_id)
    data = body.model_dump()
    data["schedule"] = [s.model_dump() for s in body.schedule]
    data["registration_deadline"] = body.registration_deadline or _default_deadline(db, body.start_date, body.time_zone)
    c = Cohort(**data)
    db.add(c)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError("conflict", 409) from exc
    calendar.generate_sessions(db, c, replace_future=False)
    audit.log(db, user.id, "create", "cohort", c.id, None, audit.snapshot(c, PRICE_FIELDS), client_ip(request))
    db.commit()
    return cohort_out(db, c)


@router.get("/admin/cohorts/{cid}")
def get_cohort(cid: uuid.UUID, db: DB, _: AdminUser) -> dict:
    c = _get(db, Cohort, cid)
    out = cohort_out(db, c)
    out["sessions"] = [session_out(s) for s in c.sessions]
    return out


@router.put("/admin/cohorts/{cid}")
def update_cohort(cid: uuid.UUID, body: CohortIn, request: Request, db: DB, user: AdminUser) -> dict:
    c = seats.lock_cohorts(db, [cid])[0] if db.get(Cohort, cid) else None
    if not c:
        raise AppError("not_found", 404)
    before = audit.snapshot(c, PRICE_FIELDS + ["status", "start_date", "end_date", "schedule"])
    schedule_changed = [s.model_dump() for s in body.schedule] != c.schedule or body.start_date != c.start_date or body.end_date != c.end_date
    data = body.model_dump()
    data["schedule"] = [s.model_dump() for s in body.schedule]
    data["registration_deadline"] = body.registration_deadline or _default_deadline(db, body.start_date, body.time_zone)
    apply(c, data)
    if schedule_changed:
        calendar.generate_sessions(db, c)
    audit.log(db, user.id, "update", "cohort", c.id, before, audit.snapshot(c, PRICE_FIELDS + ["status", "start_date", "end_date", "schedule"]), client_ip(request))
    _commit_unique(db)
    waitlist.trigger(db, c.id)  # capacity may have increased
    db.commit()
    return get_cohort(cid, db, user)


@router.post("/admin/cohorts/{cid}/duplicate", status_code=201)
def duplicate_cohort(cid: uuid.UUID, db: DB, user: AdminUser) -> dict:
    src = _get(db, Cohort, cid)
    data = {c.key: getattr(src, c.key) for c in Cohort.__table__.columns if c.key not in ("id", "created_at", "updated_at", "code", "prep_email_sent_at", "full_alert_sent_at")}
    c = Cohort(**data, code=f"{src.code}-{uuid.uuid4().hex[:4].upper()}")
    c.status = "draft"
    db.add(c)
    db.flush()
    calendar.generate_sessions(db, c, replace_future=False)
    db.commit()
    return cohort_out(db, c)


@router.post("/admin/cohorts/{cid}/generate-sessions")
def regenerate_sessions(cid: uuid.UUID, db: DB, _: AdminUser) -> dict:
    c = _get(db, Cohort, cid)
    calendar.generate_sessions(db, c)
    db.commit()
    return get_cohort(cid, db, _)


# --- Sessions (EF-63): move / cancel / add, students are emailed on every change ----------------


class SessionIn(BaseModel):
    starts_at: datetime
    ends_at: datetime
    title: str = Field(default="", max_length=200)
    meeting_url_override: str | None = Field(default=None, max_length=500)
    status: Literal["scheduled", "moved", "cancelled"] | None = None
    notify: bool = True


def session_out(s: ClassSession) -> dict:
    return {
        "id": str(s.id), "cohort_id": str(s.cohort_id), "starts_at": dt(s.starts_at), "ends_at": dt(s.ends_at), "title": s.title,
        "status": s.status, "meeting_url_override": s.meeting_url_override, "previous_starts_at": dt(s.previous_starts_at),
    }


def _notify_change(db, s: ClassSession, change: str, old_start: datetime | None) -> None:
    c = s.cohort
    for e in db.scalars(select(Enrollment).where(Enrollment.cohort_id == c.id, Enrollment.status == "confirmed")):
        u = e.user
        loc = u.locale
        title = pick(c.bootcamp, "title", loc)
        labels = {"moved": ("déplacée", "moved"), "cancelled": ("annulée", "cancelled"), "added": ("ajoutée", "added")}[change]
        jobs.send_email(
            db, "session_changed", u.email, loc,
            {
                "first_name": u.first_name, "bootcamp_title": title, "session_title": calendar.session_title(s, title, loc),
                "change": labels[0] if loc == "fr" else labels[1],
                "old_time": fmt_datetime(old_start, loc, u.time_zone) if old_start else "—",
                "new_time": ("—" if change == "cancelled" else fmt_datetime(s.starts_at, loc, u.time_zone)),
                "dashboard_url": urls.front("dashboard", loc),
            },
            user_id=u.id, attachments=[{"kind": "ics_session", "id": str(s.id)}],
        )


@router.post("/admin/cohorts/{cid}/sessions", status_code=201)
def add_session(cid: uuid.UUID, body: SessionIn, db: DB, user: StaffUser) -> dict:
    c = own_cohort(db, user, cid)
    s = ClassSession(cohort_id=c.id, starts_at=body.starts_at, ends_at=body.ends_at, title=body.title, meeting_url_override=body.meeting_url_override, sequence=1)
    c.sessions.append(s)
    db.flush()
    calendar.renumber(c)
    if body.notify:
        _notify_change(db, s, "added", None)
    db.commit()
    return session_out(s)


@router.put("/admin/sessions/{sid}")
def update_session(sid: uuid.UUID, body: SessionIn, db: DB, user: AdminUser) -> dict:
    s = _get(db, ClassSession, sid)
    old_start = s.starts_at
    moved = body.starts_at != s.starts_at or body.ends_at != s.ends_at
    cancelling = body.status == "cancelled" and s.status != "cancelled"
    s.title = body.title or s.title
    s.meeting_url_override = body.meeting_url_override
    if moved:
        s.previous_starts_at = old_start
        s.starts_at, s.ends_at = body.starts_at, body.ends_at
        s.status = "moved"
        s.reminder_24h_sent_at = s.reminder_1h_sent_at = None
    if cancelling:
        s.status = "cancelled"
    elif body.status == "scheduled" and s.status == "cancelled":
        s.status = "scheduled"
    if moved or cancelling:
        s.sequence += 1
        calendar.renumber(s.cohort)
        if body.notify:
            _notify_change(db, s, "cancelled" if cancelling else "moved", old_start)
    db.commit()
    return session_out(s)


@router.delete("/admin/sessions/{sid}", response_model=Ok)
def delete_session(sid: uuid.UUID, db: DB, _: AdminUser) -> Ok:
    s = _get(db, ClassSession, sid)
    if s.status != "cancelled":
        raise AppError("invalid_state", 409)  # cancel first, so students are notified
    db.delete(s)
    db.commit()
    return Ok()


@router.get("/admin/sessions/upcoming")
def upcoming_sessions(db: DB, _: AdminUser, days: int = Query(default=14, le=90)) -> list[dict]:
    now = utcnow()
    rows = db.scalars(select(ClassSession).where(ClassSession.starts_at >= now, ClassSession.starts_at <= now + timedelta(days=days)).order_by(ClassSession.starts_at))
    return [{**session_out(s), "cohort_code": s.cohort.code, "bootcamp_title": s.cohort.bootcamp.title_fr} for s in rows]
