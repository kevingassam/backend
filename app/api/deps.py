"""Request dependencies: current user, role checks (enforced by the API, section 3), CSRF, rate limits, locale."""

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.i18n import request_locale
from app.core.security import ACCESS_COOKIE, client_ip, csrf_valid, decode_access_token, limiter
from app.models import Cohort, User

DB = Annotated[Session, Depends(get_db)]

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def locale(request: Request) -> str:
    return request_locale(request)


Locale = Annotated[str, Depends(locale)]


def _claims(request: Request) -> dict | None:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:]
    return decode_access_token(token) if token else None


def optional_user(request: Request, db: DB) -> User | None:
    claims = _claims(request)
    if not claims:
        return None
    user = db.get(User, uuid.UUID(claims["sub"]))
    if not user or user.deleted_at or user.token_version != claims.get("tv"):
        return None
    request.state.mfa = bool(claims.get("mfa"))
    # CSRF: cookie-authenticated unsafe requests must echo the CSRF cookie in a header
    if request.method in UNSAFE and request.cookies.get(ACCESS_COOKIE) and not csrf_valid(request):
        raise AppError("csrf_failed", 403)
    return user


def current_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if not user:
        raise AppError("unauthorized", 401)
    return user


OptionalUser = Annotated[User | None, Depends(optional_user)]
CurrentUser = Annotated[User, Depends(current_user)]


def _require_mfa(request: Request, user: User) -> None:
    if settings.require_admin_mfa and not getattr(request.state, "mfa", False):
        raise AppError("mfa_required", 403)


def admin_user(request: Request, user: CurrentUser) -> User:
    if user.role != "admin":
        raise AppError("forbidden", 403)
    _require_mfa(request, user)
    return user


def staff_user(request: Request, user: CurrentUser) -> User:
    if user.role not in ("admin", "instructor"):
        raise AppError("forbidden", 403)
    _require_mfa(request, user)
    return user


AdminUser = Annotated[User, Depends(admin_user)]
StaffUser = Annotated[User, Depends(staff_user)]


def own_cohort(db: Session, user: User, cohort_id: uuid.UUID) -> Cohort:
    """Instructors only see their own cohorts; admins see all."""
    cohort = db.get(Cohort, cohort_id)
    if not cohort:
        raise AppError("not_found", 404)
    if user.role != "admin" and cohort.instructor_id != user.id:
        raise AppError("forbidden", 403)
    return cohort


def rate_limit(name: str, limit: int, window: int) -> Callable:
    def dep(request: Request) -> None:
        if not limiter.hit(f"{name}:{client_ip(request) or 'unknown'}", limit, window):
            raise AppError("rate_limited", 429)

    return Depends(dep)


def ip(request: Request) -> str:
    return client_ip(request)
