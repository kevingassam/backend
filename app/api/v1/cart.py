"""Cart and checkout (EF-30..EF-47)."""

import uuid

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentUser, Locale, OptionalUser, rate_limit
from app.core.config import settings
from app.core.errors import AppError
from app.core.i18n import norm_locale, pick
from app.core.security import CART_COOKIE, client_ip
from app.models import Cart, Order, User
from app.schemas.common import AddressIn, Loc, validate_canadian_province
from app.services import calendar, orders, pricing, site_settings, taxes
from app.services import cart as cart_svc
from app.services.legal import consent_versions

router = APIRouter(tags=["cart"])


def _set_cart_cookie(response: Response, token: str | None) -> None:
    if token:
        response.set_cookie(CART_COOKIE, token, httponly=True, max_age=60 * 86400, samesite="lax", secure=settings.cookie_secure, path="/")


def cart_view(db, cart: Cart | None, user: User | None, loc: str, province: str | None = None) -> dict:
    cfg = site_settings.get_all(db)
    if not cart or not cart.items:
        return {
            "items": [], "count": 0, "subtotal": 0, "multi_discount": 0, "promo_discount": 0, "discount": 0,
            "tax_lines": [], "tax": 0, "total": 0, "promo_code": None, "promo_error": None, "tax_estimated": True,
            "province": province, "warnings": [], "can_checkout": False,
            "multi_discount_rule": {"enabled": cfg["multi_discount_enabled"], "percent": cfg["multi_discount_percent"], "min_items": cfg["multi_discount_min_items"]},
        }
    if not province and user and user.address:
        province = user.address.province
    country = user.address.country if (user and user.address and not province) else "CA"
    cohorts = [i.cohort for i in cart.items]
    quote = pricing.build_quote(db, cohorts, user_id=user.id if user else None, promo=cart.promo_code, province=province, country=country)
    items = []
    can_checkout = True
    for item, line in zip(cart.items, quote.lines, strict=True):
        c = item.cohort
        issue = cart_svc.item_issue(db, c, user)
        if issue:
            can_checkout = False
        items.append({
            "id": str(item.id),
            "bootcamp": {"id": str(c.bootcamp_id), "title": pick(c.bootcamp, "title", loc), "slug": getattr(c.bootcamp, f"slug_{loc}"), "image_url": c.bootcamp.image_url},
            "cohort": {"id": str(c.id), "code": c.code, "start_date": c.start_date.isoformat(), "end_date": c.end_date.isoformat(), "schedule_text": calendar.schedule_text(c, loc)},
            "unit_price": line.unit_price, "regular_price": line.regular_price, "early_bird": line.early_bird,
            "multi_discount": line.multi_discount, "promo_discount": line.promo_discount, "net": line.net,
            "tax": line.tax, "total": line.total, "issue": issue,
        })
    return {
        "items": items,
        "count": len(items),
        "subtotal": quote.subtotal,
        "multi_discount": quote.multi_discount,
        "multi_discount_percent": quote.multi_discount_percent,
        "promo_discount": quote.promo_discount,
        "discount": quote.discount,
        "tax_lines": [{**t, "label": taxes.label(t["type"], loc)} for t in quote.tax_lines],
        "tax": quote.tax,
        "total": quote.total,
        "promo_code": cart.promo_code.code if cart.promo_code and not quote.promo_error else None,
        "promo_error": quote.promo_error,
        "tax_estimated": quote.tax_estimated,
        "province": province,
        "warnings": [{"type": "overlap", **o} for o in cart_svc.overlaps(db, cohorts)],
        "can_checkout": can_checkout,
        "multi_discount_rule": {"enabled": cfg["multi_discount_enabled"], "percent": cfg["multi_discount_percent"], "min_items": cfg["multi_discount_min_items"]},
    }


@router.get("/cart")
def get_cart(request: Request, db: DB, user: OptionalUser, loc: Locale, province: str | None = None) -> dict:
    cart = cart_svc.find(db, user, request.cookies.get(CART_COOKIE))
    return cart_view(db, cart, user, loc, province.upper() if province else None)


class AddItemIn(BaseModel):
    cohort_id: uuid.UUID


@router.post("/cart/items", dependencies=[rate_limit("cart", 60, 60)])
def add_item(body: AddItemIn, request: Request, response: Response, db: DB, user: OptionalUser, loc: Locale) -> dict:
    cart, token = cart_svc.get_or_create(db, user, request.cookies.get(CART_COOKIE))
    res = cart_svc.add_item(db, cart, body.cohort_id, user)
    db.commit()
    _set_cart_cookie(response, token)
    db.refresh(cart, attribute_names=["items"])
    return {"replaced": res["replaced"], "cart": cart_view(db, cart_svc.find(db, user, token or request.cookies.get(CART_COOKIE)), user, loc)}


@router.delete("/cart/items/{item_id}")
def remove_item(item_id: uuid.UUID, request: Request, db: DB, user: OptionalUser, loc: Locale) -> dict:
    cart = cart_svc.find(db, user, request.cookies.get(CART_COOKIE))
    if not cart:
        raise AppError("not_found", 404)
    cart_svc.remove_item(db, cart, item_id)
    db.commit()
    return cart_view(db, cart_svc.find(db, user, request.cookies.get(CART_COOKIE)), user, loc)


class PromoIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)


@router.post("/cart/promo-code", dependencies=[rate_limit("promo", 20, 600)])
def apply_promo(body: PromoIn, request: Request, db: DB, user: OptionalUser, loc: Locale) -> dict:
    cart = cart_svc.find(db, user, request.cookies.get(CART_COOKIE))
    if not cart or not cart.items:
        raise AppError("cart_empty", 409)
    promo = pricing.find_promo(db, body.code)
    if not promo:
        raise AppError("promo_invalid", 422)
    pricing.validate_promo(db, promo, user.id if user else None, [i.bootcamp_id for i in cart.items])
    cart.promo_code = promo
    db.commit()
    return cart_view(db, cart_svc.find(db, user, request.cookies.get(CART_COOKIE)), user, loc)


@router.delete("/cart/promo-code")
def remove_promo(request: Request, db: DB, user: OptionalUser, loc: Locale) -> dict:
    cart = cart_svc.find(db, user, request.cookies.get(CART_COOKIE))
    if cart:
        cart.promo_code = None
        db.commit()
    return cart_view(db, cart_svc.find(db, user, request.cookies.get(CART_COOKIE)), user, loc)


class CheckoutIn(BaseModel):
    billing_address: AddressIn
    accept_terms: bool  # mandatory box (EF-43)
    marketing_opt_in: bool = False  # unchecked by default
    locale: Loc = "fr"


@router.post("/checkout", dependencies=[rate_limit("checkout", 20, 600)])
def checkout(body: CheckoutIn, request: Request, db: DB, user: CurrentUser) -> dict:
    validate_canadian_province(body.billing_address)
    return orders.start_checkout(
        db, user, address=body.billing_address.model_dump(), accept_terms=body.accept_terms,
        marketing_opt_in=body.marketing_opt_in, locale=norm_locale(body.locale), ip=client_ip(request),
        ua=request.headers.get("user-agent", "")[:500], consent_versions=consent_versions(db),
    )


@router.get("/checkout/orders/{order_id}")
def checkout_status(order_id: uuid.UUID, db: DB, user: CurrentUser, loc: Locale) -> dict:
    """Confirmation page polls this until the webhook has confirmed the payment (never trusts the redirect)."""
    order = db.get(Order, order_id)
    if not order or order.user_id != user.id:
        raise AppError("not_found", 404)
    from app.api.v1.me import order_out

    return order_out(db, order, loc)
