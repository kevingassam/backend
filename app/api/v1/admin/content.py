"""Admin: site content (EF-68), email templates (EF-69), email log, audit log (EF-72)."""

import uuid
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, AdminUser
from app.api.v1.admin.common import dt, paginate
from app.core.db import utcnow
from app.core.errors import AppError
from app.core.html import clean
from app.models import AuditLog, ContentBlock, EmailLog, EmailTemplate, FaqItem, Page, Testimonial, User
from app.schemas.common import Ok
from app.services import emails
from app.services.email_templates import TEMPLATES

router = APIRouter(tags=["admin"])


def _cols(obj) -> dict:
    out = {}
    for c in obj.__table__.columns:
        v = getattr(obj, c.key)
        out[c.key] = str(v) if isinstance(v, uuid.UUID) else (dt(v) if hasattr(v, "isoformat") else v)
    return out


# --- Testimonials (real ones only, with the student's consent) ---------------------------


class TestimonialIn(BaseModel):
    author_name: str = Field(min_length=1, max_length=150)
    author_title_fr: str = ""
    author_title_en: str = ""
    quote_fr: str = Field(min_length=1)
    quote_en: str = ""
    bootcamp_id: uuid.UUID | None = None
    consent_obtained: bool = False
    position: int = 0
    published: bool = False


@router.get("/admin/testimonials")
def list_testimonials(db: DB, _: AdminUser) -> list[dict]:
    return [_cols(t) for t in db.scalars(select(Testimonial).order_by(Testimonial.position))]


def _save_testimonial(t: Testimonial, body: TestimonialIn) -> None:
    if body.published and not body.consent_obtained:
        raise AppError("validation_error", 422, details=[{"field": "consent_obtained", "message": "consent required to publish"}])
    if body.consent_obtained and not t.consent_obtained:
        t.consent_date = utcnow()
    for k, v in body.model_dump().items():
        setattr(t, k, v)


@router.post("/admin/testimonials", status_code=201)
def create_testimonial(body: TestimonialIn, db: DB, _: AdminUser) -> dict:
    t = Testimonial(author_name=body.author_name, quote_fr=body.quote_fr)
    _save_testimonial(t, body)
    db.add(t)
    db.commit()
    return _cols(t)


@router.put("/admin/testimonials/{tid}")
def update_testimonial(tid: uuid.UUID, body: TestimonialIn, db: DB, _: AdminUser) -> dict:
    t = db.get(Testimonial, tid)
    if not t:
        raise AppError("not_found", 404)
    _save_testimonial(t, body)
    db.commit()
    return _cols(t)


@router.delete("/admin/testimonials/{tid}", response_model=Ok)
def delete_testimonial(tid: uuid.UUID, db: DB, _: AdminUser) -> Ok:
    t = db.get(Testimonial, tid)
    if t:
        db.delete(t)
        db.commit()
    return Ok()


# --- General FAQ --------------------------------------------------------------------------------


class FaqIn(BaseModel):
    theme_fr: str = ""
    theme_en: str = ""
    question_fr: str = Field(min_length=1, max_length=500)
    question_en: str = ""
    answer_fr: str = Field(min_length=1)
    answer_en: str = ""
    position: int = 0
    published: bool = True
    show_on_home: bool = False


def _faq_data(body: "FaqIn") -> dict:
    data = body.model_dump()
    data["answer_fr"], data["answer_en"] = clean(data["answer_fr"]), clean(data["answer_en"])
    return data


@router.get("/admin/faq")
def list_faq(db: DB, _: AdminUser) -> list[dict]:
    return [_cols(f) for f in db.scalars(select(FaqItem).where(FaqItem.bootcamp_id.is_(None)).order_by(FaqItem.position))]


@router.post("/admin/faq", status_code=201)
def create_faq(body: FaqIn, db: DB, _: AdminUser) -> dict:
    f = FaqItem(**_faq_data(body))
    db.add(f)
    db.commit()
    return _cols(f)


@router.put("/admin/faq/{fid}")
def update_faq(fid: uuid.UUID, body: FaqIn, db: DB, _: AdminUser) -> dict:
    f = db.get(FaqItem, fid)
    if not f:
        raise AppError("not_found", 404)
    for k, v in _faq_data(body).items():
        setattr(f, k, v)
    db.commit()
    return _cols(f)


@router.delete("/admin/faq/{fid}", response_model=Ok)
def delete_faq(fid: uuid.UUID, db: DB, _: AdminUser) -> Ok:
    f = db.get(FaqItem, fid)
    if f:
        db.delete(f)
        db.commit()
    return Ok()


# --- Pages (about, legal) --------------------------------------------------------------------


class PageIn(BaseModel):
    title_fr: str = Field(min_length=1, max_length=200)
    title_en: str = ""
    body_fr: str = ""
    body_en: str = ""
    seo_description_fr: str = ""
    seo_description_en: str = ""
    published: bool = True
    new_version: bool = False  # bump the legal version: new consents reference it


@router.get("/admin/pages")
def list_pages(db: DB, _: AdminUser) -> list[dict]:
    return [_cols(p) for p in db.scalars(select(Page).order_by(Page.key))]


