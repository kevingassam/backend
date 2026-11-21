"""FastAPI application: REST API versioned under /api/v1 with auto-generated OpenAPI docs."""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1 import auth, cart, catalog, instructor, me, public, webhooks
from app.api.v1.admin import catalog as admin_catalog
from app.api.v1.admin import commerce as admin_commerce
from app.api.v1.admin import content as admin_content
from app.api.v1.admin import people as admin_people
from app.core.config import settings
from app.core.errors import install_error_handlers

logging.basicConfig(level=logging.INFO, format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')

app = FastAPI(
    title="BECOME DEVOPS API",
    version="1.0.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,  # strict CORS (section 12)
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Accept-Language", "Authorization"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if not request.url.path.startswith("/api/v1/docs"):
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    if settings.cookie_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    if request.url.path.startswith("/api/v1/me") or request.url.path.startswith("/api/v1/admin"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


install_error_handlers(app)

API = "/api/v1"
for r in (
    public.router, auth.router, catalog.router, cart.router, webhooks.router, me.router, instructor.router,
    admin_catalog.router, admin_people.router, admin_commerce.router, admin_content.router,
):
    app.include_router(r, prefix=API)

settings.public_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.public_dir), name="media")
