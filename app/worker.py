"""Background worker: `python -m app.worker`

- processes the jobs queue (emails with PDF/ICS attachments), never blocking web requests;
- runs periodic tasks: expired checkouts, waitlist offers, reminders (7 days, 24 h, 1 h), scheduled material notices.
"""

import logging
import signal
import time
import traceback
import uuid
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import urls
from app.core.config import settings
from app.core.db import SessionLocal, utcnow
from app.core.i18n import fmt_date, fmt_datetime, pick
from app.models import ClassSession, Cohort, Enrollment, Job, Order
from app.services import calendar, emails, jobs, materials, notify, orders, pdf, waitlist

log = logging.getLogger("app.worker")
_running = True


def _stop(*_):
    global _running
    _running = False


# --- Attachments ---------------------------------------------------------------------------


def build_attachments(db: Session, specs: list[dict], locale: str) -> list[tuple[str, str, bytes]]:
    out = []
    for spec in specs:
        kind, oid = spec["kind"], uuid.UUID(spec["id"])
        if kind == "invoice":
            order = db.get(Order, oid)
            out.append((f"facture-{order.number}.pdf" if locale == "fr" else f"invoice-{order.number}.pdf", "application/pdf", pdf.invoice_pdf(db, order, locale)))
        elif kind == "ics_order":
            order = db.get(Order, oid)
            events = []
            for it in order.items:
                c = it.cohort
                title = pick(c.bootcamp, "title", locale)
                events += [calendar.session_event(s, title, locale, include_link=True) for s in c.sessions if s.status != "cancelled"]
            out.append(("seances.ics" if locale == "fr" else "sessions.ics", "text/calendar", calendar.build_ics(events)))
        elif kind == "ics_session":
            s = db.get(ClassSession, oid)
            title = pick(s.cohort.bootcamp, "title", locale)
            out.append(("seance.ics" if locale == "fr" else "session.ics", "text/calendar", calendar.build_ics([calendar.session_event(s, title, locale, include_link=True)])))
    return out


def run_job(db: Session, job: Job) -> None:
    if job.type == "send_email":
        p = job.payload
        attachments = build_attachments(db, p.get("attachments", []), p["locale"])
        emails.deliver(
            db, key=p["template"], to=p["to"], locale=p["locale"], context=p["context"],
            user_id=uuid.UUID(p["user_id"]) if p.get("user_id") else None, attachments=attachments, dedupe_key=p.get("dedupe_key"),
        )
    else:
        raise ValueError(f"unknown job type {job.type}")


def process_jobs(batch: int = 20) -> int:
    done = 0
    for _ in range(batch):
        with SessionLocal() as db:
            job = db.scalar(
                select(Job).where(Job.status == "queued", Job.run_at <= utcnow()).order_by(Job.run_at).limit(1).with_for_update(skip_locked=True)
            )
            if not job:
                return done
            job.status = "running"
            job.attempts += 1
            db.commit()
            try:
                run_job(db, job)
                job.status = "done"
                job.finished_at = utcnow()
                db.commit()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                job = db.get(Job, job.id)
                job.last_error = "".join(traceback.format_exception_only(exc))[-2000:]
                if job.attempts >= job.max_attempts:
                    job.status = "failed"
                    if job.payload.get("template") != "admin_alert":
                        notify.admin_alert(db, f"Échec d'envoi de courriel : {job.payload.get('template')}", f"<p>{job.payload.get('to')}</p><pre>{job.last_error}</pre>", "email-log")
                else:
                    job.status = "queued"
                    job.run_at = utcnow() + timedelta(seconds=30 * 2 ** job.attempts)
                db.commit()
                log.warning("job %s failed (attempt %s): %s", job.id, job.attempts, exc)
            done += 1
    return done


# --- Periodic tasks ------------------------------------------------------------------------


