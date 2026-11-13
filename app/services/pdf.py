"""PDF invoices (EF-46) and certificates of completion (EF-55), rendered from HTML templates."""

import io

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session
from xhtml2pdf import pisa

from app.core.config import BASE_DIR
from app.core.i18n import fmt_date, money, norm_locale, pick
from app.models import Enrollment, Order
from app.services import site_settings, taxes

_env = Environment(loader=FileSystemLoader(BASE_DIR / "app" / "templates"), autoescape=select_autoescape(["html"]))

LABELS = {
    "fr": {
        "invoice": "Facture", "number": "Numéro", "date": "Date", "bill_to": "Facturé à", "item": "Description",
        "price": "Prix", "discount": "Rabais", "amount": "Montant", "subtotal": "Sous-total",
        "multi": "Rabais multi-bootcamps", "promo": "Code promo", "total": "Total", "paid": "Payé",
        "refunded": "Remboursé", "tax_numbers": "Numéros de taxes", "cohort": "Cohorte", "dates": "du {start} au {end}",
        "status": "Statut", "currency": "Montants en dollars canadiens (CAD).", "method": "Mode de paiement",
        "status_paid": "Payée", "status_refunded": "Remboursée", "status_partially_refunded": "Partiellement remboursée",
        "certificate": "Attestation de réussite", "certifies": "Nous attestons que",
        "completed": "a terminé avec succès le bootcamp", "held": "offert du {start} au {end}",
        "cert_number": "N° d'attestation", "issued": "Délivrée le",
    },
    "en": {
        "invoice": "Invoice", "number": "Number", "date": "Date", "bill_to": "Billed to", "item": "Description",
        "price": "Price", "discount": "Discount", "amount": "Amount", "subtotal": "Subtotal",
        "multi": "Multi-bootcamp discount", "promo": "Promo code", "total": "Total", "paid": "Paid",
        "refunded": "Refunded", "tax_numbers": "Tax numbers", "cohort": "Cohort", "dates": "from {start} to {end}",
        "status": "Status", "currency": "Amounts in Canadian dollars (CAD).", "method": "Payment method",
        "status_paid": "Paid", "status_refunded": "Refunded", "status_partially_refunded": "Partially refunded",
        "certificate": "Certificate of completion", "certifies": "This certifies that",
        "completed": "has successfully completed the bootcamp", "held": "held from {start} to {end}",
        "cert_number": "Certificate no.", "issued": "Issued on",
    },
}


def _render_pdf(template: str, **ctx) -> bytes:
    source = _env.get_template(template).render(**ctx)
    out = io.BytesIO()
    result = pisa.CreatePDF(source, dest=out, encoding="utf-8")
    if result.err:
        raise RuntimeError("PDF generation failed")
    return out.getvalue()


def invoice_pdf(db: Session, order: Order, locale: str | None = None) -> bytes:
    loc = norm_locale(locale or order.locale)
    lb = LABELS[loc]
    cfg = site_settings.get_all(db)
    lines = []
    for it in order.items:
        c = it.cohort
        lines.append({
            "title": it.title_fr if loc == "fr" else (it.title_en or it.title_fr),
            "cohort": it.cohort_code,
            "dates": lb["dates"].format(start=fmt_date(c.start_date, loc), end=fmt_date(c.end_date, loc)),
            "price": money(it.unit_price_cents, loc),
            "discount": money(-it.discount_cents, loc) if it.discount_cents else "",
            "net": money(it.net_cents, loc),
        })
    tax_rows = [
        {"label": f"{taxes.label(t['type'], loc)} ({taxes.fmt_rate(t['rate'], loc)})", "amount": money(t["amount"], loc)}
        for t in order.tax_lines
    ]
    tax_numbers = []
    if cfg["gst_number"]:
        tax_numbers.append(f"{taxes.label('GST', loc)} : {cfg['gst_number']}" if loc == "fr" else f"GST: {cfg['gst_number']}")
    if cfg["qst_number"]:
        tax_numbers.append(f"{taxes.label('QST', loc)} : {cfg['qst_number']}" if loc == "fr" else f"QST: {cfg['qst_number']}")
    if cfg["hst_number"]:
        tax_numbers.append(f"{taxes.label('HST', loc)} : {cfg['hst_number']}" if loc == "fr" else f"HST: {cfg['hst_number']}")
    method = next((p.method for p in order.payments if p.status == "succeeded" and p.method), None)
    addr = order.billing_address or {}
    return _render_pdf(
        "pdf/invoice.html",
        lb=lb,
        loc=loc,
        cfg=cfg,
        order=order,
        date=fmt_date(order.paid_at or order.created_at, loc),
        lines=lines,
        subtotal=money(order.subtotal_cents, loc),
        multi=money(-order.multi_discount_cents, loc) if order.multi_discount_cents else None,
        promo=money(-order.promo_discount_cents, loc) if order.promo_discount_cents else None,
        tax_rows=tax_rows,
        total=money(order.total_cents, loc),
        refunded=money(order.refunded_cents, loc) if order.refunded_cents else None,
        status=lb.get(f"status_{order.status}", order.status),
        tax_numbers=tax_numbers,
        method=method,
        addr=addr,
    )


def certificate_pdf(db: Session, enrollment: Enrollment, locale: str) -> bytes:
    loc = norm_locale(locale)
    lb = LABELS[loc]
    cfg = site_settings.get_all(db)
    c = enrollment.cohort
    b = c.bootcamp
    return _render_pdf(
        "pdf/certificate.html",
        lb=lb,
        loc=loc,
        cfg=cfg,
        name=enrollment.user.full_name,
        bootcamp=pick(b, "title", loc),
        weeks=b.duration_weeks,
        held=lb["held"].format(start=fmt_date(c.start_date, loc), end=fmt_date(c.end_date, loc)),
        number=enrollment.certificate_number,
        issued=fmt_date(enrollment.completed_at, loc) if enrollment.completed_at else "",
    )
