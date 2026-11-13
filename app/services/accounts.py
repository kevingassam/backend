"""Accounts and authentication (EF-20..EF-25, EF-70, EF-89)."""

import uuid
from datetime import timedelta

import pyotp
from fastapi import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import urls
from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import AppError
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_token,
    set_auth_cookies,
    verify_password,
)
from app.models import (
    Address,
    AuthToken,
    Cart,
    Consent,
    Enrollment,
    NewsletterSubscriber,
    Order,
    PrivacyRequest,
    RefreshToken,
    User,
    WaitlistEntry,
)
from app.services import jobs, newsletter

MIN_PASSWORD = 10


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AppError("weak_password", 422)


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.strip().lower(), User.deleted_at.is_(None)))


def create_user(db: Session, *, email: str, password: str | None, first_name: str, last_name: str, locale: str, time_zone: str | None = None, role: str = "student") -> User:
    email = email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise AppError("email_taken", 409)
    if password is not None:
        check_password_strength(password)
    user = User(
        email=email,
        password_hash=hash_password(password) if password else None,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        locale=locale,
        time_zone=time_zone or "America/Toronto",
        role=role,
        email_prefs={"reminders": True, "materials": True},
    )
    db.add(user)
    db.flush()
    return user


def issue_token(db: Session, user: User | None, purpose: str, minutes: int, payload: dict | None = None) -> str:
    token = new_token()
    db.add(AuthToken(user_id=user.id if user else None, purpose=purpose, token_hash=hash_token(token), expires_at=utcnow() + timedelta(minutes=minutes), payload=payload or {}))
    db.flush()
    return token


def consume_token(db: Session, token: str, purpose: str) -> AuthToken:
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(token), AuthToken.purpose == purpose).with_for_update())
    if not row or row.used_at or row.expires_at < utcnow():
        raise AppError("invalid_token", 400)
    row.used_at = utcnow()
    return row


def send_verification(db: Session, user: User, welcome: bool) -> None:
    token = issue_token(db, user, "verify_email", settings.verify_token_hours * 60)
    jobs.send_email(
        db, "welcome_verify" if welcome else "verify_email", user.email, user.locale,
        {"first_name": user.first_name, "verify_url": urls.front("verify_email", user.locale, query=f"token={token}")},
        user_id=user.id,
    )


def verify_email(db: Session, token: str) -> User:
    row = consume_token(db, token, "verify_email")
    user = db.get(User, row.user_id)
    if not user:
        raise AppError("invalid_token", 400)
    user.email_verified_at = user.email_verified_at or utcnow()
    return user


# --- Sessions ------------------------------------------------------------------------


def authenticate(db: Session, email: str, password: str) -> User:
    user = get_by_email(db, email)
    now = utcnow()
    if user and user.locked_until and user.locked_until > now:
        minutes = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        raise AppError("account_locked", 423, minutes=minutes)
    if not user or not verify_password(password, user.password_hash):
        if user:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.max_failed_logins:  # EF-25
                user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
                user.failed_login_count = 0
            db.commit()
        raise AppError("invalid_credentials", 401)
    user.failed_login_count = 0
    user.locked_until = None
    return user


def mfa_required(user: User) -> bool:
    return user.is_staff and settings.require_admin_mfa


def start_session(db: Session, response: Response, user: User, mfa: bool) -> None:
    refresh = new_token()
    db.add(RefreshToken(user_id=user.id, token_hash=hash_token(refresh), expires_at=utcnow() + timedelta(days=settings.refresh_token_days), mfa=mfa))
    user.last_login_at = utcnow()
    set_auth_cookies(response, create_access_token(user.id, user.role, user.token_version, mfa), refresh)


def rotate_refresh(db: Session, response: Response, refresh_token: str) -> User:
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token)).with_for_update())
    if not row or row.revoked_at or row.expires_at < utcnow():
        if row and row.revoked_at:
            # reuse of a rotated token: revoke the whole family for safety
            db.execute(update(RefreshToken).where(RefreshToken.user_id == row.user_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))
            db.commit()
        raise AppError("unauthorized", 401)
    user = db.get(User, row.user_id)
    if not user or user.deleted_at:
        raise AppError("unauthorized", 401)
    row.revoked_at = utcnow()
    start_session(db, response, user, row.mfa)
    return user


