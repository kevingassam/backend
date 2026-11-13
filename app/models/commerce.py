import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin

ORDER_STATUSES = ("pending", "paid", "failed", "expired", "refunded", "partially_refunded", "cancelled")
ENROLLMENT_STATUSES = ("confirmed", "cancelled", "transferred", "completed")


class Cart(TimestampMixin, Base):
    __tablename__ = "carts"

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    token: Mapped[str | None] = mapped_column(String(64), unique=True)  # anonymous carts
    promo_code_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("promo_codes.id", ondelete="SET NULL"))

    items: Mapped[list["CartItem"]] = relationship(back_populates="cart", cascade="all, delete-orphan", order_by="CartItem.created_at")
    promo_code: Mapped["PromoCode | None"] = relationship()


class CartItem(TimestampMixin, Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("cart_id", "bootcamp_id"),)  # one cohort per bootcamp (EF-30)

    cart_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("carts.id", ondelete="CASCADE"), index=True)
    bootcamp_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bootcamps.id", ondelete="CASCADE"))
    cohort_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cohorts.id", ondelete="CASCADE"))

    cart: Mapped[Cart] = relationship(back_populates="items")
    cohort: Mapped["Cohort"] = relationship()  # type: ignore[name-defined]  # noqa: F821


class SeatHold(TimestampMixin, Base):
    __tablename__ = "seat_holds"

    cohort_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cohorts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    waitlist_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("waitlist_entries.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


promo_code_bootcamps = Table(
    "promo_code_bootcamps",
    Base.metadata,
    Column("promo_code_id", UUID(as_uuid=True), ForeignKey("promo_codes.id", ondelete="CASCADE"), primary_key=True),
    Column("bootcamp_id", UUID(as_uuid=True), ForeignKey("bootcamps.id", ondelete="CASCADE"), primary_key=True),
)


class PromoCode(TimestampMixin, Base):
    __tablename__ = "promo_codes"

    code: Mapped[str] = mapped_column(String(40), unique=True)  # stored uppercase
    type: Mapped[str] = mapped_column(String(10))  # percent | fixed
    value: Mapped[int] = mapped_column(Integer)  # percent (1-100) or cents
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_uses: Mapped[int | None] = mapped_column(Integer)
    per_user_limit: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(String(300), default="")

    bootcamps: Mapped[list["Bootcamp"]] = relationship(secondary=promo_code_bootcamps)  # type: ignore[name-defined]  # noqa: F821


class Order(TimestampMixin, Base):
    __tablename__ = "orders"

    number: Mapped[str] = mapped_column(String(20), unique=True)  # 2026-000123
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    locale: Mapped[str] = mapped_column(String(2), default="fr")
    subtotal_cents: Mapped[int] = mapped_column(Integer, default=0)
    multi_discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    promo_discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    tax_lines: Mapped[list] = mapped_column(JSONB, default=list)  # [{"type":"GST","label":"TPS","rate":"5","amount":1234}]
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0)
    province: Mapped[str | None] = mapped_column(String(2), index=True)
    billing_address: Mapped[dict] = mapped_column(JSONB, default=dict)  # snapshot kept for invoices
    billing_name: Mapped[str] = mapped_column(String(200), default="")
    billing_email: Mapped[str] = mapped_column(String(320), default="")
    promo_code_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("promo_codes.id", ondelete="SET NULL"))
    promo_code_text: Mapped[str | None] = mapped_column(String(40))
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255), index=True)
    waitlist_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("waitlist_entries.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(20), default="stripe")  # stripe | manual
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    failure_message: Mapped[str | None] = mapped_column(Text)
    confirmation_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.created_at")
    user: Mapped["User"] = relationship()  # type: ignore[name-defined]  # noqa: F821
    payments: Mapped[list["Payment"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    refunds: Mapped[list["Refund"]] = relationship(back_populates="order", cascade="all, delete-orphan", order_by="Refund.created_at")


class OrderItem(TimestampMixin, Base):
    __tablename__ = "order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    cohort_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cohorts.id", ondelete="RESTRICT"), index=True)
    bootcamp_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bootcamps.id", ondelete="RESTRICT"), index=True)
    title_fr: Mapped[str] = mapped_column(String(300))
    title_en: Mapped[str] = mapped_column(String(300))
    cohort_code: Mapped[str] = mapped_column(String(40))
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    multi_discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    promo_discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    net_cents: Mapped[int] = mapped_column(Integer)
    taxable: Mapped[bool] = mapped_column(Boolean, default=True)
    tax_lines: Mapped[list] = mapped_column(JSONB, default=list)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer)
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0)

    order: Mapped[Order] = relationship(back_populates="items")
    cohort: Mapped["Cohort"] = relationship()  # type: ignore[name-defined]  # noqa: F821


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"

    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255))
    stripe_charge_id: Mapped[str | None] = mapped_column(String(255))
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))  # succeeded | failed
    method: Mapped[str | None] = mapped_column(String(50))  # visa •••• 4242

    order: Mapped[Order] = relationship(back_populates="payments")


class Refund(TimestampMixin, Base):
    __tablename__ = "refunds"

    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    stripe_refund_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | succeeded | failed
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    cancelled_enrollment_ids: Mapped[list] = mapped_column(JSONB, default=list)

    order: Mapped[Order] = relationship(back_populates="refunds")


class Enrollment(TimestampMixin, Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("user_id", "cohort_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    cohort_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cohorts.id", ondelete="RESTRICT"), index=True)
    order_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("order_items.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="confirmed", index=True)
    source: Mapped[str] = mapped_column(String(20), default="stripe")  # stripe | manual | free | transfer
    note: Mapped[str] = mapped_column(Text, default="")
    transferred_to_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="SET NULL"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    certificate_number: Mapped[str | None] = mapped_column(String(40), unique=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cohort: Mapped["Cohort"] = relationship()  # type: ignore[name-defined]  # noqa: F821
    user: Mapped["User"] = relationship()  # type: ignore[name-defined]  # noqa: F821
    order_item: Mapped[OrderItem | None] = relationship()


class CancellationRequest(TimestampMixin, Base):
    __tablename__ = "cancellation_requests"

    enrollment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    handled_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_note: Mapped[str] = mapped_column(Text, default="")
    refund_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("refunds.id", ondelete="SET NULL"))

    enrollment: Mapped[Enrollment] = relationship()


class Attendance(TimestampMixin, Base):
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("session_id", "enrollment_id"),)

    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    enrollment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="CASCADE"))
    present: Mapped[bool] = mapped_column(Boolean, default=False)


class WaitlistEntry(TimestampMixin, Base):
    __tablename__ = "waitlist_entries"
    __table_args__ = (UniqueConstraint("cohort_id", "email"),)

    cohort_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cohorts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    email: Mapped[str] = mapped_column(String(320))
    first_name: Mapped[str] = mapped_column(String(100), default="")
    locale: Mapped[str] = mapped_column(String(2), default="fr")
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="waiting")  # waiting | offered | converted | expired | removed
    offered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offer_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offer_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)

    cohort: Mapped["Cohort"] = relationship()  # type: ignore[name-defined]  # noqa: F821


class Material(TimestampMixin, Base):
    __tablename__ = "materials"

    cohort_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cohorts.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(10))  # file | link
    storage_key: Mapped[str | None] = mapped_column(String(500))
    filename: Mapped[str | None] = mapped_column(String(300))
    content_type: Mapped[str | None] = mapped_column(String(150))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(String(1000))
    visible_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
