"""Thin wrapper around the Stripe SDK (Checkout, refunds, webhooks). Card data never touches our servers (EF-40)."""

import json
from typing import Any

import stripe

from app.core.config import settings
from app.core.errors import AppError


def _client() -> stripe.StripeClient:
    if not settings.stripe_secret_key:
        raise AppError("payments_not_configured", 503)
    return stripe.StripeClient(settings.stripe_secret_key, max_network_retries=2)


def create_checkout_session(params: dict[str, Any], idempotency_key: str) -> dict:
    try:
        session = _client().v1.checkout.sessions.create(params=params, options={"idempotency_key": idempotency_key})
    except stripe.StripeError as exc:
        raise AppError("stripe_error", 502, details={"stripe": str(exc.user_message or exc)}) from exc
    return {"id": session.id, "url": session.url}


def expire_checkout_session(session_id: str) -> None:
    try:
        _client().v1.checkout.sessions.expire(session_id)
    except (stripe.StripeError, AppError):
        pass  # already completed/expired, or Stripe not configured


def create_refund(payment_intent_id: str, amount_cents: int, idempotency_key: str, metadata: dict[str, str]) -> dict:
    try:
        refund = _client().v1.refunds.create(
            params={"payment_intent": payment_intent_id, "amount": amount_cents, "metadata": metadata},
            options={"idempotency_key": idempotency_key},
        )
    except stripe.StripeError as exc:
        raise AppError("stripe_error", 502, details={"stripe": str(exc.user_message or exc)}) from exc
    return {"id": refund.id, "status": refund.status}


def payment_method_label(payment_intent_id: str | None) -> str | None:
    """e.g. 'Visa •••• 4242' — best effort, for invoices and refund emails."""
    if not payment_intent_id:
        return None
    try:
        pi = _client().v1.payment_intents.retrieve(payment_intent_id, params={"expand": ["latest_charge"]})
        charge = pi.latest_charge
        details = getattr(charge, "payment_method_details", None)
        card = getattr(details, "card", None) if details else None
        if card:
            wallet = getattr(card, "wallet", None)
            prefix = f"{wallet.type.replace('_', ' ').title()} " if wallet else ""
            return f"{prefix}{card.brand.title()} •••• {card.last4}"
        return details.type if details else None
    except Exception:  # noqa: BLE001
        return None


def construct_event(payload: bytes, signature: str | None) -> dict:
    """Verify the Stripe signature, then return the event as plain JSON (dict)."""
    if not settings.stripe_webhook_secret:
        raise AppError("payments_not_configured", 503)
    try:
        stripe.Webhook.construct_event(payload, signature or "", settings.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise AppError("invalid_token", 400) from exc
    return json.loads(payload)
