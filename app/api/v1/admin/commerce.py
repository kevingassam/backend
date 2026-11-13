"""Admin: dashboard (EF-60), orders & refunds (EF-65), promo codes (EF-67), reports (EF-71), settings (EF-73)."""

import csv
import io
import uuid
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import DB, AdminUser
from app.api.v1.admin.common import dt, paginate
from app.core.db import utcnow
from app.core.errors import AppError
from app.core.i18n import BUSINESS_TZ
from app.core.security import client_ip
from app.models import (
    Bootcamp,
    CancellationRequest,
    Cohort,
    Enrollment,
    NewsletterSubscriber,
    Order,
    OrderItem,
    PrivacyRequest,
    PromoCode,
    Refund,
    User,
)
from app.services import audit, jobs, pdf, pricing, seats, site_settings
from app.services import orders as order_svc

router = APIRouter(tags=["admin"])
PAID = ("paid", "partially_refunded", "refunded")


def _month_bounds(d: date) -> tuple[datetime, datetime]:
    tz = ZoneInfo(BUSINESS_TZ)
    start = datetime.combine(d.replace(day=1), time(0), tzinfo=tz)
    nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, datetime.combine(nxt, time(0), tzinfo=tz)


def _revenue(db, start: datetime, end: datetime) -> dict:
    row = db.execute(
        select(func.coalesce(func.sum(Order.total_cents), 0), func.coalesce(func.sum(Order.tax_cents), 0), func.count())
        .where(Order.status.in_(PAID), Order.paid_at >= start, Order.paid_at < end)
    ).one()
    refunds = db.scalar(select(func.coalesce(func.sum(Refund.amount_cents), 0)).where(Refund.status == "succeeded", Refund.created_at >= start, Refund.created_at < end))
    return {"gross": int(row[0]), "taxes": int(row[1]), "net_before_refunds": int(row[0]) - int(row[1]), "orders": int(row[2]), "refunds": int(refunds or 0)}


@router.get("/admin/dashboard")
def dashboard(db: DB, _: AdminUser) -> dict:
    today = utcnow().astimezone(ZoneInfo(BUSINESS_TZ)).date()
    cur_start, cur_end = _month_bounds(today)
    prev_start, prev_end = _month_bounds(cur_start.date() - timedelta(days=1))
    new_enrollments = db.scalar(select(func.count()).select_from(Enrollment).where(Enrollment.created_at >= cur_start, Enrollment.status.in_(("confirmed", "completed")))) or 0
    upcoming = list(db.scalars(select(Cohort).where(Cohort.start_date >= today, Cohort.status.in_(("open", "closed"))).options(selectinload(Cohort.bootcamp)).order_by(Cohort.start_date).limit(20)))
    fill = []
    for c in upcoming:
        n = seats.confirmed_count(db, c.id)
        fill.append({"cohort_id": str(c.id), "code": c.code, "bootcamp_title": c.bootcamp.title_fr, "start_date": c.start_date.isoformat(), "enrolled": n, "capacity": c.capacity, "fill_rate": round(100 * n / c.capacity) if c.capacity else 0})
    started = db.scalar(select(func.count()).select_from(Order).where(Order.created_at >= cur_start, Order.source == "stripe")) or 0
    paid = db.scalar(select(func.count()).select_from(Order).where(Order.created_at >= cur_start, Order.source == "stripe", Order.status.in_(PAID))) or 0
    return {
        "current_month": _revenue(db, cur_start, cur_end),
        "previous_month": _revenue(db, prev_start, prev_end),
        "new_enrollments_this_month": new_enrollments,
        "upcoming_fill": fill,
        "pending_cancellations": db.scalar(select(func.count()).select_from(CancellationRequest).where(CancellationRequest.status == "pending")) or 0,
        "pending_privacy_requests": db.scalar(select(func.count()).select_from(PrivacyRequest).where(PrivacyRequest.status == "pending")) or 0,
        "checkouts_started_this_month": started,
        "checkout_abandon_rate": round(100 * (started - paid) / started) if started else None,
    }


# --- Orders ------------------------------------------------------------------------------------


def order_brief(o: Order) -> dict:
    return {
        "id": str(o.id), "number": o.number, "status": o.status, "source": o.source, "created_at": dt(o.created_at), "paid_at": dt(o.paid_at),
        "total": o.total_cents, "refunded": o.refunded_cents, "province": o.province, "promo_code": o.promo_code_text,
        "customer": {"id": str(o.user_id), "name": o.billing_name, "email": o.billing_email},
        "items_count": len(o.items),
    }


