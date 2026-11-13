"""Business rules, security and permissions."""

from datetime import timedelta

import pyotp
from sqlalchemy import select

from app.core.db import utcnow
from app.core.i18n import money
from app.models import Job, User
from app.services import taxes
from tests.conftest import admin_client, checkout, csrf_headers, make_admin, make_bootcamp, make_client, pay, signup

# --- taxes and formatting -------------------------------------------------------------------


def test_taxes_by_province():
    assert taxes.compute(100_00, "QC") == [{"type": "GST", "rate": "5", "amount": 5_00}, {"type": "QST", "rate": "9.975", "amount": 9_98}]
    assert taxes.compute(100_00, "ON") == [{"type": "HST", "rate": "13", "amount": 13_00}]
    assert taxes.compute(100_00, "NS") == [{"type": "HST", "rate": "14", "amount": 14_00}]
    assert taxes.compute(100_00, "AB") == [{"type": "GST", "rate": "5", "amount": 5_00}]
    assert taxes.compute(100_00, "ON", country="US") == []
    assert taxes.compute(100_00, "QC", taxable=False) == []


def test_money_format_by_locale():
    assert money(1299_00, "fr") == "1 299,00 $"
    assert money(1299_00, "en") == "$1,299.00"


# --- cart rules ----------------------------------------------------------------------------------


def test_one_cohort_per_bootcamp_replaces_with_notice(client, db):
    c1 = make_bootcamp(db, start_in_days=30)
    from app.models import Cohort

    c2 = Cohort(bootcamp_id=c1.bootcamp_id, code="OTHER", start_date=c1.start_date + timedelta(days=60), end_date=c1.end_date + timedelta(days=60),
                schedule=c1.schedule, capacity=5, price_cents=c1.price_cents, registration_deadline=utcnow() + timedelta(days=80))
    db.add(c2)
    db.commit()
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)})
    r = client.post("/api/v1/cart/items", json={"cohort_id": str(c2.id)}).json()
    assert r["replaced"] == c1.code
    assert r["cart"]["count"] == 1 and r["cart"]["items"][0]["cohort"]["id"] == str(c2.id)


def test_cannot_add_cohort_already_enrolled(client, db):
    c1 = make_bootcamp(db)
    signup(client)
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers=csrf_headers(client))
    pay(client, checkout(client).json()["order_id"], "cs_test_1")
    r = client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers=csrf_headers(client))
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_enrolled"


def test_registration_closes_at_deadline(client, db):
    c1 = make_bootcamp(db)
    c1.registration_deadline = utcnow() - timedelta(minutes=1)
    db.commit()
    r = client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)})
    assert r.status_code == 409 and r.json()["error"]["code"] == "cohort_unavailable"
    card = client.get("/api/v1/bootcamps/devops-intensif").json()["cohorts"][0]
    assert card["status"] == "closed"


def test_few_seats_badge_and_early_bird(client, db):
    make_bootcamp(db, capacity=5, early=999_00)
    item = client.get("/api/v1/bootcamps").json()["items"][0]
    assert item["few_seats"] is True
    assert item["price_from"] == 999_00 and item["next_cohort"]["early_bird_active"] is True


def test_terms_must_be_accepted(client, db):
    c1 = make_bootcamp(db)
    signup(client)
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers=csrf_headers(client))
    r = checkout(client, accept_terms=False)
    assert r.status_code == 422 and r.json()["error"]["code"] == "terms_required"


def test_overlap_warning(client, db):
    c1 = make_bootcamp(db, "a-bootcamp", weekdays=(1,))
    c2 = make_bootcamp(db, "b-bootcamp", weekdays=(1,))
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)})
    cart = client.post("/api/v1/cart/items", json={"cohort_id": str(c2.id)}).json()["cart"]
    assert cart["warnings"] and cart["warnings"][0]["type"] == "overlap"
    assert cart["can_checkout"] is True  # non-blocking


