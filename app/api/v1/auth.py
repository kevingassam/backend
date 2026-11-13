"""Authentication: email/password, Google, verification, password reset, TOTP for staff (EF-20..EF-25, EF-70)."""

import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import DB, CurrentUser, Locale, rate_limit
from app.core import urls
from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import AppError
from app.core.i18n import norm_locale
from app.core.security import (
    CART_COOKIE,
    REFRESH_COOKIE,
    clear_auth_cookies,
    client_ip,
    create_purpose_token,
    decode_purpose_token,
    ensure_csrf_cookie,
)
from app.models import Consent, User
from app.schemas.common import EmailIn, Loc, Ok
from app.services import accounts, newsletter
from app.services import cart as cart_svc

router = APIRouter(prefix="/auth", tags=["auth"])


class AddressOut(BaseModel):
    line1: str
    line2: str | None
    city: str
    province: str
    postal_code: str
    country: str


class UserOut(BaseModel):
    id: str
    email: str
    first_name: str
    last_name: str
    phone: str | None
    locale: str
    time_zone: str
    role: str
    email_verified: bool
    has_password: bool
    google_linked: bool
    totp_enabled: bool
    marketing_opt_in: bool
    email_prefs: dict
    address: AddressOut | None


def user_out(user: User) -> UserOut:
    return UserOut(
        id=str(user.id), email=user.email, first_name=user.first_name, last_name=user.last_name, phone=user.phone,
        locale=user.locale, time_zone=user.time_zone, role=user.role, email_verified=user.email_verified_at is not None,
        has_password=user.password_hash is not None, google_linked=user.google_sub is not None,
        totp_enabled=user.totp_enabled, marketing_opt_in=user.marketing_opt_in,
        email_prefs={"reminders": True, "materials": True, **(user.email_prefs or {})},
        address=AddressOut(**accounts.address_dict(user.address)) if user.address else None,
    )


class SessionOut(BaseModel):
    user: UserOut | None = None
    mfa_required: bool = False
    mfa_setup_required: bool = False
    mfa_token: str | None = None


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    locale: Loc = "fr"
    time_zone: str | None = Field(default=None, max_length=64)
    marketing_opt_in: bool = False  # unchecked by default (EF-95)


def _finish_login(db, request: Request, response: Response, user: User) -> SessionOut:
    """Create the session, or ask for the second factor for staff accounts."""
    if accounts.mfa_required(user):
        if user.totp_enabled:
            token = create_purpose_token("mfa", {"sub": str(user.id), "tv": user.token_version}, minutes=5)
            db.commit()
            return SessionOut(mfa_required=True, mfa_token=token)
        accounts.start_session(db, response, user, mfa=False)
        db.commit()
        return SessionOut(user=user_out(user), mfa_setup_required=True)
    accounts.start_session(db, response, user, mfa=False)
    cart_svc.merge_on_login(db, user, request.cookies.get(CART_COOKIE))
    response.delete_cookie(CART_COOKIE, path="/")
    db.commit()
    return SessionOut(user=user_out(user))


@router.get("/csrf")
def csrf(request: Request, response: Response) -> dict:
    return {"csrf_token": ensure_csrf_cookie(response, request)}


@router.post("/signup", response_model=SessionOut, status_code=201, dependencies=[rate_limit("signup", 10, 3600)])
def signup(body: SignupIn, request: Request, response: Response, db: DB) -> SessionOut:
    user = accounts.create_user(
        db, email=body.email, password=body.password, first_name=body.first_name, last_name=body.last_name,
        locale=body.locale, time_zone=body.time_zone,
    )
    addr = client_ip(request)
    db.add(Consent(user_id=user.id, email=user.email, type="privacy", version="1", granted=True, ip=addr, context="signup"))
    db.add(Consent(user_id=user.id, email=user.email, type="marketing", version="1", granted=body.marketing_opt_in, ip=addr, context="signup"))
    if body.marketing_opt_in:
        user.marketing_opt_in = True
        newsletter.subscribe(db, user.email, user.locale, user.id)
    accounts.send_verification(db, user, welcome=True)
    return _finish_login(db, request, response, user)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


@router.post("/login", response_model=SessionOut, dependencies=[rate_limit("login", 20, 300)])
def login(body: LoginIn, request: Request, response: Response, db: DB) -> SessionOut:
    user = accounts.authenticate(db, body.email, body.password)
    return _finish_login(db, request, response, user)


class MfaIn(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=8)


@router.post("/mfa/verify", response_model=SessionOut, dependencies=[rate_limit("mfa", 10, 300)])
def mfa_verify(body: MfaIn, response: Response, db: DB) -> SessionOut:
    data = decode_purpose_token(body.mfa_token, "mfa")
    if not data:
        raise AppError("invalid_token", 400)
    import uuid

    user = db.get(User, uuid.UUID(data["sub"]))
    if not user or user.token_version != data.get("tv") or not accounts.totp_verify(user, body.code):
        raise AppError("mfa_invalid", 400)
    accounts.start_session(db, response, user, mfa=True)
    db.commit()
    return SessionOut(user=user_out(user))


@router.post("/totp/setup")
def totp_setup(user: CurrentUser, db: DB) -> dict:
    data = accounts.totp_setup(db, user)
    db.commit()
    return data


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


@router.post("/totp/enable", response_model=SessionOut)
def totp_enable(body: CodeIn, user: CurrentUser, response: Response, db: DB) -> SessionOut:
    if user.totp_enabled or not accounts.totp_verify(user, body.code):
        raise AppError("mfa_invalid", 400)
    user.totp_enabled = True
    accounts.start_session(db, response, user, mfa=True)
    db.commit()
    return SessionOut(user=user_out(user))