@router.get("/admin/orders")
def list_orders(
    db: DB, _: AdminUser, status: str | None = None, q: str | None = None, bootcamp_id: uuid.UUID | None = None,
    date_from: date | None = None, date_to: date | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, le=100),
) -> dict:
    query = select(Order).options(selectinload(Order.items)).order_by(Order.created_at.desc())
    if status:
        query = query.where(Order.status == status)
    else:
        query = query.where(Order.status != "expired")
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Order.number.ilike(like), Order.billing_email.ilike(like), Order.billing_name.ilike(like)))
    if bootcamp_id:
        query = query.where(Order.id.in_(select(OrderItem.order_id).where(OrderItem.bootcamp_id == bootcamp_id)))
    if date_from:
        query = query.where(Order.created_at >= datetime.combine(date_from, time(0), tzinfo=ZoneInfo(BUSINESS_TZ)))
    if date_to:
        query = query.where(Order.created_at < datetime.combine(date_to + timedelta(days=1), time(0), tzinfo=ZoneInfo(BUSINESS_TZ)))
    return paginate(db, query, page, page_size, order_brief)


@router.get("/admin/orders/{oid}")
def get_order(oid: uuid.UUID, db: DB, _: AdminUser) -> dict:
    o = db.get(Order, oid)
    if not o:
        raise AppError("not_found", 404)
    enr = {e.order_item_id: e for e in db.scalars(select(Enrollment).where(Enrollment.order_item_id.in_([it.id for it in o.items])))}
    return {
        **order_brief(o),
        "subtotal": o.subtotal_cents, "multi_discount": o.multi_discount_cents, "promo_discount": o.promo_discount_cents,
        "tax_lines": o.tax_lines, "tax": o.tax_cents, "billing_address": o.billing_address, "locale": o.locale,
        "stripe_checkout_session_id": o.stripe_checkout_session_id, "stripe_payment_intent_id": o.stripe_payment_intent_id,
        "failure_message": o.failure_message, "refundable": o.total_cents - o.refunded_cents if o.status in ("paid", "partially_refunded") else 0,
        "items": [
            {
                "id": str(it.id), "title": it.title_fr, "cohort_code": it.cohort_code, "cohort_id": str(it.cohort_id), "unit_price": it.unit_price_cents,
                "discount": it.discount_cents, "net": it.net_cents, "tax": it.tax_cents, "total": it.total_cents, "refunded": it.refunded_cents,
                "enrollment": {"id": str(enr[it.id].id), "status": enr[it.id].status} if it.id in enr else None,
            }
            for it in o.items
        ],
        "payments": [{"amount": p.amount_cents, "status": p.status, "method": p.method, "created_at": dt(p.created_at)} for p in o.payments],
        "refunds": [{"id": str(r.id), "amount": r.amount_cents, "status": r.status, "reason": r.reason, "created_at": dt(r.created_at), "stripe_refund_id": r.stripe_refund_id} for r in o.refunds],
    }


class RefundIn(BaseModel):
    amount_cents: int = Field(gt=0)
    reason: str = Field(default="", max_length=1000)
    item_ids: list[uuid.UUID] | None = None  # enrollments of these items are cancelled
    cancel_enrollments: bool = True


@router.post("/admin/orders/{oid}/refund")
def refund(oid: uuid.UUID, body: RefundIn, request: Request, db: DB, admin: AdminUser) -> dict:
    """Total or partial refund through Stripe (EF-65, acceptance criterion 6)."""
    o = db.scalar(select(Order).where(Order.id == oid).with_for_update())
    if not o:
        raise AppError("not_found", 404)
    db.refresh(o, attribute_names=["items", "payments", "refunds"])
    order_svc.refund_order(db, o, body.amount_cents, reason=body.reason, actor=admin, item_ids=body.item_ids, cancel_enrollments=body.cancel_enrollments, ip=client_ip(request))
    db.commit()
    return get_order(oid, db, admin)


@router.post("/admin/orders/{oid}/resend-invoice")
def resend_invoice(oid: uuid.UUID, db: DB, _: AdminUser) -> dict:
    o = db.get(Order, oid)
    if not o or o.status not in PAID:
        raise AppError("not_found", 404)
    user = db.get(User, o.user_id)
    from app.core import urls
    from app.services import notify

    jobs.send_email(
        db, "order_confirmed", user.email, o.locale,
        {"first_name": user.first_name, "order_number": o.number, "order_summary_html": notify.order_summary_html(o, o.locale),
         "schedule_html": notify.cohort_schedule_html([it.cohort for it in o.items], o.locale, user.time_zone),
         "dashboard_url": urls.front("dashboard", o.locale), "terms_url": urls.front("terms", o.locale), "refund_policy_url": urls.front("refunds", o.locale)},
        user_id=user.id, attachments=[{"kind": "invoice", "id": str(o.id)}, {"kind": "ics_order", "id": str(o.id)}],
    )
    db.commit()
    return {"ok": True}


