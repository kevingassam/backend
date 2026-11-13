"""Acceptance criteria from section 14 of the specification."""

import threading
from datetime import timedelta

import pyotp  # noqa: F401
from sqlalchemy import func, select

from app.core.db import SessionLocal, utcnow
from app.models import ClassSession, Enrollment, Job, Order, SeatHold, WaitlistEntry
from app.services import waitlist
from tests.conftest import (
    ADDRESS,
    admin_client,
    checkout,
    csrf_headers,
    make_bootcamp,
    make_client,
    pay,
    signup,
    stripe_event,
)


def _jobs(db, template):
    return [j for j in db.scalars(select(Job).where(Job.type == "send_email")) if j.payload["template"] == template]


def test_1_two_bootcamps_one_payment_one_invoice(client, db, fake_stripe):
    """A visitor adds two bootcamps, creates an account during checkout, pays once, sees both enrollments."""
    c1 = make_bootcamp(db, "devops-intensif", price=1000_00)
    c2 = make_bootcamp(db, "python-donnees", price=500_00)
    # visitor cart (anonymous)
    assert client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}).status_code == 200
    assert client.post("/api/v1/cart/items", json={"cohort_id": str(c2.id)}).status_code == 200
    # account created during checkout: the anonymous cart is merged
    signup(client)
    cart = client.get("/api/v1/cart").json()
    assert cart["count"] == 2
    assert cart["multi_discount"] == 150_00  # 10 % from 2 bootcamps

    r = checkout(client)
    assert r.status_code == 200, r.text
    order_id = r.json()["order_id"]
    assert len(fake_stripe.sessions) == 1
    params = fake_stripe.sessions[0]["params"]
    assert params["mode"] == "payment" and params["line_items"][0]["price_data"]["currency"] == "cad"

    # the browser redirect alone does not confirm anything (EF-45)
    status = client.get(f"/api/v1/checkout/orders/{order_id}").json()
    assert status["status"] == "pending"
    assert db.scalar(select(func.count()).select_from(Enrollment)) == 0

    assert pay(client, order_id, fake_stripe.sessions[0]["id"]).status_code == 200
    enrollments = client.get("/api/v1/me/enrollments").json()
    assert len(enrollments) == 2
    order = db.get(Order, __import__("uuid").UUID(order_id))
    assert order.status == "paid"
    # QC taxes: GST 5 % + QST 9.975 % on 1350.00
    assert {t["type"]: t["amount"] for t in order.tax_lines} == {"GST": 67_50, "QST": 134_66}
    assert order.total_cents == 1350_00 + 67_50 + 134_66
    # Stripe total == invoice total (criterion 5)
    assert sum(li["price_data"]["unit_amount"] for li in params["line_items"]) == order.total_cents
    confirmations = _jobs(db, "order_confirmed")
    assert len(confirmations) == 1
    assert {a["kind"] for a in confirmations[0].payload["attachments"]} == {"invoice", "ics_order"}
    # one invoice PDF, downloadable from the dashboard
    inv = client.get(f"/api/v1/me/orders/{order_id}/invoice")
    assert inv.status_code == 200 and inv.content[:4] == b"%PDF"
    # cart emptied
    assert client.get("/api/v1/cart").json()["count"] == 0


def test_2_declined_payment_keeps_cart_and_creates_nothing(client, db, fake_stripe):
    c1 = make_bootcamp(db)
    signup(client)
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers=csrf_headers(client))
    order_id = checkout(client).json()["order_id"]
    # Card declined on Stripe's page → the student cancels back, the session eventually expires
    payload, headers = stripe_event("checkout.session.expired", {"id": "cs_test_1", "object": "checkout.session", "metadata": {"order_id": order_id}})
    assert client.post("/api/v1/webhooks/stripe", content=payload, headers=headers).status_code == 200
    assert db.scalar(select(func.count()).select_from(Enrollment)) == 0
    assert client.get("/api/v1/cart").json()["count"] == 1  # cart kept, can retry
    holds = db.scalars(select(SeatHold)).all()
    assert all(h.released_at for h in holds)
    # retry works
    assert checkout(client).status_code == 200