@router.post("/logout", response_model=Ok)
def logout(request: Request, response: Response, db: DB) -> Ok:
    accounts.revoke_refresh(db, request.cookies.get(REFRESH_COOKIE))
    db.commit()
    clear_auth_cookies(response)
    return Ok()


@router.post("/refresh", response_model=SessionOut)
def refresh(request: Request, response: Response, db: DB) -> SessionOut:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise AppError("unauthorized", 401)
    user = accounts.rotate_refresh(db, response, token)
    db.commit()
    return SessionOut(user=user_out(user))


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


@router.post("/verify-email", response_model=Ok)
def verify_email(body: TokenIn, db: DB) -> Ok:
    accounts.verify_email(db, body.token)
    db.commit()
    return Ok()


@router.post("/resend-verification", response_model=Ok, dependencies=[rate_limit("resend", 5, 3600)])
def resend_verification(user: CurrentUser, db: DB) -> Ok:
    if not user.email_verified_at:
        accounts.send_verification(db, user, welcome=False)
        db.commit()
    return Ok()


@router.post("/forgot-password", response_model=Ok, dependencies=[rate_limit("forgot", 5, 900)])
def forgot_password(body: EmailIn, db: DB) -> Ok:
    accounts.forgot_password(db, body.email)
    db.commit()
    return Ok()  # same answer whether or not the account exists


class ResetIn(BaseModel):
    token: str
    password: str = Field(max_length=200)


@router.post("/reset-password", response_model=Ok, dependencies=[rate_limit("reset", 10, 900)])
def reset_password(body: ResetIn, response: Response, db: DB) -> Ok:
    accounts.reset_password(db, body.token, body.password)
    db.commit()
    clear_auth_cookies(response)
    return Ok()


@router.post("/confirm-email-change", response_model=Ok)
def confirm_email_change(body: TokenIn, db: DB) -> Ok:
    accounts.confirm_email_change(db, body.token)
    db.commit()
    return Ok()


class InviteAcceptIn(BaseModel):
    token: str
    password: str = Field(max_length=200)


@router.post("/accept-invite", response_model=Ok)
def accept_invite(body: InviteAcceptIn, db: DB) -> Ok:
    row = accounts.consume_token(db, body.token, "invite")
    user = db.get(User, row.user_id)
    accounts.check_password_strength(body.password)
    from app.core.security import hash_password

    user.password_hash = hash_password(body.password)
    user.email_verified_at = user.email_verified_at or utcnow()
    db.commit()
    return Ok()


# --- Google OAuth 2.0 --------------------------------------------------------------------

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
STATE_COOKIE = "oauth_state"


def _google_redirect_uri() -> str:
    return f"{settings.api_base_url}/api/v1/auth/google/callback"


@router.get("/google/login")
def google_login(loc: Locale, locale: str | None = None, next: str | None = None) -> RedirectResponse:
    if not settings.google_client_id:
        raise AppError("google_not_configured", 503)
    state = secrets.token_urlsafe(24)
    lang = norm_locale(locale or loc)
    nxt = next if next and next.startswith("/") and not next.startswith("//") else f"/{lang}"
    q = urlencode({
        "client_id": settings.google_client_id, "redirect_uri": _google_redirect_uri(), "response_type": "code",
        "scope": "openid email profile", "state": state, "prompt": "select_account",
    })
    resp = RedirectResponse(f"{GOOGLE_AUTH}?{q}")
    payload = create_purpose_token("oauth_state", {"s": state, "l": lang, "n": nxt}, minutes=10)
    resp.set_cookie(STATE_COOKIE, payload, httponly=True, max_age=600, samesite="lax", secure=settings.cookie_secure)
    return resp


@router.get("/google/callback")
def google_callback(request: Request, db: DB, code: str | None = None, state: str | None = None) -> RedirectResponse:
    data = decode_purpose_token(request.cookies.get(STATE_COOKIE, ""), "oauth_state")
    if not data or not state or not secrets.compare_digest(data["s"], state) or not code:
        return RedirectResponse(f"{urls.front('login', 'fr')}?error=google")
    lang, nxt = data["l"], data["n"]
    try:
        with httpx.Client(timeout=10) as client:
            tok = client.post(GOOGLE_TOKEN, data={
                "code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
                "redirect_uri": _google_redirect_uri(), "grant_type": "authorization_code",
            })
            tok.raise_for_status()
            info = client.get(GOOGLE_USERINFO, headers={"Authorization": f"Bearer {tok.json()['access_token']}"})
            info.raise_for_status()
            profile = info.json()
        user = accounts.login_or_create_google(
            db, sub=profile["sub"], email=profile["email"], email_verified=bool(profile.get("email_verified")),
            first_name=profile.get("given_name", ""), last_name=profile.get("family_name", ""), locale=lang,
        )
    except (httpx.HTTPError, KeyError, AppError):
        db.rollback()
        return RedirectResponse(f"{urls.front('login', lang)}?error=google")
    response = RedirectResponse(f"{settings.frontend_url}{nxt}")
    response.delete_cookie(STATE_COOKIE)
    if accounts.mfa_required(user) and user.totp_enabled:
        # staff must still enter their TOTP code: send them to the login page's second step
        token = create_purpose_token("mfa", {"sub": str(user.id), "tv": user.token_version}, minutes=5)
        db.commit()
        return RedirectResponse(f"{urls.front('login', lang)}?mfa_token={token}")
    accounts.start_session(db, response, user, mfa=False)
    cart_svc.merge_on_login(db, user, request.cookies.get(CART_COOKIE))
    response.delete_cookie(CART_COOKIE, path="/")
    db.commit()
    return response
