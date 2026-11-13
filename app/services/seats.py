"""Seat accounting. Remaining = capacity - confirmed enrollments - active holds (section 10)."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import Cohort, Enrollment, SeatHold
from app.services import site_settings

ACTIVE_ENROLLMENT = ("confirmed", "completed")


def _active_hold_filter():
    return (
        SeatHold.released_at.is_(None),
        SeatHold.converted_at.is_(None),
        SeatHold.expires_at > utcnow(),
    )


def confirmed_count(db: Session, cohort_id: uuid.UUID) -> int:
    return db.scalar(
        select(func.count()).select_from(Enrollment).where(
            Enrollment.cohort_id == cohort_id, Enrollment.status.in_(ACTIVE_ENROLLMENT)
        )
    ) or 0


def active_holds_count(db: Session, cohort_id: uuid.UUID, exclude_user_id: uuid.UUID | None = None) -> int:
    q = select(func.count()).select_from(SeatHold).where(SeatHold.cohort_id == cohort_id, *_active_hold_filter())
    if exclude_user_id:
        q = q.where((SeatHold.user_id.is_(None)) | (SeatHold.user_id != exclude_user_id))
    return db.scalar(q) or 0


def remaining(db: Session, cohort: Cohort, exclude_user_id: uuid.UUID | None = None) -> int:
    return max(0, cohort.capacity - confirmed_count(db, cohort.id) - active_holds_count(db, cohort.id, exclude_user_id))


def counts_for(db: Session, cohort_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
    """Bulk (confirmed, holds) per cohort, for listings."""
    if not cohort_ids:
        return {}
    conf = dict(
        db.execute(
            select(Enrollment.cohort_id, func.count())
            .where(Enrollment.cohort_id.in_(cohort_ids), Enrollment.status.in_(ACTIVE_ENROLLMENT))
            .group_by(Enrollment.cohort_id)
        ).all()
    )
    holds = dict(
        db.execute(
            select(SeatHold.cohort_id, func.count())
            .where(SeatHold.cohort_id.in_(cohort_ids), *_active_hold_filter())
            .group_by(SeatHold.cohort_id)
        ).all()
    )
    return {cid: (conf.get(cid, 0), holds.get(cid, 0)) for cid in cohort_ids}


def seats_left_bulk(db: Session, cohorts: list[Cohort]) -> dict[uuid.UUID, int]:
    counts = counts_for(db, [c.id for c in cohorts])
    return {c.id: max(0, c.capacity - counts[c.id][0] - counts[c.id][1]) for c in cohorts}


def registration_open(cohort: Cohort, now: datetime | None = None) -> bool:
    """EF-13: registrations close automatically at the cohort's deadline."""
    return cohort.status == "open" and (now or utcnow()) < cohort.registration_deadline


def display_status(cohort: Cohort, seats_left: int, few_threshold: int) -> str:
    """open | few_seats | full | closed (EF-11)."""
    if not registration_open(cohort):
        return "closed"
    if seats_left <= 0:
        return "full"
    if seats_left <= few_threshold:
        return "few_seats"
    return "open"


def current_price(cohort: Cohort) -> tuple[int, bool]:
    """(price in cents, is_early_bird)."""
    if cohort.early_bird_price_cents is not None and cohort.early_bird_until and utcnow() <= cohort.early_bird_until:
        return cohort.early_bird_price_cents, True
    return cohort.price_cents, False


def lock_cohorts(db: Session, cohort_ids: list[uuid.UUID]) -> list[Cohort]:
    """Row-lock cohorts in a stable order to serialize seat decisions (no overbooking) and avoid deadlocks."""
    ids = sorted(set(cohort_ids), key=str)
    return list(db.scalars(select(Cohort).where(Cohort.id.in_(ids)).order_by(Cohort.id).with_for_update()))


def hold_expiry(db: Session) -> datetime:
    return utcnow() + timedelta(minutes=int(site_settings.get(db, "seat_hold_minutes")))
