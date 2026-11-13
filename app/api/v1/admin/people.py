"""Admin: students (EF-64), staff and roles (EF-70), enrollments, cancellations (EF-56), waitlists (EF-66),
privacy requests (EF-89), incident register (EF-91), contact messages."""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import DB, AdminUser
from app.api.v1.admin.common import dt, paginate
from app.core import urls
from app.core.db import utcnow
from app.core.errors import AppError
from app.core.html import clean
from app.core.security import client_ip
from app.models import (
    CancellationRequest,
    ContactMessage,
    Enrollment,
    Order,
    PrivacyIncident,
    PrivacyRequest,
    User,
    WaitlistEntry,
)
from app.schemas.common import Ok
from app.services import accounts, audit, jobs, seats, waitlist
from app.services import enrollments as enr_svc

router = APIRouter(tags=["admin"])


def user_brief(u: User) -> dict:
    return {
        "id": str(u.id), "email": u.email, "first_name": u.first_name, "last_name": u.last_name, "role": u.role,
        "locale": u.locale, "email_verified": u.email_verified_at is not None, "totp_enabled": u.totp_enabled,
        "created_at": dt(u.created_at), "last_login_at": dt(u.last_login_at), "deleted": u.deleted_at is not None,
    }


@router.get("/admin/users")
def list_users(db: DB, _: AdminUser, q: str | None = None, role: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, le=100)) -> dict:
    query = select(User).order_by(User.created_at.desc())
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(User.email.ilike(like), User.first_name.ilike(like), User.last_name.ilike(like)))
    if role:
        query = query.where(User.role == role)
    return paginate(db, query, page, page_size, user_brief)


def enrollment_out(e: Enrollment) -> dict:
    c = e.cohort
    return {
        "id": str(e.id), "user_id": str(e.user_id), "cohort_id": str(c.id), "cohort_code": c.code, "bootcamp_title": c.bootcamp.title_fr,
        "start_date": c.start_date.isoformat(), "status": e.status, "source": e.source, "note": e.note,
        "completed_at": dt(e.completed_at), "certificate_number": e.certificate_number, "created_at": dt(e.created_at),
    }


@router.get("/admin/users/{uid}")
def get_user(uid: uuid.UUID, db: DB, _: AdminUser) -> dict:
    u = db.get(User, uid)
    if not u:
        raise AppError("not_found", 404)
    enrollments = db.scalars(select(Enrollment).where(Enrollment.user_id == u.id).order_by(Enrollment.created_at.desc()))
    orders = db.scalars(select(Order).where(Order.user_id == u.id).order_by(Order.created_at.desc()))
    return {
        **user_brief(u), "phone": u.phone, "time_zone": u.time_zone, "marketing_opt_in": u.marketing_opt_in,
        "address": accounts.address_dict(u.address), "google_linked": bool(u.google_sub),
        "bio_fr": u.bio_fr, "bio_en": u.bio_en, "title_fr": u.title_fr, "title_en": u.title_en, "photo_url": u.photo_url, "show_on_about": u.show_on_about,
        "enrollments": [enrollment_out(e) for e in enrollments],
        "orders": [{"id": str(o.id), "number": o.number, "status": o.status, "total": o.total_cents, "refunded": o.refunded_cents, "created_at": dt(o.created_at)} for o in orders],
    }


class StaffProfileIn(BaseModel):
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    title_fr: str | None = Field(default=None, max_length=200)
    title_en: str | None = Field(default=None, max_length=200)
    bio_fr: str | None = None
    bio_en: str | None = None
    photo_url: str | None = Field(default=None, max_length=500)
    show_on_about: bool | None = None


@router.patch("/admin/users/{uid}")
def update_user(uid: uuid.UUID, body: StaffProfileIn, db: DB, admin: AdminUser) -> dict:
    u = db.get(User, uid)
    if not u:
        raise AppError("not_found", 404)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(u, k, clean(v) if k in ("bio_fr", "bio_en") else v)
    db.commit()
    return get_user(uid, db, admin)


class RoleIn(BaseModel):
    role: Literal["student", "instructor", "admin"]


@router.post("/admin/users/{uid}/role")
def set_role(uid: uuid.UUID, body: RoleIn, request: Request, db: DB, admin: AdminUser) -> dict:
    u = db.get(User, uid)
    if not u:
        raise AppError("not_found", 404)
    if u.id == admin.id and body.role != "admin":
        raise AppError("invalid_state", 409)  # don't lock yourself out
    before = {"role": u.role}
    u.role = body.role
    accounts.revoke_all(db, u)  # new permissions on next sign-in
    audit.log(db, admin.id, "role_change", "user", u.id, before, {"role": u.role}, client_ip(request))
    db.commit()
    return user_brief(u)


