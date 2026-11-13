"""Email context builders and admin alerts."""

import html

from sqlalchemy.orm import Session

from app.core import urls
from app.core.config import settings
from app.core.i18n import fmt_date, fmt_datetime, money, pick
from app.models import Cohort, Order, User
from app.services import calendar, jobs, taxes


def admin_alert(db: Session, title: str, body_html: str, admin_path: str, dedupe_key: str | None = None) -> None:
    jobs.send_email(
        db,
        "admin_alert",
        settings.admin_alert_email,
        "fr",
        {"alert_title": title, "alert_body_html": body_html, "admin_url": urls.admin(admin_path)},
        dedupe_key=dedupe_key,
    )


def order_summary_html(order: Order, locale: str) -> str:
    rows = []
    for it in order.items:
        title = html.escape(it.title_fr if locale == "fr" else (it.title_en or it.title_fr))
        rows.append(f'<tr><td>{title} ({html.escape(it.cohort_code)})</td><td class="amount">{money(it.unit_price_cents, locale)}</td></tr>')
    if order.multi_discount_cents:
        label = "Rabais multi-bootcamps" if locale == "fr" else "Multi-bootcamp discount"
        rows.append(f'<tr><td>{label}</td><td class="amount">{money(-order.multi_discount_cents, locale)}</td></tr>')
    if order.promo_discount_cents:
        label = ("Code promo" if locale == "fr" else "Promo code") + f" {html.escape(order.promo_code_text or '')}"
        rows.append(f'<tr><td>{label}</td><td class="amount">{money(-order.promo_discount_cents, locale)}</td></tr>')
    for t in order.tax_lines:
        rows.append(
            f'<tr><td>{taxes.label(t["type"], locale)} ({taxes.fmt_rate(t["rate"], locale)})</td>'
            f'<td class="amount">{money(t["amount"], locale)}</td></tr>'
        )
    rows.append(f'<tr class="total"><td>Total</td><td class="amount">{money(order.total_cents, locale)}</td></tr>')
    return f'<table class="summary">{"".join(rows)}</table>'


def cohort_schedule_html(cohorts: list[Cohort], locale: str, tz: str) -> str:
    parts = []
    for c in cohorts:
        title = html.escape(pick(c.bootcamp, "title", locale))
        dates = (
            f"{fmt_date(c.start_date, locale)} – {fmt_date(c.end_date, locale)}"
        )
        first = next((s for s in c.sessions if s.status != "cancelled"), None)
        first_txt = fmt_datetime(first.starts_at, locale, tz) if first else ""
        label_first = "Première séance" if locale == "fr" else "First session"
        parts.append(
            f"<p><strong>{title}</strong> ({html.escape(c.code)})<br>{dates}<br>"
            f"{html.escape(calendar.schedule_text(c, locale))}"
            + (f"<br>{label_first} : {first_txt}" if first_txt else "")
            + "</p>"
        )
    return "".join(parts)


def user_tz(user: User | None) -> str:
    return (user.time_zone if user and user.time_zone else "America/Toronto")