def test_catalog_filters_and_search(client, db):
    make_bootcamp(db, "devops-intensif", title="DevOps intensif", price=1299_00)
    make_bootcamp(db, "python-donnees", title="Python données", price=500_00)
    assert client.get("/api/v1/bootcamps?q=python").json()["total"] == 1
    assert client.get("/api/v1/bootcamps?price_max=600").json()["total"] == 1
    assert [x["slug"] for x in client.get("/api/v1/bootcamps?sort=price").json()["items"]] == ["python-donnees", "devops-intensif"]


# --- auth and security ---------------------------------------------------------------------


def test_lockout_after_5_failed_logins(client, db):
    signup(client, "lock@example.com")
    c = make_client()
    for _ in range(5):
        assert c.post("/api/v1/auth/login", json={"email": "lock@example.com", "password": "wrong-password"}).status_code == 401
    r = c.post("/api/v1/auth/login", json={"email": "lock@example.com", "password": "motdepasse-solide"})
    assert r.status_code == 423 and r.json()["error"]["code"] == "account_locked"


def test_password_reset_flow(client, db):
    signup(client, "reset@example.com")
    make_client().post("/api/v1/auth/forgot-password", json={"email": "reset@example.com"})
    job = [j for j in db.scalars(select(Job)) if j.payload["template"] == "password_reset"][0]
    token = job.payload["context"]["reset_url"].split("token=")[1]
    assert make_client().post("/api/v1/auth/reset-password", json={"token": token, "password": "nouveau-mot-de-passe"}).status_code == 200
    assert make_client().post("/api/v1/auth/reset-password", json={"token": token, "password": "encore-un-autre"}).status_code == 400  # single use
    assert make_client().post("/api/v1/auth/login", json={"email": "reset@example.com", "password": "nouveau-mot-de-passe"}).status_code == 200


def test_csrf_required_for_cookie_auth(client, db):
    c1 = make_bootcamp(db)
    signup(client)
    r = client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"


def test_session_links_hidden_until_email_verified(client, db):
    c1 = make_bootcamp(db, start_in_days=2)
    signup(client)
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers=csrf_headers(client))
    pay(client, checkout(client).json()["order_id"], "cs_test_1")
    from app.models import ClassSession

    s = db.scalar(select(ClassSession).order_by(ClassSession.starts_at))
    s.starts_at = utcnow() + timedelta(minutes=5)
    s.ends_at = utcnow() + timedelta(hours=2)
    db.commit()
    item = client.get("/api/v1/me/sessions").json()["items"][0]
    assert item["join_available"] is False and item["join_url"] is None
    u = db.scalar(select(User).where(User.email == "etudiant@example.com"))
    u.email_verified_at = utcnow()
    db.commit()
    item = client.get("/api/v1/me/sessions").json()["items"][0]
    assert item["join_available"] is True and item["join_url"] == "https://meet.example.com/x"


