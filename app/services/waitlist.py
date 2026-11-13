"""Waitlist for full cohorts (EF-12, EF-49, EF-66).

When a seat frees up, the first waiting person gets a seat hold valid 48 h and an email with a
reserved payment link; when the offer expires the next person is offered, and so on.
"""

import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import urls
from app.core.db import utcnow
from app.core.errors import AppError
from app.core.i18n import fmt_date, fmt_datetime, norm_locale, pick
from app.core.security import hash_token, new_token
from app.models import Cohort, SeatHold, User, WaitlistEntry
from app.services import jobs, seats, site_settings


def join(db: Session, cohort: Cohort, *, email: str, first_name: str, locale: str, user: User | None) -> WaitlistEntry:
    email = email.strip().lower()
    if cohort.bootcamp.status != "published" or not seats.registration_open(cohort):
        raise AppError("cohort_unavailable", 409)
    if seats.remaining(db, cohort) > 0 and not _has_queue(db, cohort.id):
        raise AppError("waitlist_not_full", 409)
    existing = db.scalar(select(WaitlistEntry).where(WaitlistEntry.cohort_id == cohort.id, WaitlistEntry.email == email))
    if existing and existing.status in ("waiting", "offered"):
        raise AppError("waitlist_already", 409)
    pos = (db.scalar(select(func.max(WaitlistEntry.position)).where(WaitlistEntry.cohort_id == cohort.id)) or 0) + 1
    if existing:
        existing.status, existing.position, existing.first_name, existing.locale = "waiting", pos, first_name, norm_locale(locale)
        entry = existing
    else:
        entry = WaitlistEntry(
            cohort_id=cohort.id, user_id=user.id if user else None, email=email,
            first_name=first_name, locale=norm_locale(locale), position=pos,
        )
        db.add(entry)
    db.flush()
    loc = entry.locale
    jobs.send_email(
        db, "waitlist_joined", email, loc,
        {
            "first_name": first_name,
            "bootcamp_title": pick(cohort.bootcamp, "title", loc),
            "cohort_start": fmt_date(cohort.start_date, loc),
            "position": waiting_rank(db, entry),
        },
        user_id=user.id if user else None,
    )
    return entry


def _has_queue(db: Session, cohort_id: uuid.UUID) -> bool:
    return db.scalar(
        select(WaitlistEntry.id).where(WaitlistEntry.cohort_id == cohort_id, WaitlistEntry.status.in_(("waiting", "offered"))).limit(1)
    ) is not None


def waiting_rank(db: Session, entry: WaitlistEntry) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(WaitlistEntry).where(
                WaitlistEntry.cohort_id == entry.cohort_id,
                WaitlistEntry.status == "waiting",
                WaitlistEntry.position <= entry.position,
            )
        )
        or 1
    )


def offer(db: Session, entry: WaitlistEntry) -> None:
    hours = int(site_settings.get(db, "waitlist_offer_hours"))
    token = new_token()
    now = utcnow()
    entry.status = "offered"
    entry.offered_at = now
    entry.offer_expires_at = now + timedelta(hours=hours)
    entry.offer_token_hash = hash_token(token)
    db.add(SeatHold(cohort_id=entry.cohort_id, user_id=entry.user_id, waitlist_entry_id=entry.id, expires_at=entry.offer_expires_at))
    cohort = db.get(Cohort, entry.cohort_id)
    loc = entry.locale
    user = db.get(User, entry.user_id) if entry.user_id else None
    tz = user.time_zone if user else "America/Toronto"
    jobs.send_email(
        db, "waitlist_offer", entry.email, loc,
        {
            "first_name": entry.first_name,
            "bootcamp_title": pick(cohort.bootcamp, "title", loc),
            "cohort_start": fmt_date(cohort.start_date, loc),
            "offer_url": urls.front("waitlist_offer", loc, suffix=token),
            "expires_at": fmt_datetime(entry.offer_expires_at, loc, tz),
        },
        user_id=entry.user_id,
    )
    db.flush()


def trigger(db: Session, cohort_id: uuid.UUID) -> int:
    """Offer freed seats to the next people in line. Returns the number of offers sent."""
    cohort = seats.lock_cohorts(db, [cohort_id])[0]
    if not seats.registration_open(cohort):
        return 0
    sent = 0
    while seats.remaining(db, cohort) > 0:
        nxt = db.scalar(
            select(WaitlistEntry)
            .where(WaitlistEntry.cohort_id == cohort_id, WaitlistEntry.status == "waiting")
            .order_by(WaitlistEntry.position)
            .limit(1)
        )
        if not nxt:
            break
        offer(db, nxt)
        sent += 1
    return sent


def expire_offers(db: Session) -> int:
    """Worker: expired offers release their seat, then the next person is offered."""
    now = utcnow()
    expired = list(
        db.scalars(select(WaitlistEntry).where(WaitlistEntry.status == "offered", WaitlistEntry.offer_expires_at < now))
    )
    cohorts = set()
    for e in expired:
        e.status = "expired"
        for h in db.scalars(select(SeatHold).where(SeatHold.waitlist_entry_id == e.id, SeatHold.released_at.is_(None), SeatHold.converted_at.is_(None))):
            h.released_at = now
        cohorts.add(e.cohort_id)
    db.flush()
    for cid in cohorts:
        trigger(db, cid)
    return len(expired)


def process_all(db: Session) -> None:
    """Worker: offer seats freed by expired checkouts, cancellations or capacity changes."""
    cohort_ids = set(db.scalars(select(WaitlistEntry.cohort_id).where(WaitlistEntry.status == "waiting").distinct()))
    for cid in cohort_ids:
        trigger(db, cid)


def find_offer(db: Session, token: str) -> WaitlistEntry:
    entry = db.scalar(select(WaitlistEntry).where(WaitlistEntry.offer_token_hash == hash_token(token)))
    if not entry or entry.status != "offered" or not entry.offer_expires_at or entry.offer_expires_at < utcnow():
        raise AppError("offer_invalid", 410)
    return entry


def reorder(db: Session, cohort_id: uuid.UUID, entry_ids: list[uuid.UUID]) -> None:
    entries = {e.id: e for e in db.scalars(select(WaitlistEntry).where(WaitlistEntry.cohort_id == cohort_id))}
    for i, eid in enumerate(entry_ids, start=1):
        if eid in entries:
            entries[eid].position = i
    db.flush()