def send_cohort_prep(db: Session) -> None:
    """7 days before the start: preparation list, prerequisites, tools, first session."""
    today = utcnow().date()
    cohorts = db.scalars(
        select(Cohort).where(Cohort.prep_email_sent_at.is_(None), Cohort.status.in_(("open", "closed")), Cohort.start_date > today, Cohort.start_date <= today + timedelta(days=7))
    )
    for c in cohorts:
        c.prep_email_sent_at = utcnow()
        b = c.bootcamp
        first = next((s for s in c.sessions if s.status != "cancelled"), None)
        for e in db.scalars(select(Enrollment).where(Enrollment.cohort_id == c.id, Enrollment.status == "confirmed")):
            u = e.user
            loc = u.locale
            jobs.send_email(
                db, "cohort_prep", u.email, loc,
                {
                    "first_name": u.first_name, "bootcamp_title": pick(b, "title", loc), "cohort_start": fmt_date(c.start_date, loc),
                    "first_session": fmt_datetime(first.starts_at, loc, u.time_zone) if first else "—",
                    "preparation_html": pick(b, "preparation", loc) or "<p>—</p>", "prerequisites_html": pick(b, "prerequisites", loc) or "<p>—</p>",
                    "tools": ", ".join(b.tools) or "—", "dashboard_url": urls.front("dashboard", loc),
                },
                user_id=u.id, dedupe_key=f"prep:{c.id}:{u.id}",
            )


def send_session_reminders(db: Session) -> None:
    """24 hours and 1 hour before each session, in the student's time zone."""
    now = utcnow()
    for window, field, label in ((timedelta(hours=24), "reminder_24h_sent_at", ("demain", "tomorrow")), (timedelta(hours=1), "reminder_1h_sent_at", ("dans une heure", "in one hour"))):
        q = select(ClassSession).where(
            ClassSession.status != "cancelled", ClassSession.starts_at > now, ClassSession.starts_at <= now + window, getattr(ClassSession, field).is_(None)
        )
        for s in db.scalars(q):
            setattr(s, field, now)
            if field == "reminder_24h_sent_at" and s.starts_at - now < timedelta(hours=2):
                continue  # too late for the 24 h reminder; the 1 h one will go out
            c = s.cohort
            for e in db.scalars(select(Enrollment).where(Enrollment.cohort_id == c.id, Enrollment.status == "confirmed")):
                u = e.user
                if not (u.email_prefs or {}).get("reminders", True):
                    continue
                loc = u.locale
                title = pick(c.bootcamp, "title", loc)
                jobs.send_email(
                    db, "session_reminder", u.email, loc,
                    {"first_name": u.first_name, "bootcamp_title": title, "session_title": calendar.session_title(s, title, loc),
                     "session_time": fmt_datetime(s.starts_at, loc, u.time_zone), "when": label[0] if loc == "fr" else label[1],
                     "join_url": urls.front("dashboard", loc)},
                    user_id=u.id, dedupe_key=f"reminder:{field}:{s.id}:{u.id}:{s.sequence}",
                )


PERIODIC = [
    ("expire_orders", 60, orders.expire_stale_orders),
    ("waitlist_expire", 60, waitlist.expire_offers),
    ("waitlist_process", 120, waitlist.process_all),
    ("cohort_prep", 600, send_cohort_prep),
    ("session_reminders", 60, send_session_reminders),
    ("material_notices", 300, materials.notify_due),
]


def run_periodic(last_run: dict[str, float]) -> None:
    now = time.monotonic()
    for name, every, fn in PERIODIC:
        if now - last_run.get(name, 0) < every:
            continue
        last_run[name] = now
        with SessionLocal() as db:
            # advisory lock: only one worker runs a given periodic task at a time
            if not db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtext(:n))"), {"n": name}):
                continue
            try:
                fn(db)
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("periodic task %s failed", name)


def main() -> None:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    log.info("worker started (email backend: %s)", settings.email_backend)
    last: dict[str, float] = {}
    while _running:
        run_periodic(last)
        if process_jobs() == 0:
            time.sleep(settings.worker_poll_seconds)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
