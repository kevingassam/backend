"""Canadian sales taxes by billing province (EF-44, EF-94).

Rates to be confirmed by the owner's accountant. Quebec: GST 5 % + QST 9.975 %.
HST provinces: ON 13 %, NB/NL/PE 15 %, NS 14 % (since 2025-04-01). Other provinces/territories: GST 5 %.
"""

from decimal import ROUND_HALF_UP, Decimal

PROVINCES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories",
    "NU": "Nunavut", "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Québec",
    "SK": "Saskatchewan", "YT": "Yukon",
}

TAX_TABLE: dict[str, list[tuple[str, Decimal]]] = {
    "QC": [("GST", Decimal("5")), ("QST", Decimal("9.975"))],
    "ON": [("HST", Decimal("13"))],
    "NB": [("HST", Decimal("15"))],
    "NL": [("HST", Decimal("15"))],
    "PE": [("HST", Decimal("15"))],
    "NS": [("HST", Decimal("14"))],
}
DEFAULT_CANADA = [("GST", Decimal("5"))]

LABELS = {
    "GST": {"fr": "TPS", "en": "GST"},
    "QST": {"fr": "TVQ", "en": "QST"},
    "HST": {"fr": "TVH", "en": "HST"},
}
ORDER = ["GST", "HST", "QST"]


def rates_for(province: str | None, country: str = "CA") -> list[tuple[str, Decimal]]:
    if country and country.upper() != "CA":
        return []  # no Canadian sales tax outside Canada
    if not province:
        return TAX_TABLE["QC"]  # estimate with Quebec rates until a billing address is known
    return TAX_TABLE.get(province.upper(), DEFAULT_CANADA)


def compute(amount_cents: int, province: str | None, country: str = "CA", taxable: bool = True) -> list[dict]:
    """Tax lines for one amount; each tax rounded half-up to the cent."""
    if not taxable or amount_cents <= 0:
        return []
    lines = []
    for tax_type, rate in rates_for(province, country):
        amount = (Decimal(amount_cents) * rate / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        lines.append({"type": tax_type, "rate": str(rate), "amount": int(amount)})
    return lines


def merge(lines_list: list[list[dict]]) -> list[dict]:
    merged: dict[str, dict] = {}
    for lines in lines_list:
        for line in lines:
            m = merged.setdefault(line["type"], {"type": line["type"], "rate": line["rate"], "amount": 0})
            m["amount"] += line["amount"]
    return sorted(merged.values(), key=lambda x: ORDER.index(x["type"]) if x["type"] in ORDER else 99)


def label(tax_type: str, locale: str) -> str:
    return LABELS.get(tax_type, {}).get(locale, tax_type)


def fmt_rate(rate: str, locale: str) -> str:
    r = rate.rstrip("0").rstrip(".") if "." in rate else rate
    return f"{r.replace('.', ',')} %" if locale == "fr" else f"{r}%"
