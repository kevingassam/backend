"""Public catalog serialization: cards (EF-01..EF-04) and bootcamp page (EF-10, EF-11)."""

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.db import utcnow
from app.core.i18n import pick
from app.models import Bootcamp, ClassSession, Cohort, Enrollment, FaqItem, Testimonial, User
from app.services import calendar, seats, site_settings

VISIBLE_COHORT = ("open", "closed")


def load_published(db: Session) -> list[Bootcamp]:
    return list(
        db.scalars(
            select(Bootcamp)
            .where(Bootcamp.status == "published")
            .options(selectinload(Bootcamp.cohorts), selectinload(Bootcamp.category))
            .order_by(Bootcamp.position, Bootcamp.title_fr)
        )
    )


def upcoming_cohorts(b: Bootcamp, today: date | None = None) -> list[Cohort]:
    today = today or utcnow().date()
    return [c for c in b.cohorts if c.status in VISIBLE_COHORT and c.end_date >= today]


def popularity(db: Session, bootcamp_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not bootcamp_ids:
        return {}
    rows = db.execute(
        select(Cohort.bootcamp_id, func.count(Enrollment.id))
        .join(Enrollment, Enrollment.cohort_id == Cohort.id)
        .where(Cohort.bootcamp_id.in_(bootcamp_ids), Enrollment.status.in_(("confirmed", "completed")))
        .group_by(Cohort.bootcamp_id)
    ).all()
    return dict(rows)


def cohort_brief(c: Cohort, seats_left: int, few: int, locale: str) -> dict:
    price, eb = seats.current_price(c)
    return {
        "id": str(c.id),
        "code": c.code,
        "start_date": c.start_date.isoformat(),
        "end_date": c.end_date.isoformat(),
        "status": seats.display_status(c, seats_left, few),
        "seats_left": seats_left,
        "price": price,
        "regular_price": c.price_cents,
        "early_bird_active": eb,
        "early_bird_until": c.early_bird_until.isoformat() if eb and c.early_bird_until else None,
        "registration_deadline": c.registration_deadline.isoformat(),
        "language": c.language,
        "schedule": c.schedule,
        "schedule_text": calendar.schedule_text(c, locale),
        "time_zone": c.time_zone,
    }


def card(b: Bootcamp, locale: str, seats_left: dict[uuid.UUID, int], few: int, pop: dict) -> dict:
    ups = upcoming_cohorts(b)
    briefs = [cohort_brief(c, seats_left.get(c.id, 0), few, locale) for c in ups]
    buyable = [x for x in briefs if x["status"] in ("open", "few_seats")]
    nxt = buyable[0] if buyable else (briefs[0] if briefs else None)
    prices = [x["price"] for x in (buyable or briefs)]
    other = "en" if locale == "fr" else "fr"
    return {
        "id": str(b.id),
        "slug": getattr(b, f"slug_{locale}"),
        "alt_slug": getattr(b, f"slug_{other}"),
        "title": pick(b, "title", locale),
        "tagline": pick(b, "tagline", locale),
        "summary": pick(b, "summary", locale),
        "image_url": b.image_url,
        "image_alt": pick(b, "image_alt", locale) or pick(b, "title", locale),
        "level": b.level,
        "duration_weeks": b.duration_weeks,
        "language": b.language,
        "featured": b.featured,
        "category": {"slug": getattr(b.category, f"slug_{locale}"), "name": pick(b.category, "name", locale)} if b.category else None,
        "next_cohort": nxt,
        "price_from": min(prices) if prices else None,
        "few_seats": bool(nxt and nxt["status"] == "few_seats"),  # EF-01 badge
        "popularity": pop.get(b.id, 0),
        "keywords": b.keywords,
    }


def list_cards(db: Session, locale: str) -> list[dict]:
    bootcamps = load_published(db)
    all_cohorts = [c for b in bootcamps for c in upcoming_cohorts(b)]
    left = seats.seats_left_bulk(db, all_cohorts)
    few = int(site_settings.get(db, "few_seats_threshold"))
    pop = popularity(db, [b.id for b in bootcamps])
    return [card(b, locale, left, few, pop) for b in bootcamps]


def find_by_slug(db: Session, slug: str, locale: str, include_drafts: bool = False) -> Bootcamp | None:
    col = Bootcamp.slug_fr if locale == "fr" else Bootcamp.slug_en
    q = select(Bootcamp).where(col == slug)
    if not include_drafts:
        q = q.where(Bootcamp.status == "published")
    return db.scalar(q)


def detail(db: Session, b: Bootcamp, locale: str) -> dict:
    db.refresh(b, attribute_names=["cohorts", "syllabus", "category"])
    few = int(site_settings.get(db, "few_seats_threshold"))
    ups = upcoming_cohorts(b)
    left = seats.seats_left_bulk(db, ups)
    pop = popularity(db, [b.id])
    base = card(b, locale, left, few, pop)
    session_rows = db.execute(
        select(ClassSession.cohort_id, ClassSession.starts_at, ClassSession.ends_at)
        .where(ClassSession.cohort_id.in_([c.id for c in ups]), ClassSession.status != "cancelled")
        .order_by(ClassSession.starts_at)
    ).all()
    sessions_by: dict[uuid.UUID, list] = {}
    for cid, s, e in session_rows:
        sessions_by.setdefault(cid, []).append({"starts_at": s.isoformat(), "ends_at": e.isoformat()})
    cohorts = []
    for c in ups:
        brief = cohort_brief(c, left.get(c.id, 0), few, locale)
        brief["sessions"] = sessions_by.get(c.id, [])
        brief["sessions_count"] = len(brief["sessions"])
        brief["instructor_name"] = None
        if c.instructor_id:
            inst = db.get(User, c.instructor_id)
            brief["instructor_name"] = inst.full_name if inst else None
        cohorts.append(brief)
    weeks: dict[int, list] = {}
    for it in b.syllabus:
        weeks.setdefault(it.week, []).append({"title": pick(it, "title", locale), "content": pick(it, "content", locale)})
    instructor = None
    if b.instructor_id:
        u = db.get(User, b.instructor_id)
        if u and not u.deleted_at:
            instructor = {"name": u.full_name, "title": pick(u, "title", locale), "bio": pick(u, "bio", locale), "photo_url": u.photo_url}
    faq = db.scalars(select(FaqItem).where(FaqItem.bootcamp_id == b.id, FaqItem.published.is_(True)).order_by(FaqItem.position))
    testimonials = db.scalars(
        select(Testimonial).where(Testimonial.bootcamp_id == b.id, Testimonial.published.is_(True), Testimonial.consent_obtained.is_(True)).order_by(Testimonial.position)
    )
    outcomes = getattr(b, f"outcomes_{locale}") or b.outcomes_fr
    return {
        **base,
        "slugs": {"fr": b.slug_fr, "en": b.slug_en},
        "status": b.status,
        "description": pick(b, "description", locale),
        "outcomes": outcomes,
        "prerequisites": pick(b, "prerequisites", locale),
        "tools": b.tools,
        "syllabus": [{"week": w, "items": items} for w, items in sorted(weeks.items())],
        "instructor": instructor,
        "cohorts": cohorts,
        "faq": [{"question": pick(f, "question", locale), "answer": pick(f, "answer", locale)} for f in faq],
        "testimonials": [testimonial_out(t, locale) for t in testimonials],
        "seo": {"title": pick(b, "seo_title", locale) or pick(b, "title", locale), "description": pick(b, "seo_description", locale) or pick(b, "summary", locale)},
        "updated_at": b.updated_at.isoformat(),
    }


def testimonial_out(t: Testimonial, locale: str) -> dict:
    return {"id": str(t.id), "author_name": t.author_name, "author_title": pick(t, "author_title", locale), "quote": pick(t, "quote", locale)}
