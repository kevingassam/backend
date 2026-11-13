"""Student area: profile, settings, privacy, enrollments, schedule, materials, orders (EF-23, EF-24, EF-50..EF-56, EF-89)."""

import uuid
from datetime import timedelta

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import DB, CurrentUser, Locale
from app.api.v1.auth import UserOut, user_out
from app.core.db import utcnow
from app.core.errors import AppError
from app.core.i18n import pick
from app.models import ClassSession, Cohort, Enrollment, Material, Order, User
from app.schemas.common import AddressIn, Loc, Ok, validate_canadian_province
from app.services import accounts, calendar, newsletter, orders, pdf, site_settings, storage, taxes
from app.services import enrollments as enr_svc

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return user_out(user)


class ProfileIn(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=40)
    locale: Loc | None = None
    time_zone: str | None = Field(default=None, max_length=64)
    marketing_opt_in: bool | None = None
    email_prefs: dict[str, bool] | None = None
    address: AddressIn | None = None


@router.patch("", response_model=UserOut)
def update_me(body: ProfileIn, user: CurrentUser, db: DB) -> UserOut:
    data = body.model_dump(exclude_unset=True)
    for f in ("first_name", "last_name", "phone", "locale", "time_zone"):
        if f in data:
            setattr(user, f, data[f])
    if "time_zone" in data:
        from zoneinfo import available_timezones

        if data["time_zone"] not in available_timezones():
            raise AppError("validation_error", 422, details=[{"field": "time_zone", "message": "Unknown time zone"}])
    if "email_prefs" in data:
        user.email_prefs = {**(user.email_prefs or {}), **{k: bool(v) for k, v in data["email_prefs"].items() if k in ("reminders", "materials")}}
    if "marketing_opt_in" in data and data["marketing_opt_in"] != user.marketing_opt_in:
        from app.models import Consent

        user.marketing_opt_in = data["marketing_opt_in"]
        db.add(Consent(user_id=user.id, email=user.email, type="marketing", version="1", granted=user.marketing_opt_in, context="profile"))
        if user.marketing_opt_in:
            newsletter.subscribe(db, user.email, user.locale, user.id)
        else:
            newsletter.unsubscribe_email(db, user.email)
    if body.address is not None:
        validate_canadian_province(body.address)
        orders.save_billing_address(db, user, body.address.model_dump())
    db.commit()
    db.refresh(user)
    return user_out(user)


class PasswordIn(BaseModel):
    current_password: str | None = None
    new_password: str = Field(max_length=200)


@router.post("/password", response_model=Ok)
def change_password(body: PasswordIn, user: CurrentUser, response: Response, db: DB) -> Ok:
    accounts.change_password(db, user, body.current_password, body.new_password)
    accounts.start_session(db, response, user, mfa=False if not user.is_staff else True)
    db.commit()
    return Ok()


class EmailChangeIn(BaseModel):
    new_email: EmailStr
    password: str | None = None


@router.post("/email", response_model=Ok)
def change_email(body: EmailChangeIn, user: CurrentUser, db: DB) -> Ok:
    accounts.request_email_change(db, user, body.new_email, body.password)
    db.commit()
    return Ok()


@router.get("/export")
def export(user: CurrentUser, db: DB) -> JSONResponse:
    """EF-89: download my data."""
    return JSONResponse(accounts.export_data(db, user), headers={"Content-Disposition": 'attachment; filename="mes-donnees.json"'})


class DeletionIn(BaseModel):
    details: str = Field(default="", max_length=2000)


@router.post("/deletion-request", response_model=Ok)
def deletion_request(body: DeletionIn, user: CurrentUser, db: DB) -> Ok:
    accounts.request_deletion(db, user, body.details)
    db.commit()
    return Ok()


class RectificationIn(BaseModel):
    details: str = Field(min_length=5, max_length=2000)


@router.post("/rectification-request", response_model=Ok)
def rectification_request(body: RectificationIn, user: CurrentUser, db: DB) -> Ok:
    from app.models import PrivacyRequest

    db.add(PrivacyRequest(user_id=user.id, email=user.email, type="rectification", details=body.details))
    db.commit()
    return Ok()


# --- Enrollments, schedule, materials -------------------------------------------------------


