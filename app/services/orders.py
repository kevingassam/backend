"""Checkout, Stripe fulfilment, payment failures and refunds (EF-40..EF-47, EF-65).

Invariants:
- A seat is only *confirmed* by a verified Stripe webhook (EF-45), never by the browser redirect.
- Seat decisions happen under row locks on the cohorts (no overbooking, section 12).
- Webhook processing is idempotent (stripe_events.event_id unique + order status checks).
"""

import uuid
from datetime import timedelta

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core import urls
from app.core.db import utcnow
from app.core.errors import AppError
from app.core.i18n import fmt_date, money, norm_locale, pick
from app.models import (
    Address,
    Consent,
    Enrollment,
    Order,
    OrderItem,
    Payment,
    Refund,
    SeatHold,
    StripeEvent,
    User,
    WaitlistEntry,
)
from app.services import audit, jobs, notify, pricing, seats, stripe_service, waitlist
from app.services import cart as cart_svc

STRIPE_LOCALES = {"fr": "fr-CA", "en": "en"}


def next_order_number(db: Session) -> str:
    n = db.scalar(text("SELECT nextval('order_number_seq')"))
    return f"{utcnow().year}-{n:06d}"


def _load_order(db: Session, order_id: uuid.UUID, lock: bool = False) -> Order | None:
    q = select(Order).where(Order.id == order_id)
    if lock:
        q = q.with_for_update()
    order = db.scalar(q)
    if order:
        db.refresh(order, attribute_names=["items", "payments", "refunds"])
    return order


def _release_holds(db: Session, order_id: uuid.UUID) -> None:
    db.execute(
        update(SeatHold)
        .where(SeatHold.order_id == order_id, SeatHold.released_at.is_(None), SeatHold.converted_at.is_(None))
        .values(released_at=utcnow())
    )


def _abandon_pending_orders(db: Session, user_id: uuid.UUID) -> None:
    """A new checkout replaces the previous unpaid one: release its seats and expire its Stripe session."""
    pending = list(db.scalars(select(Order).where(Order.user_id == user_id, Order.status == "pending").with_for_update()))
    for o in pending:
        if o.waitlist_entry_id:
            # keep the waitlist offer hold attached to the entry, just detach it from the abandoned order
            db.execute(update(SeatHold).where(SeatHold.order_id == o.id, SeatHold.waitlist_entry_id.isnot(None)).values(order_id=None))
        _release_holds(db, o.id)
        o.status = "expired"
        if o.stripe_checkout_session_id:
            stripe_service.expire_checkout_session(o.stripe_checkout_session_id)


def save_billing_address(db: Session, user: User, data: dict) -> Address:
    addr = user.address
    if not addr:
        addr = Address(user_id=user.id, **data)
        db.add(addr)
        user.address = addr
    else:
        for k, v in data.items():
            setattr(addr, k, v)
    db.flush()
    return addr


def record_consents(db: Session, user: User, *, ip: str | None, ua: str | None, versions: dict[str, str], marketing: bool, context: str) -> None:
    """EF-43 / EF-88: keep proof of consent with date, text version and IP."""
    for ctype in ("terms", "refund_policy", "privacy"):
        db.add(Consent(user_id=user.id, email=user.email, type=ctype, version=versions.get(ctype, "1"), granted=True, ip=ip, user_agent=ua, context=context))
    db.add(Consent(user_id=user.id, email=user.email, type="marketing", version=versions.get("marketing", "1"), granted=marketing, ip=ip, user_agent=ua, context=context))
    if marketing and not user.marketing_opt_in:
        user.marketing_opt_in = True
        from app.services import newsletter

        newsletter.subscribe(db, user.email, user.locale, user_id=user.id)


def _create_order(
    db: Session, user: User, quote: pricing.Quote, address: Address, locale: str, waitlist_entry: WaitlistEntry | None = None
) -> Order:
    order = Order(
        number=next_order_number(db),
        user_id=user.id,
        status="pending",
        locale=locale,
        subtotal_cents=quote.subtotal,
        multi_discount_cents=quote.multi_discount,
        promo_discount_cents=quote.promo_discount,
        discount_cents=quote.discount,
        tax_lines=quote.tax_lines,
        tax_cents=quote.tax,
        total_cents=quote.total,
        province=address.province if address.country == "CA" else None,
        billing_address={
            "line1": address.line1, "line2": address.line2, "city": address.city,
            "province": address.province, "postal_code": address.postal_code, "country": address.country,
        },
        billing_name=user.full_name,
        billing_email=user.email,
        promo_code_id=quote.promo.id if quote.promo else None,
        promo_code_text=quote.promo.code if quote.promo else None,
        waitlist_entry_id=waitlist_entry.id if waitlist_entry else None,
    )
    for line in quote.lines:
        c = line.cohort
        order.items.append(
            OrderItem(
                cohort_id=c.id,
                bootcamp_id=c.bootcamp_id,
                title_fr=c.bootcamp.title_fr,
                title_en=c.bootcamp.title_en or c.bootcamp.title_fr,
                cohort_code=c.code,
                unit_price_cents=line.unit_price,
                multi_discount_cents=line.multi_discount,
                promo_discount_cents=line.promo_discount,
                discount_cents=line.discount,
                net_cents=line.net,
                taxable=c.bootcamp.taxable,
                tax_lines=line.tax_lines,
                tax_cents=line.tax,
                total_cents=line.total,
            )
        )
    db.add(order)
    db.flush()
    return order