class InviteIn(BaseModel):
    email: EmailStr
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(default="", max_length=100)
    role: Literal["instructor", "admin"]
    locale: Literal["fr", "en"] = "fr"


@router.post("/admin/users/invite", status_code=201)
def invite(body: InviteIn, request: Request, db: DB, admin: AdminUser) -> dict:
    """EF-70: invite admins and instructors (they set a password, then must enable TOTP)."""
    u = accounts.get_by_email(db, body.email)
    if u:
        before = {"role": u.role}
        u.role = body.role
        audit.log(db, admin.id, "role_change", "user", u.id, before, {"role": u.role}, client_ip(request))
    else:
        u = accounts.create_user(db, email=body.email, password=None, first_name=body.first_name, last_name=body.last_name, locale=body.locale, role=body.role)
        audit.log(db, admin.id, "invite", "user", u.id, None, {"role": body.role, "email": body.email}, client_ip(request))
    token = accounts.issue_token(db, u, "invite", 7 * 24 * 60)
    role_label = {"instructor": ("formateur", "instructor"), "admin": ("administrateur", "administrator")}[body.role]
    jobs.send_email(
        db, "staff_invite", u.email, u.locale,
        {"first_name": u.first_name, "role": role_label[0] if u.locale == "fr" else role_label[1], "invite_url": urls.front("invitation", u.locale, query=f"token={token}")},
        user_id=u.id,
    )
    db.commit()
    return user_brief(u)


@router.post("/admin/users/{uid}/reset-mfa", response_model=Ok)
def reset_mfa(uid: uuid.UUID, request: Request, db: DB, admin: AdminUser) -> Ok:
    u = db.get(User, uid)
    if not u:
        raise AppError("not_found", 404)
    u.totp_enabled = False
    u.totp_secret = None
    accounts.revoke_all(db, u)
    audit.log(db, admin.id, "reset_mfa", "user", u.id, None, None, client_ip(request))
    db.commit()
    return Ok()


# --- Enrollments ------------------------------------------------------------------------------


@router.get("/admin/enrollments")
def list_enrollments(db: DB, _: AdminUser, cohort_id: uuid.UUID | None = None, status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, le=200)) -> dict:
    q = select(Enrollment).options(selectinload(Enrollment.user), selectinload(Enrollment.cohort)).order_by(Enrollment.created_at.desc())
    if cohort_id:
        q = q.where(Enrollment.cohort_id == cohort_id)
    if status:
        q = q.where(Enrollment.status == status)
    return paginate(db, q, page, page_size, lambda e: {**enrollment_out(e), "student": {"name": e.user.full_name, "email": e.user.email}})


class ManualEnrollIn(BaseModel):
    user_id: uuid.UUID | None = None
    email: EmailStr | None = None
    first_name: str = Field(default="", max_length=100)
    last_name: str = Field(default="", max_length=100)
    cohort_id: uuid.UUID
    source: Literal["manual", "free"] = "manual"
    note: str = Field(default="", max_length=1000)
    override_capacity: bool = False
    notify: bool = True


@router.post("/admin/enrollments", status_code=201)
def manual_enroll(body: ManualEnrollIn, request: Request, db: DB, admin: AdminUser) -> dict:
    """Manual enrollment, e.g. paid by bank transfer or a free seat."""
    user = db.get(User, body.user_id) if body.user_id else (accounts.get_by_email(db, body.email) if body.email else None)
    if not user:
        if not body.email:
            raise AppError("validation_error", 422, details=[{"field": "email", "message": "required"}])
        user = accounts.create_user(db, email=body.email, password=None, first_name=body.first_name, last_name=body.last_name, locale="fr")
        token = accounts.issue_token(db, user, "reset_password", 7 * 24 * 60)
        jobs.send_email(db, "password_reset", user.email, user.locale, {"first_name": user.first_name, "reset_url": urls.front("reset_password", user.locale, query=f"token={token}")}, user_id=user.id)
    e = enr_svc.manual_enroll(db, admin, user, body.cohort_id, source=body.source, note=body.note, override_capacity=body.override_capacity, notify_student=body.notify, ip=client_ip(request))
    db.commit()
    db.refresh(e)
    return enrollment_out(e)


class TransferIn(BaseModel):
    to_cohort_id: uuid.UUID
    override_capacity: bool = False


@router.post("/admin/enrollments/{eid}/transfer")
def transfer(eid: uuid.UUID, body: TransferIn, request: Request, db: DB, admin: AdminUser) -> dict:
    e = db.get(Enrollment, eid)
    if not e:
        raise AppError("not_found", 404)
    new = enr_svc.transfer(db, admin, e, body.to_cohort_id, override_capacity=body.override_capacity, ip=client_ip(request))
    db.commit()
    db.refresh(new)
    return enrollment_out(new)


