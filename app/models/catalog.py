import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin

LEVELS = ("beginner", "intermediate", "advanced")
BOOTCAMP_STATUSES = ("draft", "published", "archived")
COHORT_STATUSES = ("draft", "open", "closed", "cancelled")
SESSION_STATUSES = ("scheduled", "moved", "cancelled")


class Category(TimestampMixin, Base):
    __tablename__ = "categories"

    name_fr: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str] = mapped_column(String(100), default="")
    slug_fr: Mapped[str] = mapped_column(String(120), unique=True)
    slug_en: Mapped[str] = mapped_column(String(120), unique=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class Bootcamp(TimestampMixin, Base):
    __tablename__ = "bootcamps"

    slug_fr: Mapped[str] = mapped_column(String(160), unique=True)
    slug_en: Mapped[str] = mapped_column(String(160), unique=True)
    title_fr: Mapped[str] = mapped_column(String(200))
    title_en: Mapped[str] = mapped_column(String(200), default="")
    tagline_fr: Mapped[str] = mapped_column(String(300), default="")
    tagline_en: Mapped[str] = mapped_column(String(300), default="")
    summary_fr: Mapped[str] = mapped_column(Text, default="")
    summary_en: Mapped[str] = mapped_column(Text, default="")
    description_fr: Mapped[str] = mapped_column(Text, default="")  # rich text (HTML)
    description_en: Mapped[str] = mapped_column(Text, default="")
    outcomes_fr: Mapped[list] = mapped_column(JSONB, default=list)  # "Ce que vous apprendrez"
    outcomes_en: Mapped[list] = mapped_column(JSONB, default=list)
    prerequisites_fr: Mapped[str] = mapped_column(Text, default="")
    prerequisites_en: Mapped[str] = mapped_column(Text, default="")
    tools: Mapped[list] = mapped_column(JSONB, default=list)  # tool names are not translated
    preparation_fr: Mapped[str] = mapped_column(Text, default="")  # sent 7 days before start
    preparation_en: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[str] = mapped_column(String(20), default="beginner")
    duration_weeks: Mapped[int] = mapped_column(Integer, default=1)
    projects_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # hands-on projects to complete
    language: Mapped[str] = mapped_column(String(10), default="fr")  # teaching language: fr | en | bilingual
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"))
    instructor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    image_url: Mapped[str | None] = mapped_column(String(500))
    image_alt_fr: Mapped[str] = mapped_column(String(300), default="")
    image_alt_en: Mapped[str] = mapped_column(String(300), default="")
    keywords: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    taxable: Mapped[bool] = mapped_column(Boolean, default=True)
    seo_title_fr: Mapped[str] = mapped_column(String(200), default="")
    seo_title_en: Mapped[str] = mapped_column(String(200), default="")
    seo_description_fr: Mapped[str] = mapped_column(String(400), default="")
    seo_description_en: Mapped[str] = mapped_column(String(400), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)

    category: Mapped[Category | None] = relationship()
    syllabus: Mapped[list["SyllabusItem"]] = relationship(
        back_populates="bootcamp", cascade="all, delete-orphan", order_by="(SyllabusItem.week, SyllabusItem.position)"
    )
    cohorts: Mapped[list["Cohort"]] = relationship(back_populates="bootcamp", order_by="Cohort.start_date")


class SlugRedirect(TimestampMixin, Base):
    """Old slugs, so /fr/bootcamps/<old> answers 301 → new slug."""

    __tablename__ = "slug_redirects"
    __table_args__ = (UniqueConstraint("locale", "old_slug"),)

    bootcamp_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bootcamps.id", ondelete="CASCADE"))
    locale: Mapped[str] = mapped_column(String(2))
    old_slug: Mapped[str] = mapped_column(String(160))


class SyllabusItem(TimestampMixin, Base):
    __tablename__ = "syllabus_items"

    bootcamp_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bootcamps.id", ondelete="CASCADE"), index=True)
    week: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer, default=0)
    title_fr: Mapped[str] = mapped_column(String(200))
    title_en: Mapped[str] = mapped_column(String(200), default="")
    content_fr: Mapped[str] = mapped_column(Text, default="")
    content_en: Mapped[str] = mapped_column(Text, default="")

    bootcamp: Mapped[Bootcamp] = relationship(back_populates="syllabus")


class Cohort(TimestampMixin, Base):
    __tablename__ = "cohorts"

    bootcamp_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bootcamps.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    # [{"weekday": 1, "start": "18:30", "end": "21:00"}]  weekday: 0=Monday
    schedule: Mapped[list] = mapped_column(JSONB, default=list)
    time_zone: Mapped[str] = mapped_column(String(64), default="America/Toronto")
    capacity: Mapped[int] = mapped_column(Integer)
    price_cents: Mapped[int] = mapped_column(Integer)
    early_bird_price_cents: Mapped[int | None] = mapped_column(Integer)
    early_bird_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registration_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    instructor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    meeting_url: Mapped[str | None] = mapped_column(String(500))
    language: Mapped[str] = mapped_column(String(10), default="fr")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    prep_email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    full_alert_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    bootcamp: Mapped[Bootcamp] = relationship(back_populates="cohorts")
    sessions: Mapped[list["ClassSession"]] = relationship(
        back_populates="cohort", cascade="all, delete-orphan", order_by="ClassSession.starts_at"
    )


class ClassSession(TimestampMixin, Base):
    """A live class of a cohort (table "sessions" in the spec)."""

    __tablename__ = "sessions"

    cohort_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cohorts.id", ondelete="CASCADE"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    meeting_url_override: Mapped[str | None] = mapped_column(String(500))
    previous_starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sequence: Mapped[int] = mapped_column(Integer, default=0)  # iCalendar SEQUENCE, bumped on change
    reminder_24h_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_1h_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cohort: Mapped[Cohort] = relationship(back_populates="sessions")
