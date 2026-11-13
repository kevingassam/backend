"""Session generation from a cohort schedule (EF-63) and iCalendar export (EF-51)."""

from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import utcnow
from app.models import ClassSession, Cohort


def parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def generate_sessions(db: Session, cohort: Cohort, *, replace_future: bool = True) -> list[ClassSession]:
    """Create one session per scheduled weekday between start and end dates (in the cohort's time zone).

    Existing future sessions that were never modified (status scheduled, sequence 0) are replaced;
    moved/cancelled/edited sessions and past sessions are kept.
    """
    tz = ZoneInfo(cohort.time_zone)
    now = utcnow()
    if replace_future:
        for s in list(cohort.sessions):
            if s.starts_at > now and s.status == "scheduled" and s.sequence == 0:
                cohort.sessions.remove(s)
                db.delete(s)
        db.flush()
    existing = {s.starts_at for s in cohort.sessions}
    created = []
    day: date = cohort.start_date
    n = 0
    while day <= cohort.end_date:
        for slot in cohort.schedule or []:
            if int(slot["weekday"]) == day.weekday():
                start = datetime.combine(day, parse_hhmm(slot["start"]), tzinfo=tz).astimezone(UTC)
                end = datetime.combine(day, parse_hhmm(slot["end"]), tzinfo=tz).astimezone(UTC)
                if start in existing or (replace_future and start <= now):
                    continue
                n += 1
                s = ClassSession(cohort_id=cohort.id, starts_at=start, ends_at=end, title="")
                cohort.sessions.append(s)
                created.append(s)
        day += timedelta(days=1)
    db.flush()
    renumber(cohort)
    return created


def renumber(cohort: Cohort) -> None:
    """Default titles 'Séance 1', 'Séance 2'... stored language-neutral as '#n' when empty."""
    active = sorted([s for s in cohort.sessions if s.status != "cancelled"], key=lambda s: s.starts_at)
    for i, s in enumerate(active, start=1):
        if not s.title or s.title.startswith("#"):
            s.title = f"#{i}"


def session_title(s: ClassSession, bootcamp_title: str, locale: str) -> str:
    if s.title.startswith("#"):
        n = s.title[1:]
        return f"{bootcamp_title} — {'Séance' if locale == 'fr' else 'Session'} {n}"
    return f"{bootcamp_title} — {s.title}"


def _ics_dt(d: datetime) -> str:
    return d.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    out, raw = [], line.encode()
    while len(raw) > 74:
        cut = 74
        while (raw[cut] & 0xC0) == 0x80:  # don't split UTF-8 sequences
            cut -= 1
        out.append(raw[:cut].decode())
        raw = raw[cut:]
    out.append(raw.decode())
    return "\r\n ".join(out)


def build_ics(events: list[dict], calname: str = "Bootcamps", method: str = "PUBLISH") -> bytes:
    """events: {uid, start, end, summary, description, url, status, sequence}"""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Bootcamps//Sessions//FR",
        "CALSCALE:GREGORIAN",
        f"METHOD:{method}",
        f"X-WR-CALNAME:{_esc(calname)}",
    ]
    stamp = _ics_dt(utcnow())
    for e in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_ics_dt(e['start'])}",
            f"DTEND:{_ics_dt(e['end'])}",
            f"SUMMARY:{_esc(e['summary'])}",
            f"DESCRIPTION:{_esc(e.get('description', ''))}",
            f"SEQUENCE:{e.get('sequence', 0)}",
            f"STATUS:{'CANCELLED' if e.get('status') == 'cancelled' else 'CONFIRMED'}",
        ]
        if e.get("url"):
            lines.append(f"URL:{e['url']}")
            lines.append(f"LOCATION:{_esc(e['url'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return ("\r\n".join(_fold(line) for line in lines) + "\r\n").encode()


def session_event(s: ClassSession, bootcamp_title: str, locale: str, include_link: bool) -> dict:
    dashboard = f"{settings.frontend_url}/{locale}/{'tableau-de-bord' if locale == 'fr' else 'dashboard'}"
    return {
        "uid": f"session-{s.id}@bootcamps",
        "start": s.starts_at,
        "end": s.ends_at,
        "summary": session_title(s, bootcamp_title, locale),
        "description": (("Lien de la séance : " if locale == "fr" else "Session link: ") + dashboard),
        "url": dashboard if include_link else None,
        "status": s.status,
        "sequence": s.sequence,
    }


def google_calendar_url(s: ClassSession, title: str, details: str = "") -> str:
    fmt = "%Y%m%dT%H%M%SZ"
    q = urlencode({
        "action": "TEMPLATE",
        "text": title,
        "dates": f"{s.starts_at.astimezone(UTC).strftime(fmt)}/{s.ends_at.astimezone(UTC).strftime(fmt)}",
        "details": details,
    })
    return f"https://calendar.google.com/calendar/render?{q}"


def weekday_name(weekday: int, locale: str) -> str:
    fr = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
    en = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return (fr if locale == "fr" else en)[weekday]


def schedule_text(cohort: Cohort, locale: str) -> str:
    """e.g. 'mar. et jeu., 18 h 30 à 21 h HE' / 'Tue & Thu, 6:30 PM – 9:00 PM ET'."""
    if not cohort.schedule:
        return ""
    groups: dict[tuple[str, str], list[int]] = {}
    for slot in cohort.schedule:
        groups.setdefault((slot["start"], slot["end"]), []).append(int(slot["weekday"]))
    parts = []
    for (start, end), days in groups.items():
        days_txt = (" et " if locale == "fr" else " & ").join(weekday_name(d, locale) for d in sorted(days))
        if locale == "fr":
            fmt = lambda v: (lambda t: f"{t.hour} h" + (f" {t.minute:02d}" if t.minute else ""))(parse_hhmm(v))  # noqa: E731
            parts.append(f"{days_txt}, {fmt(start)} à {fmt(end)} HE")
        else:
            fmt = lambda v: parse_hhmm(v).strftime("%I:%M %p").lstrip("0")  # noqa: E731
            parts.append(f"{days_txt}, {fmt(start)} – {fmt(end)} ET")
    return "; ".join(parts)