@router.put("/admin/pages/{key}")
def save_page(key: Literal["about", "terms", "privacy", "refunds"], body: PageIn, db: DB, _: AdminUser) -> dict:
    p = db.scalar(select(Page).where(Page.key == key))
    if not p:
        p = Page(key=key, title_fr=body.title_fr)
        db.add(p)
    data = body.model_dump(exclude={"new_version"})
    data["body_fr"], data["body_en"] = clean(data["body_fr"]), clean(data["body_en"])
    for k, v in data.items():
        setattr(p, k, v)
    if body.new_version:
        p.version = (p.version or 1) + 1
    db.commit()
    return _cols(p)


# --- Home blocks ----------------------------------------------------------------------------------


class BlockIn(BaseModel):
    value_fr: str = ""
    value_en: str = ""


@router.get("/admin/blocks")
def list_blocks(db: DB, _: AdminUser) -> list[dict]:
    return [_cols(b) for b in db.scalars(select(ContentBlock).order_by(ContentBlock.key))]


@router.put("/admin/blocks/{key}")
def save_block(key: str, body: BlockIn, db: DB, _: AdminUser) -> dict:
    b = db.scalar(select(ContentBlock).where(ContentBlock.key == key))
    if not b:
        b = ContentBlock(key=key[:80])
        db.add(b)
    b.value_fr, b.value_en = body.value_fr, body.value_en
    db.commit()
    return _cols(b)


# --- Email templates (EF-69) ---------------------------------------------------------------------


@router.get("/admin/email-templates")
def list_templates(db: DB, _: AdminUser) -> list[dict]:
    overrides = {(t.key, t.locale): t for t in db.scalars(select(EmailTemplate))}
    out = []
    for key, tpl in TEMPLATES.items():
        entry = {"key": key, "variables": tpl["variables"]}
        for loc in ("fr", "en"):
            o = overrides.get((key, loc))
            entry[loc] = {"subject": o.subject if o else tpl[loc][0], "body_html": o.body_html if o else tpl[loc][1], "customized": o is not None}
        out.append(entry)
    return out


class TemplateIn(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body_html: str = Field(min_length=1)


@router.put("/admin/email-templates/{key}/{loc}", response_model=Ok)
def save_template(key: str, loc: Literal["fr", "en"], body: TemplateIn, db: DB, _: AdminUser) -> Ok:
    if key not in TEMPLATES:
        raise AppError("not_found", 404)
    t = db.scalar(select(EmailTemplate).where(EmailTemplate.key == key, EmailTemplate.locale == loc))
    if not t:
        t = EmailTemplate(key=key, locale=loc, subject=body.subject, body_html=body.body_html)
        db.add(t)
    t.subject, t.body_html = body.subject, clean(body.body_html)
    db.commit()
    return Ok()


@router.delete("/admin/email-templates/{key}/{loc}", response_model=Ok)
def reset_template(key: str, loc: Literal["fr", "en"], db: DB, _: AdminUser) -> Ok:
    t = db.scalar(select(EmailTemplate).where(EmailTemplate.key == key, EmailTemplate.locale == loc))
    if t:
        db.delete(t)
        db.commit()
    return Ok()


@router.post("/admin/email-templates/{key}/{loc}/preview")
def preview_template(key: str, loc: Literal["fr", "en"], db: DB, _: AdminUser) -> dict:
    if key not in TEMPLATES:
        raise AppError("not_found", 404)
    sample = {v: (f"<em>{v}</em>" if v.endswith("_html") else f"[{v}]") for v in TEMPLATES[key]["variables"]}
    subject, html, text = emails.render(db, key, loc, sample)
    return {"subject": subject, "html": html, "text": text}


# --- Logs -------------------------------------------------------------------------------------------


@router.get("/admin/email-log")
def email_log(db: DB, _: AdminUser, status: str | None = None, q: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, le=200)) -> dict:
    query = select(EmailLog).order_by(EmailLog.created_at.desc())
    if status:
        query = query.where(EmailLog.status == status)
    if q:
        query = query.where(EmailLog.to_email.ilike(f"%{q}%"))
    return paginate(db, query, page, page_size, lambda e: {"id": str(e.id), "to": e.to_email, "template": e.template, "locale": e.locale, "subject": e.subject, "status": e.status, "error": e.error, "sent_at": dt(e.sent_at), "created_at": dt(e.created_at)})


@router.get("/admin/audit-log")
def audit_log(db: DB, _: AdminUser, entity: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, le=200)) -> dict:
    query = select(AuditLog).order_by(AuditLog.created_at.desc())
    if entity:
        query = query.where(AuditLog.entity == entity)
    actors: dict = {}

    def m(a: AuditLog) -> dict:
        if a.actor_id and a.actor_id not in actors:
            u = db.get(User, a.actor_id)
            actors[a.actor_id] = u.email if u else None
        return {"id": str(a.id), "actor": actors.get(a.actor_id) if a.actor_id else "système", "action": a.action, "entity": a.entity, "entity_id": a.entity_id, "before": a.before, "after": a.after, "ip": a.ip, "created_at": dt(a.created_at)}

    return paginate(db, query, page, page_size, m)
