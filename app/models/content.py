import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class Testimonial(TimestampMixin, Base):
    __tablename__ = "testimonials"

    author_name: Mapped[str] = mapped_column(String(150))
    author_title_fr: Mapped[str] = mapped_column(String(200), default="")
    author_title_en: Mapped[str] = mapped_column(String(200), default="")
    quote_fr: Mapped[str] = mapped_column(Text)
    quote_en: Mapped[str] = mapped_column(Text, default="")
    bootcamp_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bootcamps.id", ondelete="SET NULL"))
    consent_obtained: Mapped[bool] = mapped_column(Boolean, default=False)  # only real testimonials, with consent
    consent_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    position: Mapped[int] = mapped_column(Integer, default=0)
    published: Mapped[bool] = mapped_column(Boolean, default=False)


class FaqItem(TimestampMixin, Base):
    __tablename__ = "faq_items"

    bootcamp_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bootcamps.id", ondelete="CASCADE"), index=True)
    theme_fr: Mapped[str] = mapped_column(String(150), default="")
    theme_en: Mapped[str] = mapped_column(String(150), default="")
    question_fr: Mapped[str] = mapped_column(String(500))
    question_en: Mapped[str] = mapped_column(String(500), default="")
    answer_fr: Mapped[str] = mapped_column(Text)
    answer_en: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    published: Mapped[bool] = mapped_column(Boolean, default=True)
    show_on_home: Mapped[bool] = mapped_column(Boolean, default=False)


class Page(TimestampMixin, Base):
    """Rich-text pages: about, terms, privacy, refunds."""

    __tablename__ = "pages"

    key: Mapped[str] = mapped_column(String(50), unique=True)
    title_fr: Mapped[str] = mapped_column(String(200))
    title_en: Mapped[str] = mapped_column(String(200), default="")
    body_fr: Mapped[str] = mapped_column(Text, default="")
    body_en: Mapped[str] = mapped_column(Text, default="")
    seo_description_fr: Mapped[str] = mapped_column(String(400), default="")
    seo_description_en: Mapped[str] = mapped_column(String(400), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)  # bumped on legal text change → consent version
    published: Mapped[bool] = mapped_column(Boolean, default=True)


class ContentBlock(TimestampMixin, Base):
    """Editable home page blocks (hero, how-it-works steps, ...)."""

    __tablename__ = "content_blocks"

    key: Mapped[str] = mapped_column(String(80), unique=True)
    value_fr: Mapped[str] = mapped_column(Text, default="")
    value_en: Mapped[str] = mapped_column(Text, default="")


class Consent(TimestampMixin, Base):
    __tablename__ = "consents"

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    visitor_id: Mapped[str | None] = mapped_column(String(64), index=True)
    email: Mapped[str | None] = mapped_column(String(320))
    type: Mapped[str] = mapped_column(String(30))  # terms | privacy | refund_policy | marketing | cookies_analytics | cookies_marketing
    version: Mapped[str] = mapped_column(String(20))
    granted: Mapped[bool] = mapped_column(Boolean)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    context: Mapped[str] = mapped_column(String(50), default="")  # checkout | newsletter | cookie_banner | profile


class NewsletterSubscriber(TimestampMixin, Base):
    __tablename__ = "newsletter_subscribers"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    locale: Mapped[str] = mapped_column(String(2), default="fr")
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    unsubscribe_token: Mapped[str] = mapped_column(String(64), unique=True)
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContactMessage(TimestampMixin, Base):
    __tablename__ = "contact_messages"

    name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(2), default="fr")
    handled: Mapped[bool] = mapped_column(Boolean, default=False)


class EmailTemplate(TimestampMixin, Base):
    """Admin overrides of transactional email subject/body (EF-69)."""

    __tablename__ = "email_templates"
    __table_args__ = (UniqueConstraint("key", "locale"),)

    key: Mapped[str] = mapped_column(String(60))
    locale: Mapped[str] = mapped_column(String(2))
    subject: Mapped[str] = mapped_column(String(300))
    body_html: Mapped[str] = mapped_column(Text)


class EmailLog(TimestampMixin, Base):
    __tablename__ = "email_log"

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    to_email: Mapped[str] = mapped_column(String(320))
    template: Mapped[str] = mapped_column(String(60), index=True)
    locale: Mapped[str] = mapped_column(String(2))
    subject: Mapped[str] = mapped_column(String(300), default="")
    provider_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | sent | failed
    error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(TimestampMixin, Base):
    __tablename__ = "audit_log"

    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(60), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))


class StripeEvent(TimestampMixin, Base):
    __tablename__ = "stripe_events"

    event_id: Mapped[str] = mapped_column(String(255), unique=True)
    type: Mapped[str] = mapped_column(String(100))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class SiteSettings(TimestampMixin, Base):
    """Single row of business settings editable in admin (EF-73)."""

    __tablename__ = "site_settings"

    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class Job(TimestampMixin, Base):
    """Background job queue processed by the worker (SELECT ... FOR UPDATE SKIP LOCKED)."""

    __tablename__ = "jobs"

    type: Mapped[str] = mapped_column(String(60), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued | running | done | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    last_error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PrivacyRequest(TimestampMixin, Base):
    """Access / rectification / deletion requests (Loi 25, EF-24, EF-89)."""

    __tablename__ = "privacy_requests"

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    type: Mapped[str] = mapped_column(String(20))  # deletion | rectification | access
    details: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | completed | rejected
    handled_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_note: Mapped[str] = mapped_column(Text, default="")


class PrivacyIncident(TimestampMixin, Base):
    """Confidentiality incident register (Loi 25, EF-91)."""

    __tablename__ = "privacy_incidents"

    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    description: Mapped[str] = mapped_column(Text)
    data_involved: Mapped[str] = mapped_column(Text, default="")
    people_affected: Mapped[int | None] = mapped_column(Integer)
    risk_of_serious_harm: Mapped[bool] = mapped_column(Boolean, default=False)
    measures_taken: Mapped[str] = mapped_column(Text, default="")
    cai_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    people_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | closed
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