def revoke_refresh(db: Session, refresh_token: str | None) -> None:
    if refresh_token:
        db.execute(update(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token)).values(revoked_at=utcnow()))


def revoke_all(db: Session, user: User) -> None:
    user.token_version += 1
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))


# --- Passwords and email ---------------------------------------------------------------


def forgot_password(db: Session, email: str) -> None:
    user = get_by_email(db, email)
    if not user:
        return  # do not reveal whether the account exists
    token = issue_token(db, user, "reset_password", settings.reset_token_minutes)
    jobs.send_email(
        db, "password_reset", user.email, user.locale,
        {"first_name": user.first_name, "reset_url": urls.front("reset_password", user.locale, query=f"token={token}")},
        user_id=user.id,
    )


def reset_password(db: Session, token: str, password: str) -> User:
    check_password_strength(password)
    row = consume_token(db, token, "reset_password")
    user = db.get(User, row.user_id)
    user.password_hash = hash_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    user.email_verified_at = user.email_verified_at or utcnow()  # they proved access to the mailbox
    revoke_all(db, user)
    return user


def change_password(db: Session, user: User, current: str | None, new: str) -> None:
    if user.password_hash and not verify_password(current or "", user.password_hash):
        raise AppError("wrong_password", 400)
    check_password_strength(new)
    user.password_hash = hash_password(new)
    revoke_all(db, user)


def request_email_change(db: Session, user: User, new_email: str, password: str | None) -> None:
    if user.password_hash and not verify_password(password or "", user.password_hash):
        raise AppError("wrong_password", 400)
    new_email = new_email.strip().lower()
    if db.scalar(select(User.id).where(User.email == new_email)):
        raise AppError("email_taken", 409)
    token = issue_token(db, user, "change_email", 24 * 60, {"email": new_email})
    jobs.send_email(
        db, "email_change", new_email, user.locale,
        {"first_name": user.first_name, "new_email": new_email, "confirm_url": urls.front("confirm_email", user.locale, query=f"token={token}")},
        user_id=user.id,
    )


def confirm_email_change(db: Session, token: str) -> User:
    row = consume_token(db, token, "change_email")
    user = db.get(User, row.user_id)
    new_email = row.payload["email"]
    if db.scalar(select(User.id).where(User.email == new_email, User.id != user.id)):
        raise AppError("email_taken", 409)
    newsletter.unsubscribe_email(db, user.email)
    user.email = new_email
    user.email_verified_at = utcnow()
    if user.marketing_opt_in:
        newsletter.subscribe(db, new_email, user.locale, user.id)
    return user


# --- Google OAuth (EF-20) ------------------------------------------------------------------


def login_or_create_google(db: Session, *, sub: str, email: str, email_verified: bool, first_name: str, last_name: str, locale: str) -> User:
    user = db.scalar(select(User).where(User.google_sub == sub, User.deleted_at.is_(None)))
    if user:
        return user
    user = get_by_email(db, email)
    if user:
        if not email_verified:
            raise AppError("google_failed", 400)
        user.google_sub = sub
        user.email_verified_at = user.email_verified_at or utcnow()
        return user
    user = create_user(db, email=email, password=None, first_name=first_name, last_name=last_name, locale=locale)
    user.google_sub = sub
    if email_verified:
        user.email_verified_at = utcnow()
    return user


# --- TOTP (EF-70) -----------------------------------------------------------------------


def totp_setup(db: Session, user: User) -> dict:
    if user.totp_enabled:
        raise AppError("invalid_state", 409)
    user.totp_secret = pyotp.random_base32()
    db.flush()
    from app.services import site_settings

    issuer = site_settings.get(db, "company_name")
    return {"secret": user.totp_secret, "otpauth_url": pyotp.TOTP(user.totp_secret).provisioning_uri(name=user.email, issuer_name=issuer)}