def _stripe_params(order: Order, user: User, expires_at) -> dict:
    loc = order.locale
    line_items = []
    for it in order.items:
        c = it.cohort
        desc = f"{'Cohorte' if loc == 'fr' else 'Cohort'} {it.cohort_code} · {fmt_date(c.start_date, loc)} – {fmt_date(c.end_date, loc)}"
        if it.discount_cents:
            desc += f" · {'Prix' if loc == 'fr' else 'Price'} {money(it.unit_price_cents, loc)}, {'rabais' if loc == 'fr' else 'discount'} {money(it.discount_cents, loc)}"
        line_items.append({
            "quantity": 1,
            "price_data": {
                "currency": "cad",
                "unit_amount": it.net_cents,
                "product_data": {"name": it.title_fr if loc == "fr" else it.title_en, "description": desc[:500]},
            },
        })
    from app.services import taxes

    for t in order.tax_lines:
        line_items.append({
            "quantity": 1,
            "price_data": {
                "currency": "cad",
                "unit_amount": t["amount"],
                "product_data": {"name": f"{taxes.label(t['type'], loc)} ({taxes.fmt_rate(t['rate'], loc)})"},
            },
        })
    return {
        "mode": "payment",
        "line_items": line_items,
        "customer_email": user.email,
        "client_reference_id": str(order.id),
        "metadata": {"order_id": str(order.id), "order_number": order.number},
        "payment_intent_data": {
            "metadata": {"order_id": str(order.id), "order_number": order.number},
            "description": f"Commande {order.number}",
        },
        "locale": STRIPE_LOCALES.get(loc, "auto"),
        "success_url": urls.front("confirmation", loc, query=f"order={order.id}&session_id={{CHECKOUT_SESSION_ID}}"),
        "cancel_url": urls.front("cart", loc, query="checkout=cancelled"),
        "expires_at": int(expires_at.timestamp()),
    }


def start_checkout(
    db: Session,
    user: User,
    *,
    address: dict,
    accept_terms: bool,
    marketing_opt_in: bool,
    locale: str,
    ip: str | None,
    ua: str | None,
    consent_versions: dict[str, str],
) -> dict:
    """POST /checkout: re-check seats, hold them, create the order and the Stripe Checkout session."""
    if not accept_terms:
        raise AppError("terms_required", 422)
    locale = norm_locale(locale)
    addr = save_billing_address(db, user, address)

    cart = cart_svc.find(db, user, None)
    if not cart or not cart.items:
        raise AppError("cart_empty", 409)

    _abandon_pending_orders(db, user.id)
    cohorts = seats.lock_cohorts(db, [i.cohort_id for i in cart.items])
    by_id = {c.id: c for c in cohorts}
    problems = []
    for item in cart.items:
        c = by_id[item.cohort_id]
        issue = cart_svc.item_issue(db, c, user)
        if issue:
            problems.append({"cohort_id": str(c.id), "reason": issue})
    if problems:
        db.rollback()
        raise AppError("seats_unavailable", 409, details=problems)

    ordered = [by_id[i.cohort_id] for i in cart.items]
    quote = pricing.build_quote(db, ordered, user_id=user.id, promo=cart.promo_code, province=addr.province, country=addr.country)
    if quote.promo_error:
        cart.promo_code_id = None
        db.commit()
        raise AppError(quote.promo_error, 409)
    order = _create_order(db, user, quote, addr, locale)
    hold_until = seats.hold_expiry(db) + timedelta(minutes=2)  # outlives the Stripe session slightly
    for c in ordered:
        db.add(SeatHold(cohort_id=c.id, user_id=user.id, order_id=order.id, expires_at=hold_until))
    record_consents(db, user, ip=ip, ua=ua, versions=consent_versions, marketing=marketing_opt_in, context="checkout")
    db.commit()  # seats are held; release the row locks before calling Stripe
    return _open_payment(db, order, user)


