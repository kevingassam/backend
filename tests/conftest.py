import hashlib
import hmac
import json
import os
import time
from datetime import timedelta

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://bootcamps:bootcamps@localhost:5432/bootcamps_test")
os.environ["EMAIL_BACKEND"] = "none"
os.environ["STRIPE_SECRET_KEY"] = "sk_test_dummy"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_secret"
os.environ["ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.models  # noqa: E402,F401
from app.core.db import Base, SessionLocal, engine, utcnow  # noqa: E402
from app.core.security import hash_password, limiter  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Bootcamp, Category, Cohort, SyllabusItem, User  # noqa: E402
from app.services import calendar, stripe_service  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def schema():
    with engine.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.execute(text("DROP SEQUENCE IF EXISTS order_number_seq"))
        conn.execute(text("CREATE SEQUENCE order_number_seq START 1"))
        Base.metadata.create_all(conn)
    yield


@pytest.fixture(autouse=True)
def clean_db():
    limiter.reset()
    with engine.begin() as conn:
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


class FakeStripe:
    """Records Stripe calls instead of hitting the network."""

    def __init__(self):
        self.sessions: list[dict] = []
        self.refunds: list[dict] = []
        self.expired: list[str] = []

    def create_checkout_session(self, params, idempotency_key):
        sid = f"cs_test_{len(self.sessions) + 1}"
        self.sessions.append({"id": sid, "params": params, "key": idempotency_key})
        return {"id": sid, "url": f"https://checkout.stripe.test/{sid}"}

    def create_refund(self, payment_intent_id, amount_cents, idempotency_key, metadata):
        rid = f"re_test_{len(self.refunds) + 1}"
        self.refunds.append({"id": rid, "payment_intent": payment_intent_id, "amount": amount_cents})
        return {"id": rid, "status": "succeeded"}

    def expire_checkout_session(self, sid):
        self.expired.append(sid)

    def payment_method_label(self, pi):
        return "Visa •••• 4242"


@pytest.fixture(autouse=True)
def fake_stripe(monkeypatch):
    fake = FakeStripe()
    for name in ("create_checkout_session", "create_refund", "expire_checkout_session", "payment_method_label"):
        monkeypatch.setattr(stripe_service, name, getattr(fake, name))
    return fake


def make_client() -> TestClient:
    return TestClient(app, base_url="http://localhost")


@pytest.fixture
def client():
    return make_client()


def csrf_headers(c: TestClient) -> dict:
    token = c.cookies.get("csrf_token")
    if not token:
        token = c.get("/api/v1/auth/csrf").json()["csrf_token"]
    return {"X-CSRF-Token": token}


def signup(c: TestClient, email="etudiant@example.com", password="motdepasse-solide", locale="fr") -> dict:
    r = c.post("/api/v1/auth/signup", json={"email": email, "password": password, "first_name": "Étudiant", "last_name": "Test", "locale": locale})
    assert r.status_code == 201, r.text
    return r.json()


def make_bootcamp(db, slug="devops-intensif", title="DevOps intensif", price=1299_00, capacity=10, start_in_days=30, weekdays=(1, 3), taxable=True, early=None) -> Cohort:
    cat = db.query(Category).first() or Category(name_fr="DevOps", name_en="DevOps", slug_fr="devops", slug_en="devops")
    db.add(cat)
    b = Bootcamp(
        slug_fr=slug, slug_en=slug + "-en", title_fr=title, title_en=title + " EN", tagline_fr="t", summary_fr="s", description_fr="<p>d</p>",
        prerequisites_fr="p", status="published", category=cat, duration_weeks=4, taxable=taxable,
    )
    b.syllabus.append(SyllabusItem(week=1, title_fr="Semaine 1"))
    db.add(b)
    db.flush()
    start = (utcnow() + timedelta(days=start_in_days)).date()
    c = Cohort(
        bootcamp_id=b.id, code=f"{slug.upper()}-{start_in_days}", start_date=start, end_date=start + timedelta(days=27),
        schedule=[{"weekday": d, "start": "18:30", "end": "21:00"} for d in weekdays], capacity=capacity, price_cents=price,
        early_bird_price_cents=early, early_bird_until=(utcnow() + timedelta(days=5)) if early else None,
        registration_deadline=utcnow() + timedelta(days=start_in_days - 1), meeting_url="https://meet.example.com/x",
    )
    db.add(c)
    db.flush()
    calendar.generate_sessions(db, c, replace_future=False)
    db.commit()
    return c


def make_admin(db, email="admin@example.com", role="admin") -> User:
    u = User(email=email, password_hash=hash_password("admin-password-123"), role=role, first_name="Ad", last_name="Min", email_verified_at=utcnow())
    db.add(u)
    db.commit()
    return u


def admin_client(db, email="admin@example.com", role="admin") -> TestClient:
    """Signed-in staff client that passed TOTP."""
    import pyotp

    u = make_admin(db, email, role)
    c = make_client()
    r = c.post("/api/v1/auth/login", json={"email": email, "password": "admin-password-123"})
    assert r.json()["mfa_setup_required"]
    setup = c.post("/api/v1/auth/totp/setup", headers=csrf_headers(c)).json()
    r = c.post("/api/v1/auth/totp/enable", json={"code": pyotp.TOTP(setup["secret"]).now()}, headers=csrf_headers(c))
    assert r.status_code == 200, r.text
    c.user_id = u.id
    return c


def stripe_event(event_type: str, obj: dict, event_id: str | None = None) -> tuple[bytes, dict]:
    payload = json.dumps({"id": event_id or f"evt_{time.time_ns()}", "object": "event", "type": event_type, "data": {"object": obj}}).encode()
    ts = int(time.time())
    sig = hmac.new(b"whsec_test_secret", f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return payload, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}


def pay(client: TestClient, order_id: str, session_id: str, event_id: str | None = None, pi: str = "pi_test_1"):
    payload, headers = stripe_event(
        "checkout.session.completed",
        {"id": session_id, "object": "checkout.session", "payment_status": "paid", "payment_intent": pi, "client_reference_id": order_id, "metadata": {"order_id": order_id}},
        event_id,
    )
    return client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)


ADDRESS = {"line1": "123 rue Principale", "city": "Montréal", "province": "QC", "postal_code": "H2X 1Y4", "country": "CA"}


def checkout(c: TestClient, **extra):
    body = {"billing_address": ADDRESS, "accept_terms": True, "locale": "fr", **extra}
    return c.post("/api/v1/checkout", json=body, headers=csrf_headers(c))
