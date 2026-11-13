"""Public catalog: categories, bootcamps with filters/search/sort, bootcamp page, cohort detail, waitlist."""

import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_, select

from app.api.deps import DB, CurrentUser, Locale, OptionalUser, rate_limit
from app.core.errors import AppError
from app.core.i18n import fmt_datetime, pick
from app.core.security import client_ip
from app.models import Bootcamp, Category, Cohort, SlugRedirect
from app.schemas.common import AddressIn, Loc, validate_canadian_province
from app.services import catalog, orders, seats, site_settings, waitlist
from app.services.legal import consent_versions

router = APIRouter(tags=["catalog"])


@router.get("/categories")
def categories(db: DB, loc: Locale) -> list[dict]:
    rows = db.scalars(select(Category).order_by(Category.position, Category.name_fr))
    return [{"id": str(c.id), "slug": getattr(c, f"slug_{loc}"), "alt_slug": getattr(c, f"slug_{'en' if loc == 'fr' else 'fr'}"), "name": pick(c, "name", loc)} for c in rows]


@router.get("/bootcamps")
def list_bootcamps(
    db: DB,
    loc: Locale,
    category: str | None = None,
    level: Literal["beginner", "intermediate", "advanced"] | None = None,
    language: Literal["fr", "en", "bilingual"] | None = None,
    start_month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    price_min: int | None = Query(default=None, ge=0, description="dollars"),
    price_max: int | None = Query(default=None, ge=0, description="dollars"),
    q: str | None = Query(default=None, max_length=100),
    featured: bool | None = None,
    sort: Literal["start", "price", "price_desc", "popularity"] = "start",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=100),
) -> dict:
    cards = catalog.list_cards(db, loc)
    if category:
        cards = [c for c in cards if c["category"] and c["category"]["slug"] == category]
    if level:
        cards = [c for c in cards if c["level"] == level]
    if language:
        cards = [c for c in cards if c["language"] == language]
    if featured is not None:
        cards = [c for c in cards if c["featured"] == featured]
    if start_month:
        bootcamps = {b.id: b for b in catalog.load_published(db)}
        cards = [
            c for c in cards
            if any(co.start_date.strftime("%Y-%m") == start_month for co in catalog.upcoming_cohorts(bootcamps[uuid.UUID(c["id"])]))
        ]
    if price_min is not None:
        cards = [c for c in cards if c["price_from"] is not None and c["price_from"] >= price_min * 100]
    if price_max is not None:
        cards = [c for c in cards if c["price_from"] is not None and c["price_from"] <= price_max * 100]
    if q:
        needle = q.strip().lower()
        cards = [
            c for c in cards
            if needle in c["title"].lower() or needle in c["summary"].lower() or needle in c["tagline"].lower()
            or any(needle in str(k).lower() for k in c["keywords"])
        ]
    far = "9999-12-31"
    if sort == "start":
        cards.sort(key=lambda c: c["next_cohort"]["start_date"] if c["next_cohort"] else far)
    elif sort == "price":
        cards.sort(key=lambda c: c["price_from"] if c["price_from"] is not None else 10**12)
    elif sort == "price_desc":
        cards.sort(key=lambda c: -(c["price_from"] or 0))
    else:
        cards.sort(key=lambda c: -c["popularity"])
    total = len(cards)
    start = (page - 1) * page_size
    return {"items": cards[start : start + page_size], "total": total, "page": page, "page_size": page_size}


@router.get("/bootcamps/{slug}")
def get_bootcamp(slug: str, db: DB, loc: Locale) -> dict:
    b = catalog.find_by_slug(db, slug, loc)
    if b:
        return catalog.detail(db, b, loc)
    # translated slug of the other language, or a former slug → tell the frontend to 301
    other = db.scalar(select(Bootcamp).where(or_(Bootcamp.slug_fr == slug, Bootcamp.slug_en == slug), Bootcamp.status == "published"))
    if not other:
        red = db.scalar(select(SlugRedirect).where(SlugRedirect.old_slug == slug))
        other = db.get(Bootcamp, red.bootcamp_id) if red else None
    if other and other.status == "published":
        raise AppError("not_found", 404, details={"redirect_slug": getattr(other, f"slug_{loc}")})
    raise AppError("not_found", 404)