def _open_payment(db: Session, order: Order, user: User) -> dict:
    order = _load_order(db, order.id)
    if order.total_cents == 0:
        # 100 % promo: nothing to charge, confirm immediately
        fulfil_order(db, order.id, payment_intent_id=None, method=None, source="free")
        db.commit()
        return {"order_id": str(order.id), "checkout_url": urls.front("confirmation", order.locale, query=f"order={order.id}")}
    stripe_expiry = utcnow() + timedelta(minutes=30, seconds=30)
    try:
        session = stripe_service.create_checkout_session(_stripe_params(order, user, stripe_expiry), idempotency_key=f"checkout-{order.id}")
    except AppError:
        order.status = "failed"
        order.failure_message = "stripe_session_creation_failed"
        _release_holds(db, order.id)
        db.commit()
        raise
    order.stripe_checkout_session_id = session["id"]
    db.commit()
    return {"order_id": str(order.id), "checkout_url": session["url"]}


def checkout_waitlist_offer(db: Session, user: User, entry: WaitlistEntry, *, address: dict, accept_terms: bool, marketing_opt_in: bool, locale: str, ip, ua, consent_versions) -> dict:
    """Pay for the seat reserved by a waitlist offer (EF-49)."""
    if not accept_terms:
        raise AppError("terms_required", 422)
    addr = save_billing_address(db, user, address)
    _abandon_pending_orders(db, user.id)
    cohort = seats.lock_cohorts(db, [entry.cohort_id])[0]
    if cart_svc.is_enrolled(db, user.id, cohort.id):
        raise AppError("already_enrolled", 409)
    hold = db.scalar(select(SeatHold).where(SeatHold.waitlist_entry_id == entry.id, SeatHold.released_at.is_(None), SeatHold.converted_at.is_(None)))
    if not hold or hold.expires_at <= utcnow():
        raise AppError("offer_invalid", 410)
    quote = pricing.build_quote(db, [cohort], user_id=user.id, province=addr.province, country=addr.country)
    order = _create_order(db, user, quote, addr, norm_locale(locale), waitlist_entry=entry)
    hold.order_id = order.id
    hold.user_id = user.id
    hold.expires_at = max(hold.expires_at, utcnow() + timedelta(minutes=33))
    record_consents(db, user, ip=ip, ua=ua, versions=consent_versions, marketing=marketing_opt_in, context="checkout")
    db.commit()
    return _open_payment(db, order, user)


# --- Webhook handling -------------------------------------------------------------


def handle_stripe_event(db: Session, event) -> str:
    """Idempotent: an event id is processed once (acceptance criterion 4)."""
    try:
        with db.begin_nested():
            db.add(StripeEvent(event_id=event["id"], type=event["type"]))
            db.flush()
    except IntegrityError:
        return "duplicate"
    obj = event["data"]["object"]
    etype = event["type"]
    if etype in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        if obj.get("payment_status") == "paid":
            order_id = _order_id_from(obj)
            if order_id:
                method = stripe_service.payment_method_label(obj.get("payment_intent"))
                fulfil_order(db, order_id, payment_intent_id=obj.get("payment_intent"), method=method, source="stripe")
    elif etype == "checkout.session.async_payment_failed":
        order_id = _order_id_from(obj)
        if order_id:
            mark_failed(db, order_id, "async_payment_failed")
    elif etype == "checkout.session.expired":
        order_id = _order_id_from(obj)
        if order_id:
            mark_expired(db, order_id)
    elif etype == "charge.refunded":
        sync_dashboard_refund(db, obj)
    ev = db.scalar(select(StripeEvent).where(StripeEvent.event_id == event["id"]))
    ev.processed_at = utcnow()
    return "processed"


def _order_id_from(obj) -> uuid.UUID | None:
    raw = (obj.get("metadata") or {}).get("order_id") or obj.get("client_reference_id")
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