def test_admin_requires_totp(db):
    make_admin(db, "a2@example.com")
    c = make_client()
    r = c.post("/api/v1/auth/login", json={"email": "a2@example.com", "password": "admin-password-123"}).json()
    assert r["mfa_setup_required"] is True
    assert c.get("/api/v1/admin/dashboard").json()["error"]["code"] == "mfa_required"
    secret = c.post("/api/v1/auth/totp/setup", headers=csrf_headers(c)).json()["secret"]
    c.post("/api/v1/auth/totp/enable", json={"code": pyotp.TOTP(secret).now()}, headers=csrf_headers(c))
    assert c.get("/api/v1/admin/dashboard").status_code == 200
    # next login asks for the code
    c2 = make_client()
    r = c2.post("/api/v1/auth/login", json={"email": "a2@example.com", "password": "admin-password-123"}).json()
    assert r["mfa_required"] and r["user"] is None
    r = c2.post("/api/v1/auth/mfa/verify", json={"mfa_token": r["mfa_token"], "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200 and c2.get("/api/v1/admin/dashboard").status_code == 200


def test_student_cannot_access_admin_or_instructor(client, db):
    signup(client)
    assert client.get("/api/v1/admin/orders").status_code == 403
    assert client.get("/api/v1/instructor/cohorts").status_code == 403


def test_instructor_sees_only_own_cohorts(db):
    mine = make_bootcamp(db, "mine")
    other = make_bootcamp(db, "other")
    inst = admin_client(db, "inst@example.com", role="instructor")
    mine.instructor_id = inst.user_id
    db.commit()
    assert inst.get(f"/api/v1/instructor/cohorts/{mine.id}/roster").status_code == 200
    assert inst.get(f"/api/v1/instructor/cohorts/{other.id}/roster").status_code == 403
    assert inst.get("/api/v1/admin/orders").status_code == 403  # no access to payments


def test_publish_requires_french_fields(db):
    admin = admin_client(db)
    r = admin.post("/api/v1/admin/bootcamps", json={"title_fr": "Nouveau", "title_en": "New"}, headers=csrf_headers(admin))
    assert r.status_code == 201
    bid = r.json()["id"]
    r = admin.post(f"/api/v1/admin/bootcamps/{bid}/status", json={"status": "published"}, headers=csrf_headers(admin))
    assert r.status_code == 422 and r.json()["error"]["code"] == "french_required"


def test_slug_change_redirects(db, client):
    make_bootcamp(db, "ancien-slug")
    admin = admin_client(db)
    bid = admin.get("/api/v1/admin/bootcamps").json()[0]["id"]
    data = admin.get(f"/api/v1/admin/bootcamps/{bid}").json()
    body = {k: data[k] for k in ("title_fr", "tagline_fr", "summary_fr", "description_fr", "prerequisites_fr", "syllabus")}
    body["slug_fr"] = "nouveau-slug"
    assert admin.put(f"/api/v1/admin/bootcamps/{bid}", json=body, headers=csrf_headers(admin)).status_code == 200
    r = client.get("/api/v1/bootcamps/ancien-slug")
    assert r.status_code == 404 and r.json()["error"]["details"]["redirect_slug"] == "nouveau-slug"


def test_account_deletion_anonymizes_but_keeps_invoices(client, db):
    c1 = make_bootcamp(db)
    signup(client, "gone@example.com")
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers=csrf_headers(client))
    pay(client, checkout(client).json()["order_id"], "cs_test_1")
    assert client.get("/api/v1/me/export").json()["profile"]["email"] == "gone@example.com"
    client.post("/api/v1/me/deletion-request", json={"details": "merci"}, headers=csrf_headers(client))
    admin = admin_client(db)
    req = admin.get("/api/v1/admin/privacy-requests").json()[0]
    assert admin.post(f"/api/v1/admin/privacy-requests/{req['id']}", json={"action": "complete"}, headers=csrf_headers(admin)).status_code == 200
    db.expire_all()
    from app.models import Order

    u = db.scalar(select(User).where(User.deleted_at.isnot(None)))
    assert u.email.endswith("@invalid") and u.address is None
    assert db.scalar(select(Order)).status == "paid"  # invoice kept
    assert client.get("/api/v1/me").status_code == 401  # sessions revoked


def test_reports_csv(db, client):
    c1 = make_bootcamp(db)
    signup(client)
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers=csrf_headers(client))
    pay(client, checkout(client).json()["order_id"], "cs_test_1")
    admin = admin_client(db)
    for report in ("students", "orders", "revenue", "taxes", "newsletter"):
        r = admin.get(f"/api/v1/admin/reports/{report}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    taxes_csv = admin.get("/api/v1/admin/reports/taxes").text
    assert "QC;GST;5;64.95" in taxes_csv
    dash = admin.get("/api/v1/admin/dashboard").json()
    assert dash["current_month"]["orders"] == 1 and dash["new_enrollments_this_month"] == 1