def _enrollment_status(c: Cohort, enr: Enrollment) -> str:
    """upcoming | in_progress | finished | cancelled (EF-50)."""
    if enr.status in ("cancelled", "transferred") or c.status == "cancelled":
        return "cancelled"
    today = utcnow().date()
    if enr.status == "completed" or c.end_date < today:
        return "finished"
    return "upcoming" if c.start_date > today else "in_progress"


def _join_info(db, user: User, s: ClassSession | None, c: Cohort) -> dict:
    """The meeting link is only revealed to verified students, 15 minutes before the session (EF-21, EF-50)."""
    if not s:
        return {"join_available": False, "join_url": None, "join_opens_at": None}
    minutes = int(site_settings.get(db, "join_button_minutes_before"))
    opens = s.starts_at - timedelta(minutes=minutes)
    now = utcnow()
    available = user.email_verified_at is not None and opens <= now <= s.ends_at and s.status != "cancelled"
    return {
        "join_available": available,
        "join_url": (s.meeting_url_override or c.meeting_url) if available else None,
        "join_opens_at": opens.isoformat(),
    }


def _my_enrollments(db, user: User) -> list[Enrollment]:
    return list(
        db.scalars(
            select(Enrollment)
            .where(Enrollment.user_id == user.id)
            .options(selectinload(Enrollment.cohort).selectinload(Cohort.bootcamp), selectinload(Enrollment.cohort).selectinload(Cohort.sessions))
            .order_by(Enrollment.created_at.desc())
        )
    )


@router.get("/enrollments")
def my_enrollments(user: CurrentUser, db: DB, loc: Locale) -> list[dict]:
    out = []
    now = utcnow()
    from app.models import CancellationRequest

    pending_cancel = set(
        db.scalars(select(CancellationRequest.enrollment_id).join(Enrollment).where(Enrollment.user_id == user.id, CancellationRequest.status == "pending"))
    )
    for e in _my_enrollments(db, user):
        c = e.cohort
        status = _enrollment_status(c, e)
        nxt = next((s for s in c.sessions if s.status != "cancelled" and s.ends_at >= now), None)
        out.append({
            "id": str(e.id),
            "status": status,
            "enrollment_status": e.status,
            "bootcamp": {"title": pick(c.bootcamp, "title", loc), "slug": getattr(c.bootcamp, f"slug_{loc}"), "image_url": c.bootcamp.image_url},
            "cohort": {"id": str(c.id), "code": c.code, "start_date": c.start_date.isoformat(), "end_date": c.end_date.isoformat(), "schedule_text": calendar.schedule_text(c, loc)},
            "next_session": {"id": str(nxt.id), "starts_at": nxt.starts_at.isoformat(), "ends_at": nxt.ends_at.isoformat(), "title": calendar.session_title(nxt, pick(c.bootcamp, "title", loc), loc), **_join_info(db, user, nxt, c)} if nxt and status != "cancelled" else None,
            "certificate_available": e.status == "completed",
            "cancellation_pending": e.id in pending_cancel,
            "can_request_cancellation": e.status == "confirmed" and status == "upcoming" and e.id not in pending_cancel,
        })
    return out


@router.get("/sessions")
def my_sessions(user: CurrentUser, db: DB, loc: Locale) -> dict:
    """EF-51: upcoming sessions in the student's time zone, with calendar links."""
    now = utcnow()
    items = []
    for e in _my_enrollments(db, user):
        if e.status not in ("confirmed", "completed"):
            continue
        c = e.cohort
        title = pick(c.bootcamp, "title", loc)
        for s in c.sessions:
            if s.ends_at < now:
                continue
            st = calendar.session_title(s, title, loc)
            items.append({
                "id": str(s.id), "cohort_code": c.code, "bootcamp_title": title, "title": st,
                "starts_at": s.starts_at.isoformat(), "ends_at": s.ends_at.isoformat(), "status": s.status,
                "google_calendar_url": calendar.google_calendar_url(s, st) if s.status != "cancelled" else None,
                **_join_info(db, user, s, c),
            })
    items.sort(key=lambda x: x["starts_at"])
    return {"time_zone": user.time_zone, "email_verified": user.email_verified_at is not None, "items": items}