def fulfil_order(db: Session, order_id: uuid.UUID, *, payment_intent_id: str | None, method: str | None, source: str) -> None:
    order = _load_order(db, order_id, lock=True)
    if not order or order.status in ("paid", "partially_refunded", "refunded"):
        return  # already fulfilled: replayed webhook creates nothing (EF-45)
    user = db.get(User, order.user_id)
    cohorts = seats.lock_cohorts(db, [it.cohort_id for it in order.items])
    by_id = {c.id: c for c in cohorts}
    holds = {
        h.cohort_id: h
        for h in db.scalars(select(SeatHold).where(SeatHold.order_id == order.id, SeatHold.released_at.is_(None), SeatHold.converted_at.is_(None)))
    }
    now = utcnow()
    overbooked: list[OrderItem] = []
    for it in order.items:
        c = by_id[it.cohort_id]
        hold = holds.get(c.id)
        has_seat = (hold is not None) or seats.remaining(db, c) > 0
        if not has_seat:
            overbooked.append(it)
            continue
        enr = db.scalar(select(Enrollment).where(Enrollment.user_id == user.id, Enrollment.cohort_id == c.id))
        if enr and enr.status in ("confirmed", "completed"):
            overbooked.append(it)  # already enrolled elsewhere (manual enrollment meanwhile): refund this line
            continue
        if enr:
            enr.status, enr.order_item_id, enr.source, enr.cancelled_at = "confirmed", it.id, source, None
        else:
            db.add(Enrollment(user_id=user.id, cohort_id=c.id, order_item_id=it.id, status="confirmed", source=source))
        if hold:
            hold.converted_at = now
    order.status = "paid"
    order.paid_at = now
    order.stripe_payment_intent_id = payment_intent_id
    order.source = "stripe" if source == "stripe" else "manual"
    db.add(Payment(order_id=order.id, stripe_payment_intent_id=payment_intent_id, amount_cents=order.total_cents, status="succeeded", method=method))
    if order.waitlist_entry_id:
        entry = db.get(WaitlistEntry, order.waitlist_entry_id)
        if entry:
            entry.status = "converted"
    cart_svc.clear_cohorts(db, user.id, {it.cohort_id for it in order.items})
    db.flush()

    loc = order.locale
    tz = notify.user_tz(user)
    fulfilled = [by_id[it.cohort_id] for it in order.items if it not in overbooked]
    for c in fulfilled:
        db.refresh(c, attribute_names=["sessions"])
    jobs.send_email(
        db,
        "order_confirmed",
        user.email,
        loc,
        {
            "first_name": user.first_name,
            "order_number": order.number,
            "order_summary_html": notify.order_summary_html(order, loc),
            "schedule_html": notify.cohort_schedule_html(fulfilled, loc, tz),
            "dashboard_url": urls.front("dashboard", loc),
            "terms_url": urls.front("terms", loc),
            "refund_policy_url": urls.front("refunds", loc),
        },
        user_id=user.id,
        attachments=[{"kind": "invoice", "id": str(order.id)}, {"kind": "ics_order", "id": str(order.id)}],
        dedupe_key=f"order_confirmed:{order.id}",
    )
    order.confirmation_sent_at = now
    items_txt = "".join(f"<li>{it.title_fr} ({it.cohort_code})</li>" for it in order.items)
    notify.admin_alert(
        db,
        f"Nouvelle commande {order.number} — {money(order.total_cents, 'fr')}",
        f"<p>{user.full_name} ({user.email})</p><ul>{items_txt}</ul>",
        f"orders/{order.id}",
        dedupe_key=f"admin_new_order:{order.id}",
    )
    for c in fulfilled:
        if seats.remaining(db, c) <= 0 and not c.full_alert_sent_at:
            c.full_alert_sent_at = now
            notify.admin_alert(db, f"Cohorte complète : {c.code}", f"<p>{pick(c.bootcamp, 'title', 'fr')}</p>", f"cohorts/{c.id}")
    if overbooked and payment_intent_id:
        amount = sum(it.total_cents for it in overbooked)
        refund_order(db, order, amount, reason="overbooked", actor=None, item_ids=[it.id for it in overbooked], cancel_enrollments=False)
    db.flush()


def mark_failed(db: Session, order_id: uuid.UUID, message: str) -> None:
    order = _load_order(db, order_id, lock=True)
    if not order or order.status != "pending":
        return
    order.status = "failed"
    order.failure_message = message
    _release_holds(db, order.id)  # cart is kept, the student can retry (EF-47)


def mark_expired(db: Session, order_id: uuid.UUID) -> None:
    order = _load_order(db, order_id, lock=True)
    if not order or order.status != "pending":
        return
    order.status = "expired"
    if order.waitlist_entry_id:
        db.execute(update(SeatHold).where(SeatHold.order_id == order.id, SeatHold.waitlist_entry_id.isnot(None)).values(order_id=None))
    _release_holds(db, order.id)


def expire_stale_orders(db: Session) -> int:
    """Worker safety net when the `checkout.session.expired` webhook is missed."""
    cutoff = utcnow() - timedelta(minutes=35)
    stale = list(db.scalars(select(Order.id).where(Order.status == "pending", Order.created_at < cutoff)))
    for oid in stale:
        mark_expired(db, oid)
    return len(stale)


