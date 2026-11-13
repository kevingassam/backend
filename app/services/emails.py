"""Render and deliver transactional emails (HTML + plain text), with logging (section 7)."""

import html
import re
import smtplib
import uuid
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, settings
from app.core.db import utcnow
from app.core.i18n import norm_locale
from app.models import EmailLog, EmailTemplate
from app.services import site_settings
from app.services.email_templates import TEMPLATES

_env = Environment(
    loader=FileSystemLoader(BASE_DIR / "app" / "templates"),
    autoescape=select_autoescape(["html"]),
)


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _fill(text: str, context: dict[str, Any], escape: bool) -> str:
    values = {}
    for k, v in context.items():
        s = "" if v is None else str(v)
        values[k] = s if (k.endswith("_html") or not escape) else html.escape(s)
    return text.format_map(_SafeDict(values))


def html_to_text(body: str) -> str:
    text = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', r"\2 (\1)", body, flags=re.S)
    text = re.sub(r"<(br|/p|/h\d|/li|/tr)[^>]*>", "\n", text)
    text = re.sub(r"<li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def get_template(db: Session, key: str, locale: str) -> tuple[str, str]:
    override = db.scalar(select(EmailTemplate).where(EmailTemplate.key == key, EmailTemplate.locale == locale))
    if override:
        return override.subject, override.body_html
    return TEMPLATES[key][locale]


def render(db: Session, key: str, locale: str, context: dict[str, Any]) -> tuple[str, str, str]:
    locale = norm_locale(locale)
    subject_t, body_t = get_template(db, key, locale)
    subject = _fill(subject_t, context, escape=False)
    body = _fill(body_t, context, escape=True)
    cfg = site_settings.get_all(db)
    full_html = _env.get_template("emails/layout.html").render(
        body=body, subject=subject, locale=locale, company=cfg, frontend_url=settings.frontend_url
    )
    footer = f"\n\n--\n{cfg['company_name']}\n{cfg['company_address']}\n{cfg['contact_email']}"
    return subject, full_html, html_to_text(body) + footer


def deliver(
    db: Session,
    *,
    key: str,
    to: str,
    locale: str,
    context: dict[str, Any],
    user_id: uuid.UUID | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
    dedupe_key: str | None = None,
) -> EmailLog | None:
    """Render, send and log. Returns None if already sent (dedupe)."""
    if dedupe_key and db.scalar(select(EmailLog).where(EmailLog.dedupe_key == dedupe_key, EmailLog.status == "sent")):
        return None
    subject, body_html, body_text = render(db, key, locale, context)
    log = db.scalar(select(EmailLog).where(EmailLog.dedupe_key == dedupe_key)) if dedupe_key else None
    if not log:
        log = EmailLog(user_id=user_id, to_email=to, template=key, locale=locale, subject=subject, dedupe_key=dedupe_key)
        db.add(log)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = to
    msg_id = make_msgid(domain="bootcamps.local")
    msg["Message-ID"] = msg_id
    msg.set_content(body_text)
    msg.add_alternative(body_html, subtype="html")
    for filename, mime, data in attachments or []:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    try:
        _send(msg)
        log.status = "sent"
        log.provider_id = msg_id
        log.sent_at = utcnow()
        log.error = None
    except Exception as exc:  # noqa: BLE001 - logged and retried by the worker
        log.status = "failed"
        log.error = str(exc)[:2000]
        db.flush()
        raise
    db.flush()
    return log


def _send(msg: EmailMessage) -> None:
    backend = settings.email_backend
    if backend == "smtp":
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_tls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    elif backend == "file":
        settings.mail_dir.mkdir(parents=True, exist_ok=True)
        name = f"{utcnow():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.eml"
        (settings.mail_dir / name).write_bytes(bytes(msg))
    elif backend == "console":
        print(f"[email] to={msg['To']} subject={msg['Subject']}")
    # backend == "none": drop (tests)