@router.post("/admin/enrollments/{eid}/complete")
def complete(eid: uuid.UUID, request: Request, db: DB, admin: AdminUser) -> dict:
    e = db.get(Enrollment, eid)
    if not e:
        raise AppError("not_found", 404)
    enr_svc.mark_completed(db, admin, e, client_ip(request))
    db.commit()
    return enrollment_out(e)


@router.post("/admin/enrollments/{eid}/cancel")
def cancel(eid: uuid.UUID, request: Request, db: DB, admin: AdminUser) -> dict:
    e = db.get(Enrollment, eid)
    if not e:
        raise AppError("not_found", 404)
    enr_svc.cancel(db, admin, e, client_ip(request))
    db.commit()
    return enrollment_out(e)


# --- Cancellation requests (EF-56) -----------------------------------------------------------


@router.get("/admin/cancellation-requests")
def list_cancellations(db: DB, _: AdminUser, status: str | None = "pending") -> list[dict]:
    q = select(CancellationRequest).order_by(CancellationRequest.created_at.desc())
    if status:
        q = q.where(CancellationRequest.status == status)
    out = []
    for r in db.scalars(q):
        e = r.enrollment
        paid = None
        if e.order_item_id:
            from app.models import OrderItem

            it = db.get(OrderItem, e.order_item_id)
            paid = {"order_id": str(it.order_id), "item_total": it.total_cents, "item_refunded": it.refunded_cents}
        out.append({
            "id": str(r.id), "status": r.status, "reason": r.reason, "admin_note": r.admin_note, "created_at": dt(r.created_at),
            "handled_at": dt(r.handled_at), "enrollment": enrollment_out(e), "student": {"name": e.user.full_name, "email": e.user.email}, "payment": paid,
        })
    return out


class DecisionIn(BaseModel):
    approve: bool
    refund_cents: int = Field(default=0, ge=0)
    admin_note: str = Field(default="", max_length=2000)


@router.post("/admin/cancellation-requests/{rid}/decide", response_model=Ok)
def decide(rid: uuid.UUID, body: DecisionIn, request: Request, db: DB, admin: AdminUser) -> Ok:
    r = db.get(CancellationRequest, rid)
    if not r:
        raise AppError("not_found", 404)
    enr_svc.decide_cancellation(db, admin, r, approve=body.approve, refund_cents=body.refund_cents, admin_note=body.admin_note, ip=client_ip(request))
    db.commit()
    return Ok()


# --- Waitlists (EF-66) ----------------------------------------------------------------------------


def entry_out(e: WaitlistEntry) -> dict:
    return {
        "id": str(e.id), "email": e.email, "first_name": e.first_name, "locale": e.locale, "position": e.position, "status": e.status,
        "offered_at": dt(e.offered_at), "offer_expires_at": dt(e.offer_expires_at), "created_at": dt(e.created_at), "user_id": str(e.user_id) if e.user_id else None,
    }


@router.get("/admin/cohorts/{cid}/waitlist")
def get_waitlist(cid: uuid.UUID, db: DB, _: AdminUser) -> dict:
    entries = db.scalars(select(WaitlistEntry).where(WaitlistEntry.cohort_id == cid).order_by(WaitlistEntry.position))
    from app.models import Cohort

    c = db.get(Cohort, cid)
    if not c:
        raise AppError("not_found", 404)
    return {"seats_left": seats.remaining(db, c), "entries": [entry_out(e) for e in entries]}


class ReorderIn(BaseModel):
    entry_ids: list[uuid.UUID]


@router.post("/admin/cohorts/{cid}/waitlist/reorder", response_model=Ok)
def reorder_waitlist(cid: uuid.UUID, body: ReorderIn, db: DB, _: AdminUser) -> Ok:
    waitlist.reorder(db, cid, body.entry_ids)
    db.commit()
    return Ok()


@router.post("/admin/waitlist/{eid}/offer", response_model=Ok)
def manual_offer(eid: uuid.UUID, db: DB, _: AdminUser) -> Ok:
    e = db.get(WaitlistEntry, eid)
    if not e or e.status not in ("waiting", "expired"):
        raise AppError("invalid_state", 409)
    c = seats.lock_cohorts(db, [e.cohort_id])[0]
    if seats.remaining(db, c) <= 0:
        raise AppError("cohort_full", 409)
    waitlist.offer(db, e)
    db.commit()
    return Ok()