# --- Refunds (EF-65) -----------------------------------------------------------------


def refund_order(
    db: Session,
    order: Order,
    amount_cents: int,
    *,
    reason: str,
    actor: User | None,
    item_ids: list[uuid.UUID] | None = None,
    cancel_enrollments: bool = True,
    ip: str | None = None,
) -> Refund:
    if order.status not in ("paid", "partially_refunded"):
        raise AppError("order_not_refundable", 409)
    refundable = order.total_cents - order.refunded_cents
    if amount_cents <= 0 or amount_cents > refundable:
        raise AppError("refund_too_large", 422)
    before = audit.snapshot(order, ["status", "refunded_cents"])
    refund = Refund(order_id=order.id, amount_cents=amount_cents, reason=reason, created_by_id=actor.id if actor else None)
    db.add(refund)
    db.flush()
    if order.stripe_payment_intent_id:
        res = stripe_service.create_refund(
            order.stripe_payment_intent_id, amount_cents, idempotency_key=f"refund-{refund.id}",
            metadata={"order_id": str(order.id), "refund_id": str(refund.id)},
        )
        refund.stripe_refund_id = res["id"]
        refund.status = "succeeded" if res["status"] in ("succeeded", "pending") else res["status"]
    else:
        refund.status = "succeeded"  # free/manual order: nothing to send to Stripe
    _apply_refund_amounts(order, amount_cents, item_ids)

    cancelled_ids = []
    if cancel_enrollments:
        targets = item_ids or [it.id for it in order.items]
        for enr in db.scalars(select(Enrollment).where(Enrollment.order_item_id.in_(targets), Enrollment.status == "confirmed")):
            enr.status = "cancelled"
            enr.cancelled_at = utcnow()
            cancelled_ids.append(str(enr.id))
            waitlist.trigger(db, enr.cohort_id)
    refund.cancelled_enrollment_ids = cancelled_ids
    audit.log(db, actor.id if actor else None, "refund", "order", order.id, before, audit.snapshot(order, ["status", "refunded_cents"]) | {"amount": amount_cents, "reason": reason}, ip)

    user = db.get(User, order.user_id)
    loc = user.locale or order.locale
    method = next((p.method for p in order.payments if p.method), None) or ("Carte" if loc == "fr" else "Card")
    status_txt = (
        ("annulée" if loc == "fr" else "cancelled") if cancelled_ids else ("maintenue" if loc == "fr" else "kept")
    )
    jobs.send_email(
        db, "refund_processed", user.email, loc,
        {"first_name": user.first_name, "order_number": order.number, "amount": money(amount_cents, loc), "payment_method": method, "enrollment_status": status_txt},
        user_id=user.id, dedupe_key=f"refund:{refund.id}",
    )
    db.flush()
    return refund


def _apply_refund_amounts(order: Order, amount_cents: int, item_ids: list[uuid.UUID] | None) -> None:
    order.refunded_cents += amount_cents
    order.status = "refunded" if order.refunded_cents >= order.total_cents else "partially_refunded"
    # allocate to items (targeted items first, then proportionally) for revenue and tax reports
    remaining = amount_cents
    items = sorted(order.items, key=lambda it: 0 if item_ids and it.id in item_ids else 1)
    for it in items:
        if remaining <= 0:
            break
        room = it.total_cents - it.refunded_cents
        take = min(room, remaining)
        it.refunded_cents += take
        remaining -= take


def sync_dashboard_refund(db: Session, charge) -> None:
    """Refund made directly in the Stripe dashboard: mirror it on the order."""
    pi = charge.get("payment_intent")
    if not pi:
        return
    order = db.scalar(select(Order).where(Order.stripe_payment_intent_id == pi).with_for_update())
    if not order:
        return
    db.refresh(order, attribute_names=["items"])
    total_refunded = int(charge.get("amount_refunded") or 0)
    delta = total_refunded - order.refunded_cents
    if delta <= 0:
        return
    db.add(Refund(order_id=order.id, amount_cents=delta, status="succeeded", reason="stripe_dashboard"))
    _apply_refund_amounts(order, delta, None)
    audit.log(db, None, "refund_dashboard", "order", order.id, None, {"amount": delta})


def user_orders(db: Session, user_id: uuid.UUID) -> list[Order]:
    return list(
        db.scalars(
            select(Order)
            .where(Order.user_id == user_id, Order.status.in_(("paid", "partially_refunded", "refunded", "failed")))
            .options(selectinload(Order.items))
            .order_by(Order.created_at.desc())
        )
    )