@router.get("/admin/orders/{oid}/invoice")
def admin_invoice(oid: uuid.UUID, db: DB, _: AdminUser, lang: Literal["fr", "en"] | None = None) -> Response:
    o = db.get(Order, oid)
    if not o or o.status not in PAID:
        raise AppError("not_found", 404)
    return Response(pdf.invoice_pdf(db, o, lang), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="facture-{o.number}.pdf"'})


# --- Promo codes (EF-67) ---------------------------------------------------------------------------


class PromoIn(BaseModel):
    code: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    type: Literal["percent", "fixed"]
    value: int = Field(gt=0)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)
    per_user_limit: int = Field(default=1, ge=0)
    active: bool = True
    description: str = Field(default="", max_length=300)
    bootcamp_ids: list[uuid.UUID] = []


def promo_out(db, p: PromoCode) -> dict:
    return {
        "id": str(p.id), "code": p.code, "type": p.type, "value": p.value, "starts_at": dt(p.starts_at), "ends_at": dt(p.ends_at),
        "max_uses": p.max_uses, "per_user_limit": p.per_user_limit, "active": p.active, "description": p.description,
        "bootcamp_ids": [str(b.id) for b in p.bootcamps], "uses": pricing.paid_uses(db, p.id),
        "discount_total": int(db.scalar(select(func.coalesce(func.sum(Order.promo_discount_cents), 0)).where(Order.promo_code_id == p.id, Order.status.in_(PAID))) or 0),
    }


def _apply_promo(db, p: PromoCode, body: PromoIn) -> None:
    if body.type == "percent" and body.value > 100:
        raise AppError("validation_error", 422, details=[{"field": "value", "message": "max 100"}])
    for k, v in body.model_dump(exclude={"bootcamp_ids", "code"}).items():
        setattr(p, k, v)
    p.code = body.code.upper()
    p.bootcamps = list(db.scalars(select(Bootcamp).where(Bootcamp.id.in_(body.bootcamp_ids)))) if body.bootcamp_ids else []


@router.get("/admin/promo-codes")
def list_promos(db: DB, _: AdminUser) -> list[dict]:
    return [promo_out(db, p) for p in db.scalars(select(PromoCode).order_by(PromoCode.created_at.desc()))]


@router.post("/admin/promo-codes", status_code=201)
def create_promo(body: PromoIn, db: DB, _: AdminUser) -> dict:
    if pricing.find_promo(db, body.code):
        raise AppError("conflict", 409)
    p = PromoCode(code=body.code.upper(), type=body.type, value=body.value)
    _apply_promo(db, p, body)
    db.add(p)
    db.commit()
    return promo_out(db, p)


@router.put("/admin/promo-codes/{pid}")
def update_promo(pid: uuid.UUID, body: PromoIn, db: DB, _: AdminUser) -> dict:
    p = db.get(PromoCode, pid)
    if not p:
        raise AppError("not_found", 404)
    _apply_promo(db, p, body)
    db.commit()
    return promo_out(db, p)


# --- Settings (EF-73) -------------------------------------------------------------------------------


@router.get("/admin/settings")
def get_settings(db: DB, _: AdminUser) -> dict:
    return site_settings.get_all(db)


@router.put("/admin/settings")
def put_settings(body: dict, request: Request, db: DB, admin: AdminUser) -> dict:
    before = site_settings.get_all(db)
    after = site_settings.update(db, body)
    changed = {k for k in after if after[k] != before.get(k)}
    if changed & {"multi_discount_percent", "multi_discount_enabled", "multi_discount_min_items", "tax_enabled"}:
        audit.log(db, admin.id, "settings_pricing", "settings", "site", {k: before.get(k) for k in changed}, {k: after[k] for k in changed}, client_ip(request))
    db.commit()
    return after


# --- Reports (EF-71) ---------------------------------------------------------------------------------


def _csv(rows: list[list], header: list[str], filename: str) -> Response:
    buf = io.StringIO()
    buf.write("﻿")  # Excel-friendly UTF-8
    w = csv.writer(buf, delimiter=";")
    w.writerow(header)
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _cents(v: int) -> str:
    return f"{v / 100:.2f}"


