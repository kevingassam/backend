"""Cart: several bootcamps, one cohort per bootcamp (EF-30..EF-36)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError
from app.core.security import new_token
from app.models import Bootcamp, Cart, CartItem, ClassSession, Cohort, Enrollment, User
from app.services import seats

ENROLLED = ("confirmed", "completed")


def _load(db: Session, where) -> Cart | None:
    return db.scalar(
        select(Cart)
        .where(where)
        .options(
            selectinload(Cart.items).selectinload(CartItem.cohort).selectinload(Cohort.bootcamp),
            selectinload(Cart.promo_code),
        )
    )


def find(db: Session, user: User | None, token: str | None) -> Cart | None:
    if user:
        return _load(db, Cart.user_id == user.id)
    if token:
        return _load(db, Cart.token == token)
    return None


def get_or_create(db: Session, user: User | None, token: str | None) -> tuple[Cart, str | None]:
    """Returns (cart, new_anonymous_token_if_created)."""
    cart = find(db, user, token)
    if cart:
        return cart, None
    if user:
        cart = Cart(user_id=user.id)
        db.add(cart)
        db.flush()
        return cart, None
    tok = new_token(24)
    cart = Cart(token=tok)
    db.add(cart)
    db.flush()
    return cart, tok


def is_enrolled(db: Session, user_id: uuid.UUID, cohort_id: uuid.UUID) -> bool:
    return db.scalar(
        select(Enrollment.id).where(
            Enrollment.user_id == user_id, Enrollment.cohort_id == cohort_id, Enrollment.status.in_(ENROLLED)
        )
    ) is not None


def check_addable(db: Session, cohort: Cohort, user: User | None) -> None:
    if cohort.bootcamp.status != "published" or not seats.registration_open(cohort):
        raise AppError("cohort_unavailable", 409)
    if user and is_enrolled(db, user.id, cohort.id):
        raise AppError("already_enrolled", 409)  # EF-33
    if seats.remaining(db, cohort, exclude_user_id=user.id if user else None) <= 0:
        raise AppError("cohort_full", 409, details={"waitlist": True})  # EF-12


def add_item(db: Session, cart: Cart, cohort_id: uuid.UUID, user: User | None) -> dict:
    cohort = db.scalar(select(Cohort).where(Cohort.id == cohort_id).options(selectinload(Cohort.bootcamp)))
    if not cohort:
        raise AppError("not_found", 404)
    check_addable(db, cohort, user)
    existing = next((i for i in cart.items if i.bootcamp_id == cohort.bootcamp_id), None)
    replaced = None
    if existing:
        if existing.cohort_id == cohort.id:
            return {"replaced": None}
        replaced = existing.cohort.code
        existing.cohort_id = cohort.id
        existing.cohort = cohort
    else:
        cart.items.append(CartItem(bootcamp_id=cohort.bootcamp_id, cohort_id=cohort.id, cohort=cohort))
    db.flush()
    return {"replaced": replaced}  # EF-30: notify the student the previous cohort was replaced


def remove_item(db: Session, cart: Cart, item_id: uuid.UUID) -> None:
    item = next((i for i in cart.items if i.id == item_id), None)
    if not item:
        raise AppError("not_found", 404)
    cart.items.remove(item)
    db.delete(item)
    db.flush()


def merge_on_login(db: Session, user: User, anon_token: str | None) -> None:
    """EF-32: merge the visitor cart into the student cart when they sign in."""
    if not anon_token:
        return
    anon = _load(db, Cart.token == anon_token)
    if not anon:
        return
    target, _ = get_or_create(db, user, None)
    for item in list(anon.items):
        if is_enrolled(db, user.id, item.cohort_id):
            continue
        existing = next((i for i in target.items if i.bootcamp_id == item.bootcamp_id), None)
        if existing:
            existing.cohort_id = item.cohort_id  # most recent choice wins
        else:
            target.items.append(CartItem(bootcamp_id=item.bootcamp_id, cohort_id=item.cohort_id))
    if anon.promo_code_id and not target.promo_code_id:
        target.promo_code_id = anon.promo_code_id
    db.delete(anon)
    db.flush()


def clear_cohorts(db: Session, user_id: uuid.UUID, cohort_ids: set[uuid.UUID]) -> None:
    cart = _load(db, Cart.user_id == user_id)
    if not cart:
        return
    for item in list(cart.items):
        if item.cohort_id in cohort_ids:
            cart.items.remove(item)
            db.delete(item)
    if not cart.items:
        cart.promo_code_id = None
    db.flush()


def item_issue(db: Session, cohort: Cohort, user: User | None) -> str | None:
    """Why an item can no longer be bought: closed | full | enrolled | None."""
    if cohort.bootcamp.status != "published" or not seats.registration_open(cohort):
        return "closed"
    if user and is_enrolled(db, user.id, cohort.id):
        return "enrolled"
    if seats.remaining(db, cohort, exclude_user_id=user.id if user else None) <= 0:
        return "full"
    return None


def overlaps(db: Session, cohorts: list[Cohort]) -> list[dict]:
    """EF-34: non-blocking warning when two cohorts have overlapping sessions."""
    if len(cohorts) < 2:
        return []
    ids = [c.id for c in cohorts]
    sessions = list(
        db.scalars(select(ClassSession).where(ClassSession.cohort_id.in_(ids), ClassSession.status != "cancelled"))
    )
    by_cohort: dict[uuid.UUID, list[ClassSession]] = {}
    for s in sessions:
        by_cohort.setdefault(s.cohort_id, []).append(s)
    out = []
    for i, a in enumerate(cohorts):
        for b in cohorts[i + 1 :]:
            hit = next(
                (
                    (sa, sb)
                    for sa in by_cohort.get(a.id, [])
                    for sb in by_cohort.get(b.id, [])
                    if sa.starts_at < sb.ends_at and sb.starts_at < sa.ends_at
                ),
                None,
            )
            if hit:
                out.append({"cohort_a": str(a.id), "cohort_b": str(b.id), "first_conflict": hit[0].starts_at.isoformat()})
    return out


def bootcamp_of(db: Session, bootcamp_id: uuid.UUID) -> Bootcamp | None:
    return db.get(Bootcamp, bootcamp_id)
