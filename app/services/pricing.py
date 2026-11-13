"""Cart quote: prices, multi-bootcamp discount (EF-35), promo codes (EF-36), taxes (EF-44)."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import AppError
from app.models import Cohort, Order, PromoCode
from app.services import seats, site_settings, taxes

PAID_STATUSES = ("paid", "partially_refunded", "refunded")


@dataclass
class Line:
    cohort: Cohort
    unit_price: int
    regular_price: int
    early_bird: bool
    multi_discount: int = 0
    promo_discount: int = 0
    tax_lines: list[dict] = field(default_factory=list)

    @property
    def discount(self) -> int:
        return self.multi_discount + self.promo_discount

    @property
    def net(self) -> int:
        return self.unit_price - self.discount

    @property
    def tax(self) -> int:
        return sum(t["amount"] for t in self.tax_lines)

    @property
    def total(self) -> int:
        return self.net + self.tax


@dataclass
class Quote:
    lines: list[Line]
    province: str | None
    country: str
    tax_estimated: bool
    multi_discount_percent: int
    promo: PromoCode | None
    promo_error: str | None
    tax_lines: list[dict]

    @property
    def subtotal(self) -> int:
        return sum(line.unit_price for line in self.lines)

    @property
    def multi_discount(self) -> int:
        return sum(line.multi_discount for line in self.lines)

    @property
    def promo_discount(self) -> int:
        return sum(line.promo_discount for line in self.lines)

    @property
    def discount(self) -> int:
        return self.multi_discount + self.promo_discount

    @property
    def tax(self) -> int:
        return sum(t["amount"] for t in self.tax_lines)

    @property
    def total(self) -> int:
        return self.subtotal - self.discount + self.tax


def paid_uses(db: Session, promo_id: uuid.UUID, user_id: uuid.UUID | None = None) -> int:
    q = select(func.count()).select_from(Order).where(Order.promo_code_id == promo_id, Order.status.in_(PAID_STATUSES))
    if user_id:
        q = q.where(Order.user_id == user_id)
    return db.scalar(q) or 0


def validate_promo(
    db: Session, promo: PromoCode, user_id: uuid.UUID | None, bootcamp_ids: list[uuid.UUID], now: datetime | None = None
) -> None:
    now = now or utcnow()
    if not promo.active or (promo.starts_at and now < promo.starts_at):
        raise AppError("promo_invalid")
    if promo.ends_at and now > promo.ends_at:
        raise AppError("promo_expired")
    if promo.max_uses is not None and paid_uses(db, promo.id) >= promo.max_uses:
        raise AppError("promo_used_up")
    if user_id and promo.per_user_limit and paid_uses(db, promo.id, user_id) >= promo.per_user_limit:
        raise AppError("promo_already_used")
    if promo.bootcamps and not {b.id for b in promo.bootcamps}.intersection(bootcamp_ids):
        raise AppError("promo_not_applicable")


def find_promo(db: Session, code: str) -> PromoCode | None:
    return db.scalar(select(PromoCode).where(PromoCode.code == code.strip().upper()))


def build_quote(
    db: Session,
    cohorts: list[Cohort],
    *,
    user_id: uuid.UUID | None = None,
    promo: PromoCode | None = None,
    province: str | None = None,
    country: str = "CA",
) -> Quote:
    cfg = site_settings.get_all(db)
    lines = []
    for c in cohorts:
        price, eb = seats.current_price(c)
        lines.append(Line(cohort=c, unit_price=price, regular_price=c.price_cents, early_bird=eb))

    # Multi-bootcamp discount (automatic)
    pct = 0
    if cfg["multi_discount_enabled"] and len(lines) >= int(cfg["multi_discount_min_items"]):
        pct = int(cfg["multi_discount_percent"])
        for line in lines:
            line.multi_discount = round(line.unit_price * pct / 100)

    # Promo code, applied after the multi-bootcamp discount, on eligible bootcamps only
    promo_error = None
    if promo is not None:
        try:
            validate_promo(db, promo, user_id, [c.bootcamp_id for c in cohorts])
        except AppError as e:
            promo_error = e.code
            promo = None
    if promo is not None:
        allowed = {b.id for b in promo.bootcamps}
        eligible = [ln for ln in lines if not allowed or ln.cohort.bootcamp_id in allowed]
        if promo.type == "percent":
            for line in eligible:
                line.promo_discount = round(line.net * min(promo.value, 100) / 100)
        else:
            base = sum(ln.net for ln in eligible)
            total_off = min(promo.value, base)
            allocated = 0
            for i, line in enumerate(eligible):
                share = total_off - allocated if i == len(eligible) - 1 else (round(total_off * line.net / base) if base else 0)
                line.promo_discount = share
                allocated += share

    # Taxes on the order's taxable total (one rounding per tax, as on the invoice),
    # then allocated to the taxable lines for per-bootcamp reporting.
    tax_on = bool(cfg["tax_enabled"])
    taxable_lines = [ln for ln in lines if tax_on and ln.cohort.bootcamp.taxable and ln.net > 0]
    base = sum(ln.net for ln in taxable_lines)
    order_taxes = taxes.compute(base, province, country) if base else []
    for t in order_taxes:
        allocated = 0
        for i, line in enumerate(taxable_lines):
            share = t["amount"] - allocated if i == len(taxable_lines) - 1 else round(t["amount"] * line.net / base)
            allocated += share
            line.tax_lines.append({"type": t["type"], "rate": t["rate"], "amount": share})
    return Quote(
        lines=lines,
        province=province,
        country=country,
        tax_estimated=province is None,
        multi_discount_percent=pct,
        promo=promo,
        promo_error=promo_error,
        tax_lines=order_taxes,
    )
