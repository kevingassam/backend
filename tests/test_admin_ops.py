from sqlalchemy import select

from app.models import Enrollment, Job
from tests.conftest import admin_client, checkout, csrf_headers, make_bootcamp, pay, signup


def _templates(db):
    return [j.payload["template"] for j in db.scalars(select(Job))]


def test_manual_enrollment_transfer_and_completion(db, client):
    c1 = make_bootcamp(db, "devops-a", capacity=1)
    c2 = make_bootcamp(db, "devops-b", capacity=1)
    admin = admin_client(db)
    h = csrf_headers(admin)
    r = admin.post("/api/v1/admin/enrollments", json={"email": "virement@example.com", "first_name": "Vi", "last_name": "Rement", "cohort_id": str(c1.id), "note": "payé par virement"}, headers=h)
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    assert "enrollment_manual" in _templates(db) and "password_reset" in _templates(db)  # account activation link
    # cohort now full: a second manual enrollment needs the override
    r = admin.post("/api/v1/admin/enrollments", json={"email": "autre@example.com", "cohort_id": str(c1.id)}, headers=h)
    assert r.status_code == 409
    # transfer to another cohort
    r = admin.post(f"/api/v1/admin/enrollments/{eid}/transfer", json={"to_cohort_id": str(c2.id)}, headers=h)
    assert r.status_code == 200 and r.json()["cohort_id"] == str(c2.id)
    db.expire_all()
    assert db.get(Enrollment, __import__("uuid").UUID(eid)).status == "transferred"
    # completion → certificate
    new_id = r.json()["id"]
    r = admin.post(f"/api/v1/admin/enrollments/{new_id}/complete", headers=h)
    assert r.status_code == 200 and r.json()["certificate_number"]
    assert "certificate_ready" in _templates(db)
    audit = admin.get("/api/v1/admin/audit-log").json()["items"]
    assert {a["action"] for a in audit} >= {"enroll_manual", "transfer", "complete"}


def test_cancellation_request_with_partial_refund(db, client, fake_stripe):
    c1 = make_bootcamp(db)
    signup(client)
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers=csrf_headers(client))
    pay(client, checkout(client).json()["order_id"], "cs_test_1", pi="pi_cancel")
    enr = client.get("/api/v1/me/enrollments").json()[0]
    assert enr["can_request_cancellation"] is True
    r = client.post(f"/api/v1/me/enrollments/{enr['id']}/cancellation-request", json={"reason": "conflit d'horaire"}, headers=csrf_headers(client))
    assert r.status_code == 200
    assert client.post(f"/api/v1/me/enrollments/{enr['id']}/cancellation-request", json={}, headers=csrf_headers(client)).status_code == 409
    admin = admin_client(db)
    req = admin.get("/api/v1/admin/cancellation-requests").json()[0]
    r = admin.post(f"/api/v1/admin/cancellation-requests/{req['id']}/decide", json={"approve": True, "refund_cents": 500_00, "admin_note": "Selon la politique"}, headers=csrf_headers(admin))
    assert r.status_code == 200, r.text
    assert fake_stripe.refunds[0]["amount"] == 500_00
    assert client.get("/api/v1/me/enrollments").json()[0]["status"] == "cancelled"
    order = client.get("/api/v1/me/orders").json()[0]
    assert order["status"] == "partially_refunded" and order["refunded"] == 500_00
    assert {"refund_processed", "cancellation_decision"} <= set(_templates(db))


def test_admin_bootcamp_lifecycle(db):
    admin = admin_client(db)
    h = csrf_headers(admin)
    body = {
        "title_fr": "Sécurité infonuagique", "title_en": "Cloud security", "tagline_fr": "t", "summary_fr": "s",
        "description_fr": "<p>d</p>", "prerequisites_fr": "<p>p</p>", "syllabus": [{"week": 1, "title_fr": "Intro"}],
        "faq": [{"question_fr": "Q ?", "answer_fr": "R."}],
    }
    b = admin.post("/api/v1/admin/bootcamps", json=body, headers=h).json()
    assert b["slug_fr"] == "securite-infonuagique" and b["slug_en"] == "cloud-security"
    assert admin.post(f"/api/v1/admin/bootcamps/{b['id']}/status", json={"status": "published"}, headers=h).status_code == 200
    preview = admin.get(f"/api/v1/admin/bootcamps/{b['id']}/preview?locale=fr").json()
    assert preview["faq"][0]["question"] == "Q ?"
    cohort = admin.post("/api/v1/admin/cohorts", json={
        "bootcamp_id": b["id"], "code": "SEC-1", "start_date": "2027-03-02", "end_date": "2027-03-30",
        "schedule": [{"weekday": 1, "start": "18:30", "end": "21:00"}], "capacity": 12, "price_cents": 89900,
    }, headers=h)
    assert cohort.status_code == 201, cohort.text
    co = cohort.json()
    assert co["sessions_count"] == 5 and co["registration_deadline"] == "2027-03-02T04:59:00+00:00"  # 1 day before at 23:59 ET
    dup = admin.post(f"/api/v1/admin/cohorts/{co['id']}/duplicate", headers=h).json()
    assert dup["status"] == "draft"
    copy = admin.post(f"/api/v1/admin/bootcamps/{b['id']}/duplicate", headers=h).json()
    assert copy["status"] == "draft" and copy["faq"]
    tpls = admin.get("/api/v1/admin/email-templates").json()
    assert any(t["key"] == "order_confirmed" for t in tpls)
    assert admin.put("/api/v1/admin/email-templates/order_confirmed/fr", json={"subject": "Merci {first_name}", "body_html": "<p>Bonjour {first_name}</p>"}, headers=h).status_code == 200
    prev = admin.post("/api/v1/admin/email-templates/order_confirmed/fr/preview", headers=h).json()
    assert prev["subject"] == "Merci [first_name]"
