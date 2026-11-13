"""Public site content, contact form, newsletter, cookie consent, signed file downloads, health."""

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, text

from app.api.deps import DB, Locale, OptionalUser, rate_limit
from app.core.config import settings
from app.core.errors import AppError
from app.core.i18n import pick
from app.core.security import client_ip, verify_signed
from app.models import Bootcamp, Category, Consent, ContactMessage, ContentBlock, FaqItem, Page, Testimonial, User
from app.schemas.common import Loc, Ok
from app.services import catalog, jobs, newsletter, notify, site_settings, storage

router = APIRouter(tags=["public"])


@router.get("/health")
def health(db: DB) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/content/settings")
def public_settings(db: DB) -> dict:
    data = site_settings.public(db)
    pages = {p.key: p.version for p in db.scalars(select(Page))}
    return {
        **data,
        "google_enabled": bool(settings.google_client_id),
        "payments_enabled": bool(settings.stripe_secret_key),
        "legal_versions": pages,
    }


@router.get("/content/home")
def home(db: DB, loc: Locale) -> dict:
    blocks = {b.key: pick(b, "value", loc) for b in db.scalars(select(ContentBlock))}
    cards = catalog.list_cards(db, loc)
    featured = [c for c in cards if c["featured"]] or cards[:3]
    upcoming = []
    for c in cards:
        if c["next_cohort"] and c["next_cohort"]["status"] in ("open", "few_seats"):
            upcoming.append({"bootcamp": {"title": c["title"], "slug": c["slug"], "image_url": c["image_url"]}, "cohort": c["next_cohort"]})
    upcoming.sort(key=lambda x: x["cohort"]["start_date"])
    testimonials = db.scalars(
        select(Testimonial).where(Testimonial.published.is_(True), Testimonial.consent_obtained.is_(True)).order_by(Testimonial.position).limit(6)
    )
    faq = db.scalars(select(FaqItem).where(FaqItem.show_on_home.is_(True), FaqItem.published.is_(True), FaqItem.bootcamp_id.is_(None)).order_by(FaqItem.position).limit(5))
    return {
        "blocks": blocks,
        "featured": featured[:6],
        "upcoming": upcoming[:6],
        "testimonials": [catalog.testimonial_out(t, loc) for t in testimonials],
        "faq": [{"question": pick(f, "question", loc), "answer": pick(f, "answer", loc)} for f in faq],
    }


@router.get("/content/faq")
def faq(db: DB, loc: Locale) -> list[dict]:
    items = db.scalars(select(FaqItem).where(FaqItem.bootcamp_id.is_(None), FaqItem.published.is_(True)).order_by(FaqItem.position))
    groups: dict[str, list] = {}
    for f in items:
        groups.setdefault(pick(f, "theme", loc) or ("Général" if loc == "fr" else "General"), []).append(
            {"question": pick(f, "question", loc), "answer": pick(f, "answer", loc)}
        )
    return [{"theme": k, "items": v} for k, v in groups.items()]


@router.get("/content/pages/{key}")
def page(key: str, db: DB, loc: Locale) -> dict:
    p = db.scalar(select(Page).where(Page.key == key, Page.published.is_(True)))
    if not p:
        raise AppError("not_found", 404)
    out = {"key": p.key, "title": pick(p, "title", loc), "body": pick(p, "body", loc), "seo_description": pick(p, "seo_description", loc), "version": p.version, "updated_at": p.updated_at.isoformat()}
    if key == "about":
        instructors = db.scalars(select(User).where(User.show_on_about.is_(True), User.deleted_at.is_(None)).order_by(User.first_name))
        out["instructors"] = [{"name": u.full_name, "title": pick(u, "title", loc), "bio": pick(u, "bio", loc), "photo_url": u.photo_url} for u in instructors]
    if key == "privacy":
        cfg = site_settings.get_all(db)
        out["privacy_officer"] = {"name": cfg["privacy_officer_name"], "email": cfg["privacy_officer_email"]}  # EF-86
    return out