def test_3_last_seat_race_only_one_enrolled(db, fake_stripe):
    cohort = make_bootcamp(db, capacity=1)
    clients = []
    for i in range(2):
        c = make_client()
        signup(c, email=f"s{i}@example.com")
        assert c.post("/api/v1/cart/items", json={"cohort_id": str(cohort.id)}, headers=csrf_headers(c)).status_code == 200
        clients.append(c)
    results = []
    barrier = threading.Barrier(2)

    def go(c):
        barrier.wait()
        results.append(checkout(c))

    threads = [threading.Thread(target=go, args=(c,)) for c in clients]
    [t.start() for t in threads]
    [t.join() for t in threads]
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409], [r.text for r in results]
    loser = next(r for r in results if r.status_code == 409).json()
    assert loser["error"]["code"] == "seats_unavailable"
    assert loser["error"]["details"][0]["reason"] == "full"  # → the frontend offers the waitlist
    assert len(fake_stripe.sessions) == 1  # the other student is never sent to payment


def test_4_replayed_webhook_is_idempotent(client, db, fake_stripe):
    c1 = make_bootcamp(db)
    signup(client)
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers=csrf_headers(client))
    order_id = checkout(client).json()["order_id"]
    for _ in range(3):
        assert pay(client, order_id, "cs_test_1", event_id="evt_same").status_code == 200
    # same payment but a different event id (Stripe sends both completed + async events sometimes)
    assert pay(client, order_id, "cs_test_1", event_id="evt_other").status_code == 200
    assert db.scalar(select(func.count()).select_from(Enrollment)) == 1
    assert len(_jobs(db, "order_confirmed")) == 1


def test_5_promo_and_multi_discount_totals_match_stripe(client, db, fake_stripe):
    c1 = make_bootcamp(db, "devops-intensif", price=1000_00)
    c2 = make_bootcamp(db, "python-donnees", price=333_33)
    admin = admin_client(db)
    r = admin.post("/api/v1/admin/promo-codes", json={"code": "rentree", "type": "fixed", "value": 100_00}, headers=csrf_headers(admin))
    assert r.status_code == 201, r.text
    signup(client)
    for c in (c1, c2):
        client.post("/api/v1/cart/items", json={"cohort_id": str(c.id)}, headers=csrf_headers(client))
    cart = client.post("/api/v1/cart/promo-code", json={"code": "RENTREE"}, headers=csrf_headers(client)).json()
    assert cart["promo_code"] == "RENTREE" and cart["promo_discount"] == 100_00
    order_id = checkout(client).json()["order_id"]
    order = db.get(Order, __import__("uuid").UUID(order_id))
    assert order.subtotal_cents == 1333_33
    assert order.multi_discount_cents == 133_33
    assert order.promo_discount_cents == 100_00
    stripe_total = sum(li["price_data"]["unit_amount"] for li in fake_stripe.sessions[0]["params"]["line_items"])
    assert stripe_total == order.total_cents == cart["total"]
    pay(client, order_id, "cs_test_1")
    # one use per student
    for c in (c1, c2):
        pass
    usage = admin.get("/api/v1/admin/promo-codes").json()[0]
    assert usage["uses"] == 1


def test_6_refund_from_admin(client, db, fake_stripe):
    c1 = make_bootcamp(db)
    signup(client)
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers=csrf_headers(client))
    order_id = checkout(client).json()["order_id"]
    pay(client, order_id, "cs_test_1", pi="pi_refund")
    admin = admin_client(db)
    detail = admin.get(f"/api/v1/admin/orders/{order_id}").json()
    r = admin.post(f"/api/v1/admin/orders/{order_id}/refund", json={"amount_cents": detail["refundable"], "reason": "test"}, headers=csrf_headers(admin))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "refunded"
    assert fake_stripe.refunds[0]["payment_intent"] == "pi_refund"
    assert db.scalar(select(Enrollment)).status == "cancelled"
    assert len(_jobs(db, "refund_processed")) == 1
    # cannot refund twice
    r = admin.post(f"/api/v1/admin/orders/{order_id}/refund", json={"amount_cents": 1}, headers=csrf_headers(admin))
    assert r.status_code == 409


