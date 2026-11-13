"""Admin enrollment operations (EF-64): manual enrollment, transfer, completion, cancellation requests."""

import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import urls
from app.core.db import utcnow
from app.core.errors import AppError
from app.core.i18n import fmt_date, pick
from app.models import CancellationRequest, Cohort, Enrollment, Order, OrderItem, User
from app.services import audit, jobs, orders, seats, waitlist

FIELDS = ["status", "cohort_id", "source", "completed_at"]


def manual_enroll(db: Session, actor: User, user: User, cohort_id: uuid.UUID, *, source: str, note: str, override_capacity: bool, notify_student: bool, ip: str | None) -> Enrollment:
    cohort = seats.lock_cohorts(db, [cohort_id])[0]
    existing = db.scalar(select(Enrollment).where(Enrollment.user_id == user.id, Enrollment.cohort_id == cohort.id))
    if existing and existing.status in ("confirmed", "completed"):
        raise AppError("already_enrolled", 409)
    if not override_capacity and seats.remaining(db, cohort) <= 0:
        raise AppError("cohort_full", 409)
    if existing:
        existing.status, existing.source, existing.note, existing.cancelled_at = "confirmed", source, note, None
        enr = existing
    else:
        enr = Enrollment(user_id=user.id, cohort_id=cohort.id, status="confirmed", source=source, note=note)
        db.add(enr)
    db.flush()
    audit.log(db, actor.id, "enroll_manual", "enrollment", enr.id, None, {"user": user.email, "cohort": cohort.code, "source": source, "note": note}, ip)
    if notify_student:
        loc = user.locale
        jobs.send_email(
            db, "enrollment_manual", user.email, loc,
            {"first_name": user.first_name, "bootcamp_title": pick(cohort.bootcamp, "title", loc), "cohort_start": fmt_date(cohort.start_date, loc), "dashboard_url": urls.front("dashboard", loc)},
            user_id=user.id,
        )
    return enr


def transfer(db: Session, actor: User, enr: Enrollment, to_cohort_id: uuid.UUID, *, override_capacity: bool, ip: str | None) -> Enrollment:
    if enr.status != "confirmed":
        raise AppError("invalid_state", 409)
    if to_cohort_id == enr.cohort_id:
        raise AppError("invalid_state", 409)
    target = seats.lock_cohorts(db, [to_cohort_id, enr.cohort_id])
    target_cohort = next(c for c in target if c.id == to_cohort_id)
    if not override_capacity and seats.remaining(db, target_cohort) <= 0:
        raise AppError("cohort_full", 409)
    dup = db.scalar(select(Enrollment).where(Enrollment.user_id == enr.user_id, Enrollment.cohort_id == to_cohort_id))
    if dup and dup.status in ("confirmed", "completed"):
        raise AppError("already_enrolled", 409)
    if dup:
        dup.status, dup.source, dup.order_item_id, dup.cancelled_at = "confirmed", "transfer", enr.order_item_id, None
        new = dup
    else:
        new = Enrollment(user_id=enr.user_id, cohort_id=to_cohort_id, order_item_id=enr.order_item_id, status="confirmed", source="transfer")
        db.add(new)
    db.flush()
    old_cohort = enr.cohort_id
    enr.status = "transferred"
    enr.transferred_to_id = new.id
    audit.log(db, actor.id, "transfer", "enrollment", enr.id, {"cohort_id": old_cohort}, {"cohort_id": to_cohort_id, "new_enrollment": new.id}, ip)
    waitlist.trigger(db, old_cohort)
    return new


def mark_completed(db: Session, actor: User, enr: Enrollment, ip: str | None) -> Enrollment:
    if enr.status not in ("confirmed", "completed"):
        raise AppError("invalid_state", 409)
    if enr.status == "completed":
        return enr
    enr.status = "completed"
    enr.completed_at = utcnow()
    enr.certificate_number = f"{utcnow():%Y}-{secrets.token_hex(4).upper()}"
    audit.log(db, actor.id, "complete", "enrollment", enr.id, {"status": "confirmed"}, {"status": "completed"}, ip)
    user = enr.user
    loc = user.locale
    jobs.send_email(
        db, "certificate_ready", user.email, loc,
        {"first_name": user.first_name, "bootcamp_title": pick(enr.cohort.bootcamp, "title", loc), "dashboard_url": urls.front("dashboard", loc)},
        user_id=user.id, dedupe_key=f"certificate:{enr.id}",
    )
    return enr


def cancel(db: Session, actor: User, enr: Enrollment, ip: str | None) -> None:
    if enr.status != "confirmed":
        raise AppError("invalid_state", 409)
    enr.status = "cancelled"
    enr.cancelled_at = utcnow()
    audit.log(db, actor.id, "cancel", "enrollment", enr.id, {"status": "confirmed"}, {"status": "cancelled"}, ip)
    waitlist.trigger(db, enr.cohort_id)


def request_cancellation(db: Session, user: User, enr: Enrollment, reason: str) -> CancellationRequest:
    """EF-56: student asks to cancel; handled by an admin according to the refund policy."""
    if enr.user_id != user.id or enr.status != "confirmed":
        raise AppError("invalid_state", 409)
    pending = db.scalar(select(CancellationRequest).where(CancellationRequest.enrollment_id == enr.id, CancellationRequest.status == "pending"))
    if pending:
        raise AppError("request_exists", 409)
    req = CancellationRequest(enrollment_id=enr.id, reason=reason)
    db.add(req)
    db.flush()
    from app.services import notify

    notify.admin_alert(
        db, f"Demande d'annulation — {user.full_name}",
        f"<p>{enr.cohort.code} — {pick(enr.cohort.bootcamp, 'title', 'fr')}</p><p>{reason}</p>", "cancellations",
    )
    return req


def decide_cancellation(db: Session, actor: User, req: CancellationRequest, *, approve: bool, refund_cents: int, admin_note: str, ip: str | None) -> CancellationRequest:
    if req.status != "pending":
        raise AppError("invalid_state", 409)
    enr = req.enrollment
    req.status = "approved" if approve else "rejected"
    req.handled_by_id = actor.id
    req.handled_at = utcnow()
    req.admin_note = admin_note
    if approve:
        refund = None
        if refund_cents > 0 and enr.order_item_id:
            item = db.get(OrderItem, enr.order_item_id)
            order = db.get(Order, item.order_id)
            db.refresh(order, attribute_names=["items", "payments"])
            refund = orders.refund_order(db, order, refund_cents, reason=f"cancellation: {req.reason}", actor=actor, item_ids=[item.id], cancel_enrollments=True, ip=ip)
            req.refund_id = refund.id
        if enr.status == "confirmed":
            cancel(db, actor, enr, ip)
    user = enr.user
    loc = user.locale
    decision = ("acceptée" if approve else "refusée") if loc == "fr" else ("approved" if approve else "declined")
    jobs.send_email(
        db, "cancellation_decision", user.email, loc,
        {"first_name": user.first_name, "bootcamp_title": pick(enr.cohort.bootcamp, "title", loc), "decision": decision, "admin_note": admin_note},
        user_id=user.id,
    )
    return req


def cohort_of(db: Session, cohort_id: uuid.UUID) -> Cohort:
    c = db.get(Cohort, cohort_id)
    if not c:
        raise AppError("not_found", 404)
    return c