def totp_verify(user: User, code: str) -> bool:
    return bool(user.totp_secret) and pyotp.TOTP(user.totp_secret).verify(code.replace(" ", ""), valid_window=1)


# --- Privacy (EF-24, EF-89) -------------------------------------------------------------------


def export_data(db: Session, user: User) -> dict:
    addr = user.address
    enrollments = db.scalars(select(Enrollment).where(Enrollment.user_id == user.id))
    orders = db.scalars(select(Order).where(Order.user_id == user.id))
    consents = db.scalars(select(Consent).where(Consent.user_id == user.id))
    return {
        "exported_at": utcnow().isoformat(),
        "profile": {
            "email": user.email, "first_name": user.first_name, "last_name": user.last_name, "phone": user.phone,
            "locale": user.locale, "time_zone": user.time_zone, "marketing_opt_in": user.marketing_opt_in,
            "email_verified_at": user.email_verified_at.isoformat() if user.email_verified_at else None,
            "created_at": user.created_at.isoformat(), "google_linked": bool(user.google_sub),
        },
        "billing_address": {
            "line1": addr.line1, "line2": addr.line2, "city": addr.city, "province": addr.province,
            "postal_code": addr.postal_code, "country": addr.country,
        } if addr else None,
        "enrollments": [
            {"cohort": e.cohort.code, "status": e.status, "created_at": e.created_at.isoformat(), "completed_at": e.completed_at.isoformat() if e.completed_at else None}
            for e in enrollments
        ],
        "orders": [
            {"number": o.number, "status": o.status, "total_cents": o.total_cents, "currency": o.currency, "created_at": o.created_at.isoformat()}
            for o in orders
        ],
        "consents": [
            {"type": c.type, "version": c.version, "granted": c.granted, "date": c.created_at.isoformat(), "context": c.context}
            for c in consents
        ],
    }


def request_deletion(db: Session, user: User, details: str) -> PrivacyRequest:
    if db.scalar(select(PrivacyRequest).where(PrivacyRequest.user_id == user.id, PrivacyRequest.type == "deletion", PrivacyRequest.status == "pending")):
        raise AppError("request_exists", 409)
    req = PrivacyRequest(user_id=user.id, email=user.email, type="deletion", details=details)
    db.add(req)
    db.flush()
    from app.services import notify

    notify.admin_alert(db, f"Demande de suppression de compte — {user.email}", f"<p>{details}</p>", "privacy")
    return req


def anonymize(db: Session, user: User) -> None:
    """Erase or anonymize personal data; invoices (orders) are kept as required by law."""
    first_name, email, locale = user.first_name, user.email, user.locale
    jobs.send_email(db, "account_deleted", email, locale, {"first_name": first_name})
    newsletter.unsubscribe_email(db, email)
    db.execute(update(NewsletterSubscriber).where(NewsletterSubscriber.email == email).values(email=f"deleted-{uuid.uuid4().hex}@invalid"))
    db.execute(update(WaitlistEntry).where(WaitlistEntry.email == email).values(email=f"deleted-{uuid.uuid4().hex}@invalid", first_name="", status="removed"))
    if user.address:
        db.delete(user.address)
    cart = db.scalar(select(Cart).where(Cart.user_id == user.id))
    if cart:
        db.delete(cart)
    user.email = f"deleted-{user.id.hex}@invalid"
    user.first_name = "Utilisateur"
    user.last_name = "supprimé"
    user.phone = None
    user.password_hash = None
    user.google_sub = None
    user.totp_secret = None
    user.totp_enabled = False
    user.marketing_opt_in = False
    user.bio_fr = user.bio_en = user.photo_url = None
    user.deleted_at = utcnow()
    revoke_all(db, user)


def address_dict(addr: Address | None) -> dict | None:
    if not addr:
        return None
    return {"line1": addr.line1, "line2": addr.line2, "city": addr.city, "province": addr.province, "postal_code": addr.postal_code, "country": addr.country}
