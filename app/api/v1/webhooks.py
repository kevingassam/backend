"""Stripe webhooks (EF-45): signature verified, idempotent, enrollment confirmed only here."""

import logging

from fastapi import APIRouter, Request

from app.api.deps import DB
from app.core.db import SessionLocal
from app.services import notify, orders, stripe_service

router = APIRouter(tags=["webhooks"])
log = logging.getLogger("app.webhooks")


@router.post("/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(request: Request, db: DB) -> dict:
    payload = await request.body()
    event = stripe_service.construct_event(payload, request.headers.get("stripe-signature"))
    try:
        result = orders.handle_stripe_event(db, event)
        db.commit()
    except Exception as exc:
        db.rollback()
        log.exception("stripe webhook failed: %s %s", event["type"], event["id"])
        with SessionLocal() as alert_db:
            notify.admin_alert(
                alert_db, f"Échec du webhook Stripe {event['type']}",
                f"<p>Événement {event['id']} : {type(exc).__name__}</p><p>Stripe va réessayer automatiquement.</p>", "orders",
                dedupe_key=f"webhook_fail:{event['id']}",
            )
            alert_db.commit()
        raise  # 500 → Stripe retries
    return {"received": True, "result": result}