@router.get("/sitemap")
def sitemap(db: DB) -> dict:
    """Data for sitemap.xml with hreflang alternates (EF-85)."""
    bootcamps = db.scalars(select(Bootcamp).where(Bootcamp.status == "published"))
    cats = db.scalars(select(Category))
    return {
        "bootcamps": [{"fr": b.slug_fr, "en": b.slug_en, "updated_at": b.updated_at.isoformat()} for b in bootcamps],
        "categories": [{"fr": c.slug_fr, "en": c.slug_en} for c in cats],
    }


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    email: EmailStr
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=5, max_length=5000)
    locale: Loc = "fr"
    website: str | None = None  # honeypot: must stay empty
    elapsed_ms: int | None = None  # time spent on the form; bots submit instantly


@router.post("/contact", response_model=Ok, dependencies=[rate_limit("contact", 5, 3600)])
def contact(body: ContactIn, db: DB) -> Ok:
    if body.website or (body.elapsed_ms is not None and body.elapsed_ms < 2000):
        raise AppError("spam_detected", 400)
    msg = ContactMessage(name=body.name, email=body.email, subject=body.subject, message=body.message, locale=body.locale)
    db.add(msg)
    db.flush()
    import html as _h

    notify.admin_alert(
        db, f"Formulaire de contact : {body.subject}",
        f"<p><strong>{_h.escape(body.name)}</strong> &lt;{_h.escape(body.email)}&gt;</p><p>{_h.escape(body.message).replace(chr(10), '<br>')}</p>",
        "messages",
    )
    jobs.send_email(db, "contact_autoreply", body.email, body.locale, {"name": body.name, "subject": body.subject})
    db.commit()
    return Ok()


class NewsletterIn(BaseModel):
    email: EmailStr
    locale: Loc = "fr"
    consent: bool  # express consent checkbox, unchecked by default (EF-95)
    website: str | None = None


@router.post("/newsletter", response_model=Ok, dependencies=[rate_limit("newsletter", 10, 3600)])
def subscribe(body: NewsletterIn, request: Request, db: DB, user: OptionalUser) -> Ok:
    if body.website:
        raise AppError("spam_detected", 400)
    if not body.consent:
        raise AppError("validation_error", 422, details=[{"field": "consent", "message": "required"}])
    sub = newsletter.subscribe(db, body.email, body.locale, user.id if user else None)
    db.add(Consent(user_id=user.id if user else None, email=sub.email, type="marketing", version="1", granted=True, ip=client_ip(request), user_agent=request.headers.get("user-agent", "")[:500], context="newsletter"))
    if user and user.email == sub.email:
        user.marketing_opt_in = True
    db.commit()
    return Ok()


class TokenIn(BaseModel):
    token: str


@router.post("/newsletter/unsubscribe", response_model=Ok)
def unsubscribe(body: TokenIn, db: DB) -> Ok:
    if not newsletter.unsubscribe(db, body.token):
        raise AppError("invalid_token", 400)
    db.commit()
    return Ok()


class CookieConsentIn(BaseModel):
    visitor_id: str = Field(min_length=8, max_length=64)
    analytics: bool
    marketing: bool
    version: str = Field(default="1", max_length=20)


@router.post("/consents/cookies", response_model=Ok, dependencies=[rate_limit("consent", 30, 3600)])
def cookie_consent(body: CookieConsentIn, request: Request, db: DB, user: OptionalUser) -> Ok:
    """EF-87/EF-88: keep proof of cookie choices (date, version, IP)."""
    for ctype, granted in (("cookies_analytics", body.analytics), ("cookies_marketing", body.marketing)):
        db.add(Consent(user_id=user.id if user else None, visitor_id=body.visitor_id, type=ctype, version=body.version, granted=granted, ip=client_ip(request), user_agent=request.headers.get("user-agent", "")[:500], context="cookie_banner"))
    db.commit()
    return Ok()


@router.get("/files/{key:path}", include_in_schema=False)
def signed_file(key: str, expires: int, sig: str) -> FileResponse:
    """Private course material through a short-lived signed URL (EF-52)."""
    if not verify_signed(f"/api/v1/files/{key}", expires, sig):
        raise AppError("forbidden", 403)
    path = storage.private_path(key)
    if not path.exists():
        raise AppError("not_found", 404)
    filename = path.name.split("-", 1)[1] if "-" in path.name else path.name
    return FileResponse(path, filename=filename, headers={"Cache-Control": "private, no-store"})
