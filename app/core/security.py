"""Password hashing (Argon2id), JWT access tokens, opaque tokens, cookies, CSRF, signed URLs, rate limiting."""

import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Request, Response

from app.core.config import settings
from app.core.db import utcnow

_hasher = PasswordHasher()  # Argon2id by default

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"
CART_COOKIE = "cart_token"
VISITOR_COOKIE = "visitor_id"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(user_id: uuid.UUID, role: str, token_version: int, mfa: bool) -> str:
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "role": role,
        "tv": token_version,
        "mfa": mfa,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_minutes)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    try:
        data = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return data if data.get("typ") == "access" else None


def create_purpose_token(purpose: str, data: dict, minutes: int) -> str:
    payload = {**data, "typ": purpose, "exp": int((utcnow() + timedelta(minutes=minutes)).timestamp())}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_purpose_token(token: str, purpose: str) -> dict | None:
    try:
        data = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return data if data.get("typ") == purpose else None


def _cookie_kwargs() -> dict:
    return {"secure": settings.cookie_secure, "samesite": "lax", "domain": settings.cookie_domain}


def set_auth_cookies(response: Response, access: str, refresh: str | None) -> None:
    response.set_cookie(
        ACCESS_COOKIE, access, httponly=True, max_age=settings.access_token_minutes * 60, path="/", **_cookie_kwargs()
    )
    if refresh:
        response.set_cookie(
            REFRESH_COOKIE,
            refresh,
            httponly=True,
            max_age=settings.refresh_token_days * 86400,
            path="/api/v1/auth",
            **_cookie_kwargs(),
        )
    ensure_csrf_cookie(response, force=True)


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/", domain=settings.cookie_domain)
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth", domain=settings.cookie_domain)


def ensure_csrf_cookie(response: Response, request: Request | None = None, force: bool = False) -> str:
    existing = request.cookies.get(CSRF_COOKIE) if request else None
    if existing and not force:
        return existing
    token = new_token(24)
    response.set_cookie(CSRF_COOKIE, token, httponly=False, max_age=30 * 86400, path="/", **_cookie_kwargs())
    return token


def csrf_valid(request: Request) -> bool:
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    return bool(cookie and header and hmac.compare_digest(cookie, header))


# --- Signed URLs for private files (EF-52) ---------------------------------


def sign_path(path: str, seconds: int | None = None) -> str:
    expires = int(time.time()) + (seconds or settings.signed_url_seconds)
    sig = hmac.new(settings.secret_key.encode(), f"{path}:{expires}".encode(), hashlib.sha256).hexdigest()
    return f"{path}?expires={expires}&sig={sig}"


def verify_signed(path: str, expires: int, sig: str) -> bool:
    if expires < time.time():
        return False
    expected = hmac.new(settings.secret_key.encode(), f"{path}:{expires}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


# --- Rate limiting -----------------------------------------------------------
# In-process sliding window. Good for a single API instance; swap for Redis when scaling out.


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self.enabled = True

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        if not self.enabled:
            return True
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str | None:
    """Client IP, validated. X-Forwarded-For is only trusted behind a known proxy (TRUST_PROXY=true)."""
    raw = None
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            raw = fwd.split(",")[0].strip()
    if raw is None:
        raw = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(raw)) if raw else None
    except ValueError:
        return None