@router.delete("/admin/waitlist/{eid}", response_model=Ok)
def remove_entry(eid: uuid.UUID, db: DB, _: AdminUser) -> Ok:
    e = db.get(WaitlistEntry, eid)
    if not e:
        raise AppError("not_found", 404)
    e.status = "removed"
    from app.models import SeatHold

    for h in db.scalars(select(SeatHold).where(SeatHold.waitlist_entry_id == e.id, SeatHold.released_at.is_(None))):
        h.released_at = utcnow()
    db.commit()
    return Ok()


# --- Privacy requests (EF-89) and incidents (EF-91) -------------------------------------------------


@router.get("/admin/privacy-requests")
def list_privacy(db: DB, _: AdminUser, status: str | None = None) -> list[dict]:
    q = select(PrivacyRequest).order_by(PrivacyRequest.created_at.desc())
    if status:
        q = q.where(PrivacyRequest.status == status)
    return [
        {"id": str(r.id), "user_id": str(r.user_id) if r.user_id else None, "email": r.email, "type": r.type, "details": r.details,
         "status": r.status, "admin_note": r.admin_note, "created_at": dt(r.created_at), "handled_at": dt(r.handled_at)}
        for r in db.scalars(q)
    ]


class PrivacyDecisionIn(BaseModel):
    action: Literal["complete", "reject"]
    admin_note: str = Field(default="", max_length=2000)


@router.post("/admin/privacy-requests/{rid}", response_model=Ok)
def handle_privacy(rid: uuid.UUID, body: PrivacyDecisionIn, request: Request, db: DB, admin: AdminUser) -> Ok:
    r = db.get(PrivacyRequest, rid)
    if not r or r.status != "pending":
        raise AppError("invalid_state", 409)
    if body.action == "complete" and r.type == "deletion" and r.user_id:
        user = db.get(User, r.user_id)
        if user and not user.deleted_at:
            accounts.anonymize(db, user)
        r.email = "anonymized"
    r.status = "completed" if body.action == "complete" else "rejected"
    r.admin_note = body.admin_note
    r.handled_by_id = admin.id
    r.handled_at = utcnow()
    audit.log(db, admin.id, f"privacy_{body.action}", "privacy_request", r.id, None, {"type": r.type}, client_ip(request))
    db.commit()
    return Ok()


class IncidentIn(BaseModel):
    occurred_at: datetime | None = None
    discovered_at: datetime
    description: str = Field(min_length=1)
    data_involved: str = ""
    people_affected: int | None = None
    risk_of_serious_harm: bool = False
    measures_taken: str = ""
    cai_notified_at: datetime | None = None
    people_notified_at: datetime | None = None
    status: Literal["open", "closed"] = "open"


def incident_out(i: PrivacyIncident) -> dict:
    return {c.key: (dt(getattr(i, c.key)) if isinstance(getattr(i, c.key), datetime) else (str(getattr(i, c.key)) if isinstance(getattr(i, c.key), uuid.UUID) else getattr(i, c.key))) for c in PrivacyIncident.__table__.columns}


@router.get("/admin/privacy-incidents")
def list_incidents(db: DB, _: AdminUser) -> list[dict]:
    return [incident_out(i) for i in db.scalars(select(PrivacyIncident).order_by(PrivacyIncident.discovered_at.desc()))]


@router.post("/admin/privacy-incidents", status_code=201)
def create_incident(body: IncidentIn, db: DB, admin: AdminUser) -> dict:
    i = PrivacyIncident(**body.model_dump(), created_by_id=admin.id)
    db.add(i)
    db.commit()
    return incident_out(i)


@router.put("/admin/privacy-incidents/{iid}")
def update_incident(iid: uuid.UUID, body: IncidentIn, db: DB, _: AdminUser) -> dict:
    i = db.get(PrivacyIncident, iid)
    if not i:
        raise AppError("not_found", 404)
    for k, v in body.model_dump().items():
        setattr(i, k, v)
    db.commit()
    return incident_out(i)


# --- Contact messages ------------------------------------------------------------------------------


@router.get("/admin/messages")
def list_messages(db: DB, _: AdminUser, page: int = Query(1, ge=1), page_size: int = Query(25, le=100)) -> dict:
    q = select(ContactMessage).order_by(ContactMessage.created_at.desc())
    return paginate(db, q, page, page_size, lambda m: {"id": str(m.id), "name": m.name, "email": m.email, "subject": m.subject, "message": m.message, "locale": m.locale, "handled": m.handled, "created_at": dt(m.created_at)})


@router.post("/admin/messages/{mid}/handled", response_model=Ok)
def message_handled(mid: uuid.UUID, db: DB, _: AdminUser) -> Ok:
    m = db.get(ContactMessage, mid)
    if not m:
        raise AppError("not_found", 404)
    m.handled = True
    db.commit()
    return Ok()