@router.get("/admin/reports/{report}")
def report(
    report: Literal["students", "orders", "revenue", "taxes", "newsletter"], db: DB, _: AdminUser,
    cohort_id: uuid.UUID | None = None, date_from: date | None = None, date_to: date | None = None,
) -> Response:
    tz = ZoneInfo(BUSINESS_TZ)
    start = datetime.combine(date_from, time(0), tzinfo=tz) if date_from else datetime(2000, 1, 1, tzinfo=tz)
    end = datetime.combine(date_to + timedelta(days=1), time(0), tzinfo=tz) if date_to else datetime(2100, 1, 1, tzinfo=tz)

    if report == "students":
        q = select(Enrollment).options(selectinload(Enrollment.user), selectinload(Enrollment.cohort)).order_by(Enrollment.cohort_id)
        if cohort_id:
            q = q.where(Enrollment.cohort_id == cohort_id)
        rows = [[e.cohort.code, e.cohort.bootcamp.title_fr, e.user.last_name, e.user.first_name, e.user.email, e.user.phone or "", e.status, e.source, e.created_at.astimezone(tz).date().isoformat()] for e in db.scalars(q)]
        return _csv(rows, ["cohorte", "bootcamp", "nom", "prenom", "courriel", "telephone", "statut", "source", "inscrit_le"], "etudiants.csv")

    if report == "orders":
        q = select(Order).where(Order.status.in_(PAID + ("failed",)), Order.created_at >= start, Order.created_at < end).options(selectinload(Order.items)).order_by(Order.created_at)
        rows = []
        for o in db.scalars(q):
            taxes = {t["type"]: t["amount"] for t in o.tax_lines}
            rows.append([o.number, o.created_at.astimezone(tz).isoformat(timespec="minutes"), o.status, o.billing_name, o.billing_email, o.province or "",
                         " | ".join(f"{it.title_fr} ({it.cohort_code})" for it in o.items), _cents(o.subtotal_cents), _cents(o.discount_cents),
                         o.promo_code_text or "", _cents(taxes.get("GST", 0)), _cents(taxes.get("QST", 0)), _cents(taxes.get("HST", 0)),
                         _cents(o.total_cents), _cents(o.refunded_cents)])
        return _csv(rows, ["numero", "date", "statut", "client", "courriel", "province", "articles", "sous_total", "rabais", "code_promo", "tps", "tvq", "tvh", "total", "rembourse"], "commandes.csv")

    if report == "revenue":
        month = func.to_char(func.timezone(BUSINESS_TZ, Order.paid_at), "YYYY-MM")
        q = (
            select(month, OrderItem.title_fr, func.count(OrderItem.id), func.sum(OrderItem.net_cents), func.sum(OrderItem.tax_cents), func.sum(OrderItem.total_cents), func.sum(OrderItem.refunded_cents))
            .join(Order, Order.id == OrderItem.order_id)
            .where(Order.status.in_(PAID), Order.paid_at >= start, Order.paid_at < end)
            .group_by(month, OrderItem.title_fr).order_by(month, OrderItem.title_fr)
        )
        rows = [[m, t, n, _cents(net), _cents(tax), _cents(tot), _cents(ref), _cents(tot - ref)] for m, t, n, net, tax, tot, ref in db.execute(q)]
        return _csv(rows, ["mois", "bootcamp", "inscriptions", "revenus_avant_taxes", "taxes", "total_percu", "rembourse", "total_net"], "revenus.csv")

    if report == "taxes":
        q = select(Order).where(Order.status.in_(PAID), Order.paid_at >= start, Order.paid_at < end)
        agg: dict[tuple[str, str, str], list[int]] = {}
        for o in db.scalars(q):
            ratio = (o.total_cents - o.refunded_cents) / o.total_cents if o.total_cents else 0
            for t in o.tax_lines:
                key = (o.province or "—", t["type"], t["rate"])
                a = agg.setdefault(key, [0, 0])
                a[0] += t["amount"]
                a[1] += round(t["amount"] * ratio)
        rows = [[p, ty, r, _cents(v[0]), _cents(v[0] - v[1]), _cents(v[1])] for (p, ty, r), v in sorted(agg.items())]
        return _csv(rows, ["province", "taxe", "taux", "percue", "remboursee", "nette"], "taxes.csv")

    subs = db.scalars(select(NewsletterSubscriber).where(NewsletterSubscriber.unsubscribed_at.is_(None)).order_by(NewsletterSubscriber.created_at))
    rows = [[s.email, s.locale, s.created_at.astimezone(tz).isoformat(timespec="minutes")] for s in subs]
    return _csv(rows, ["email", "langue", "consentement_le"], "infolettre.csv")


