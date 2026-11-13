"""Newsletter subscriptions with express consent and one-click unsubscribe (EF-95, CASL).

Marketing emails themselves are sent by an external newsletter tool (Brevo/Mailchimp); this module
keeps the consent list that is synchronized with it (admin CSV export).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.security import new_token
from app.models import NewsletterSubscriber, User


def subscribe(db: Session, email: str, locale: str, user_id: uuid.UUID | None = None) -> NewsletterSubscriber:
    email = email.strip().lower()
    sub = db.scalar(select(NewsletterSubscriber).where(NewsletterSubscriber.email == email))
    if sub:
        sub.unsubscribed_at = None
        sub.locale = locale
        if user_id:
            sub.user_id = user_id
    else:
        sub = NewsletterSubscriber(email=email, locale=locale, user_id=user_id, unsubscribe_token=new_token(24))
        db.add(sub)
    db.flush()
    return sub


def unsubscribe(db: Session, token: str) -> bool:
    sub = db.scalar(select(NewsletterSubscriber).where(NewsletterSubscriber.unsubscribe_token == token))
    if not sub:
        return False
    sub.unsubscribed_at = utcnow()
    if sub.user_id:
        user = db.get(User, sub.user_id)
        if user:
            user.marketing_opt_in = False
    db.flush()
    return True


def unsubscribe_email(db: Session, email: str) -> None:
    sub = db.scalar(select(NewsletterSubscriber).where(NewsletterSubscriber.email == email.strip().lower()))
    if sub and not sub.unsubscribed_at:
        sub.unsubscribed_at = utcnow()