def test_7_waitlist_offer_then_next_person(db, fake_stripe):
    cohort = make_bootcamp(db, capacity=1)
    buyer = make_client()
    signup(buyer, "buyer@example.com")
    buyer.post("/api/v1/cart/items", json={"cohort_id": str(cohort.id)}, headers=csrf_headers(buyer))
    order_id = checkout(buyer).json()["order_id"]
    pay(buyer, order_id, "cs_test_1", pi="pi_buyer")

    # cohort full → adding to cart is refused, waitlist accepted
    visitor = make_client()
    r = visitor.post("/api/v1/cart/items", json={"cohort_id": str(cohort.id)})
    assert r.status_code == 409 and r.json()["error"]["code"] == "cohort_full"
    for email in ("first@example.com", "second@example.com"):
        r = make_client().post(f"/api/v1/cohorts/{cohort.id}/waitlist", json={"email": email, "first_name": "W"})
        assert r.status_code == 201, r.text

    # a seat frees up (refund) → first person gets a 48 h offer
    admin = admin_client(db)
    admin.post(f"/api/v1/admin/orders/{order_id}/refund", json={"amount_cents": db.get(Order, __import__("uuid").UUID(order_id)).total_cents}, headers=csrf_headers(admin))
    db.expire_all()
    first = db.scalar(select(WaitlistEntry).where(WaitlistEntry.email == "first@example.com"))
    second = db.scalar(select(WaitlistEntry).where(WaitlistEntry.email == "second@example.com"))
    assert first.status == "offered" and second.status == "waiting"
    assert timedelta(hours=47) < first.offer_expires_at - utcnow() <= timedelta(hours=48)
    assert len(_jobs(db, "waitlist_offer")) == 1
    # the reserved seat is not available to others
    assert make_client().post("/api/v1/cart/items", json={"cohort_id": str(cohort.id)}).status_code == 409

    # offer expires → next person
    first.offer_expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    with SessionLocal() as s:
        waitlist.expire_offers(s)
        s.commit()
    db.expire_all()
    assert db.get(WaitlistEntry, first.id).status == "expired"
    assert db.get(WaitlistEntry, second.id).status == "offered"


def test_7b_waitlist_offer_checkout(db, fake_stripe):
    cohort = make_bootcamp(db, capacity=1)
    c = make_client()
    signup(c, "w@example.com")
    entry = WaitlistEntry(cohort_id=cohort.id, email="w@example.com", first_name="W", position=1)
    db.add(entry)
    db.commit()
    from app.core.security import new_token  # noqa: F401

    with SessionLocal() as s:
        e = s.get(WaitlistEntry, entry.id)
        waitlist.offer(s, e)
        s.commit()
    token_job = _jobs(db, "waitlist_offer")[0]
    token = token_job.payload["context"]["offer_url"].rsplit("/", 1)[1]
    assert c.get(f"/api/v1/waitlist/offers/{token}").status_code == 200
    r = c.post(f"/api/v1/waitlist/offers/{token}/checkout", json={"billing_address": ADDRESS, "accept_terms": True}, headers=csrf_headers(c))
    assert r.status_code == 200, r.text
    pay(c, r.json()["order_id"], "cs_test_1")
    db.expire_all()
    assert db.get(WaitlistEntry, entry.id).status == "converted"
    assert db.scalar(select(Enrollment)).status == "confirmed"


def test_9_moving_a_session_notifies_students_with_ics(client, db, fake_stripe):
    c1 = make_bootcamp(db)
    signup(client)
    client.post("/api/v1/cart/items", json={"cohort_id": str(c1.id)}, headers=csrf_headers(client))
    pay(client, checkout(client).json()["order_id"], "cs_test_1")
    s = db.scalar(select(ClassSession).where(ClassSession.cohort_id == c1.id).order_by(ClassSession.starts_at))
    admin = admin_client(db)
    new_start = s.starts_at + timedelta(days=1)
    r = admin.put(f"/api/v1/admin/sessions/{s.id}", json={"starts_at": new_start.isoformat(), "ends_at": (new_start + timedelta(hours=2)).isoformat()}, headers=csrf_headers(admin))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "moved"
    jobs = _jobs(db, "session_changed")
    assert len(jobs) == 1 and jobs[0].payload["attachments"][0]["kind"] == "ics_session"
    sessions = client.get("/api/v1/me/sessions").json()["items"]
    from datetime import datetime

    assert any(x["id"] == str(s.id) and datetime.fromisoformat(x["starts_at"]) == new_start for x in sessions)