@router.get("/sessions.ics")
def my_sessions_ics(user: CurrentUser, db: DB, loc: Locale) -> Response:
    events = []
    for e in _my_enrollments(db, user):
        if e.status not in ("confirmed", "completed"):
            continue
        title = pick(e.cohort.bootcamp, "title", loc)
        events += [calendar.session_event(s, title, loc, include_link=True) for s in e.cohort.sessions]
    return Response(calendar.build_ics(events), media_type="text/calendar; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="seances.ics"'})


def _own_enrollment(db, user: User, enrollment_id: uuid.UUID) -> Enrollment:
    e = db.get(Enrollment, enrollment_id)
    if not e or e.user_id != user.id:
        raise AppError("not_found", 404)
    return e


@router.get("/enrollments/{enrollment_id}/materials")
def materials(enrollment_id: uuid.UUID, user: CurrentUser, db: DB) -> list[dict]:
    """EF-52: files via short-lived signed links."""
    e = _own_enrollment(db, user, enrollment_id)
    if e.status not in ("confirmed", "completed"):
        raise AppError("forbidden", 403)
    now = utcnow()
    rows = db.scalars(select(Material).where(Material.cohort_id == e.cohort_id).order_by(Material.created_at.desc()))
    return [
        {
            "id": str(m.id), "title": m.title, "description": m.description, "type": m.type,
            "filename": m.filename, "size_bytes": m.size_bytes, "created_at": m.created_at.isoformat(),
            "url": storage.signed_download_url(m.storage_key) if m.type == "file" else m.url,
        }
        for m in rows
        if not m.visible_from or m.visible_from <= now
    ]


class CancellationIn(BaseModel):
    reason: str = Field(default="", max_length=2000)


@router.post("/enrollments/{enrollment_id}/cancellation-request", response_model=Ok)
def cancellation_request(enrollment_id: uuid.UUID, body: CancellationIn, user: CurrentUser, db: DB) -> Ok:
    e = _own_enrollment(db, user, enrollment_id)
    enr_svc.request_cancellation(db, user, e, body.reason)
    db.commit()
    return Ok()


@router.get("/enrollments/{enrollment_id}/certificate")
def certificate(enrollment_id: uuid.UUID, user: CurrentUser, db: DB, loc: Locale, lang: Loc | None = None) -> Response:
    e = _own_enrollment(db, user, enrollment_id)
    if e.status != "completed":
        raise AppError("not_completed", 404)
    data = pdf.certificate_pdf(db, e, lang or loc)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="attestation-{e.certificate_number}.pdf"'})


# --- Orders and invoices (EF-46, EF-53) -----------------------------------------------------------


def order_out(db, order: Order, loc: str) -> dict:
    db.refresh(order, attribute_names=["items"])
    return {
        "id": str(order.id),
        "number": order.number,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "subtotal": order.subtotal_cents,
        "multi_discount": order.multi_discount_cents,
        "promo_discount": order.promo_discount_cents,
        "promo_code": order.promo_code_text,
        "tax_lines": [{**t, "label": taxes.label(t["type"], loc)} for t in order.tax_lines],
        "tax": order.tax_cents,
        "total": order.total_cents,
        "refunded": order.refunded_cents,
        "failure_message": order.failure_message,
        "items": [
            {
                "title": it.title_fr if loc == "fr" else it.title_en, "cohort_code": it.cohort_code, "cohort_id": str(it.cohort_id),
                "start_date": it.cohort.start_date.isoformat(), "end_date": it.cohort.end_date.isoformat(),
                "unit_price": it.unit_price_cents, "discount": it.discount_cents, "net": it.net_cents, "tax": it.tax_cents, "total": it.total_cents,
            }
            for it in order.items
        ],
        "invoice_available": order.status in ("paid", "partially_refunded", "refunded"),
    }


@router.get("/orders")
def my_orders(user: CurrentUser, db: DB, loc: Locale) -> list[dict]:
    return [order_out(db, o, loc) for o in orders.user_orders(db, user.id)]


@router.get("/orders/{order_id}/invoice")
def invoice(order_id: uuid.UUID, user: CurrentUser, db: DB, loc: Locale, lang: Loc | None = None) -> Response:
    order = db.get(Order, order_id)
    if not order or order.user_id != user.id or order.status not in ("paid", "partially_refunded", "refunded"):
        raise AppError("not_found", 404)
    data = pdf.invoice_pdf(db, order, lang or loc)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="facture-{order.number}.pdf"'})
