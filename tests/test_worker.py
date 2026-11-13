from datetime import timedelta

from sqlalchemy import select

from app import worker
from app.core.db import SessionLocal, utcnow
from app.models import ClassSession, EmailLog, Job, User
from app.services import emails
from tests.conftest import checkout, csrf_headers, make_bootcamp, pay, signup


def test_worker_sends_confirmation_with_pdf_and_ics(client, db, monkeypatch):
    sent = []
    monkeypatch.setattr(emails, "_send", lambda msg: sent.append(msg))
    c1 = make_bootcamp(db)
    signup(client, locale="en")
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers=csrf_headers(client))
    pay(client, checkout(client, locale="en").json()["order_id"], "cs_test_1")
    while worker.process_jobs():
        pass
    db.expire_all()
    assert all(j.status == "done" for j in db.scalars(select(Job))), [j.last_error for j in db.scalars(select(Job))]
    confirmation = next(m for m in sent if "Enrollment confirmed" in m["Subject"])
    names = [p.get_filename() for p in confirmation.iter_attachments()]
    assert names == [f"invoice-{names[0].split('invoice-')[1]}", "sessions.ics"]
    pdf_part = next(p for p in confirmation.iter_attachments() if p.get_filename().endswith(".pdf"))
    assert pdf_part.get_content()[:4] == b"%PDF"
    ics = next(p for p in confirmation.iter_attachments() if p.get_filename().endswith(".ics")).get_content()
    assert "BEGIN:VEVENT" in (ics.decode() if isinstance(ics, bytes) else ics)
    # plain-text alternative exists
    assert confirmation.get_body(("plain",)) is not None
    log = db.scalars(select(EmailLog).where(EmailLog.template == "order_confirmed")).all()
    assert len(log) == 1 and log[0].status == "sent" and log[0].locale == "en"


def test_dedupe_prevents_second_email(db, monkeypatch):
    sent = []
    monkeypatch.setattr(emails, "_send", lambda msg: sent.append(msg))
    with SessionLocal() as s:
        for _ in range(2):
            emails.deliver(s, key="contact_autoreply", to="x@example.com", locale="fr", context={"name": "X", "subject": "S"}, dedupe_key="k1")
        s.commit()
    assert len(sent) == 1


def test_session_reminders_24h_and_1h(client, db, monkeypatch):
    c1 = make_bootcamp(db, start_in_days=2)
    signup(client)
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers=csrf_headers(client))
    pay(client, checkout(client).json()["order_id"], "cs_test_1")
    s = db.scalar(select(ClassSession).order_by(ClassSession.starts_at))
    s.starts_at = utcnow() + timedelta(hours=20)
    s.ends_at = s.starts_at + timedelta(hours=2)
    db.commit()
    with SessionLocal() as sdb:
        worker.send_session_reminders(sdb)
        sdb.commit()
    templates = [j.payload["template"] for j in db.scalars(select(Job))]
    assert templates.count("session_reminder") == 1
    # running again does not resend
    with SessionLocal() as sdb:
        worker.send_session_reminders(sdb)
        sdb.commit()
    assert [j.payload["template"] for j in db.scalars(select(Job))].count("session_reminder") == 1
    # opt-out respected
    u = db.scalar(select(User).where(User.email == "etudiant@example.com"))
    u.email_prefs = {"reminders": False}
    db.commit()


def test_cohort_prep_email_7_days_before(client, db):
    c1 = make_bootcamp(db, start_in_days=5)
    signup(client)
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers=csrf_headers(client))
    pay(client, checkout(client).json()["order_id"], "cs_test_1")
    with SessionLocal() as sdb:
        worker.send_cohort_prep(sdb)
        sdb.commit()
    assert [j.payload["template"] for j in db.scalars(select(Job))].count("cohort_prep") == 1