@router.get("/cohorts/{cohort_id}")
def get_cohort(cohort_id: uuid.UUID, db: DB, loc: Locale) -> dict:
    c = db.get(Cohort, cohort_id)
    if not c or c.bootcamp.status != "published" or c.status not in catalog.VISIBLE_COHORT:
        raise AppError("not_found", 404)
    few = int(site_settings.get(db, "few_seats_threshold"))
    out = catalog.cohort_brief(c, seats.remaining(db, c), few, loc)
    out["bootcamp"] = {"id": str(c.bootcamp_id), "title": pick(c.bootcamp, "title", loc), "slug": getattr(c.bootcamp, f"slug_{loc}")}
    return out


class WaitlistIn(BaseModel):
    email: EmailStr | None = None
    first_name: str = Field(default="", max_length=100)
    locale: Loc = "fr"
    website: str | None = None  # honeypot


@router.post("/cohorts/{cohort_id}/waitlist", status_code=201, dependencies=[rate_limit("waitlist", 10, 3600)])
def join_waitlist(cohort_id: uuid.UUID, body: WaitlistIn, db: DB, user: OptionalUser) -> dict:
    if body.website:
        raise AppError("spam_detected", 400)
    c = db.get(Cohort, cohort_id)
    if not c:
        raise AppError("not_found", 404)
    email = user.email if user else body.email
    if not email:
        raise AppError("validation_error", 422, details=[{"field": "email", "message": "required"}])
    entry = waitlist.join(db, c, email=email, first_name=(user.first_name if user else body.first_name), locale=(user.locale if user else body.locale), user=user)
    db.commit()
    return {"position": waitlist.waiting_rank(db, entry)}


@router.get("/waitlist/offers/{token}")
def waitlist_offer(token: str, db: DB, loc: Locale, user: OptionalUser) -> dict:
    entry = waitlist.find_offer(db, token)
    c = entry.cohort
    price, _ = seats.current_price(c)
    tz = user.time_zone if user else "America/Toronto"
    return {
        "cohort_id": str(c.id),
        "cohort_code": c.code,
        "bootcamp_title": pick(c.bootcamp, "title", loc),
        "bootcamp_slug": getattr(c.bootcamp, f"slug_{loc}"),
        "start_date": c.start_date.isoformat(),
        "end_date": c.end_date.isoformat(),
        "price": price,
        "email": entry.email,
        "expires_at": entry.offer_expires_at.isoformat(),
        "expires_text": fmt_datetime(entry.offer_expires_at, loc, tz),
    }


class OfferCheckoutIn(BaseModel):
    billing_address: AddressIn
    accept_terms: bool
    marketing_opt_in: bool = False
    locale: Loc = "fr"


@router.post("/waitlist/offers/{token}/checkout", dependencies=[rate_limit("checkout", 20, 600)])
def waitlist_offer_checkout(token: str, body: OfferCheckoutIn, request: Request, db: DB, user: CurrentUser) -> dict:
    entry = waitlist.find_offer(db, token)
    if entry.email.lower() != user.email.lower():
        raise AppError("offer_invalid", 403)
    validate_canadian_province(body.billing_address)
    return orders.checkout_waitlist_offer(
        db, user, entry, address=body.billing_address.model_dump(), accept_terms=body.accept_terms,
        marketing_opt_in=body.marketing_opt_in, locale=body.locale, ip=client_ip(request),
        ua=request.headers.get("user-agent", "")[:500], consent_versions=consent_versions(db),
    )
