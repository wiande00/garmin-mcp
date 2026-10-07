"""Dates as the user means them: calendar days in the account's time zone."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .errors import InputError

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def today(tz: ZoneInfo) -> date:
    return datetime.now(tz).date()


def parse(value: str | None, tz: ZoneInfo, *, default: str = "today", name: str = "date") -> date:
    """`today`, `yesterday` or `YYYY-MM-DD`. Empty means `default`."""
    text = (value or default).strip().lower()
    if text == "today":
        return today(tz)
    if text == "yesterday":
        return today(tz) - timedelta(days=1)
    if not _ISO.match(text):
        raise InputError(f"{name} must be YYYY-MM-DD, 'today' or 'yesterday', not {value!r}")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise InputError(f"{name} {value!r} is not a real date") from None


def parse_range(
    start: str | None,
    end: str | None,
    tz: ZoneInfo,
    *,
    max_days: int,
    default_days: int = 7,
) -> tuple[date, date]:
    """An inclusive range. A missing end means today; a missing start means
    `default_days` ending at `end`."""
    last = parse(end, tz, name="end_date")
    first = (
        parse(start, tz, name="start_date")
        if start
        else last - timedelta(days=default_days - 1)
    )
    if first > last:
        raise InputError(f"start_date {first} is after end_date {last}")
    span = (last - first).days + 1
    if span > max_days:
        raise InputError(
            f"That range is {span} days; this view allows at most {max_days}. "
            "Split it into shorter ranges."
        )
    return first, last


def each_day(first: date, last: date):
    d = first
    while d <= last:
        yield d
        d += timedelta(days=1)


def chunks(first: date, last: date, size: int):
    """Consecutive windows of at most `size` days covering first..last."""
    start = first
    while start <= last:
        end = min(start + timedelta(days=size - 1), last)
        yield start, end
        start = end + timedelta(days=1)


_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")


def moment(value: str | None, tz: ZoneInfo) -> tuple[datetime, bool]:
    """When something happened, as an aware datetime in `tz`.

    Accepts nothing (now), `HH:MM` (today), `YYYY-MM-DD` (that day; the bool says the
    time of day was not given) or `YYYY-MM-DD HH:MM` / `YYYY-MM-DDTHH:MM`.
    """
    now = datetime.now(tz)
    text = (value or "").strip()
    if not text:
        return now.replace(microsecond=0), True
    clock = _CLOCK.match(text)
    if clock:
        h, m = int(clock.group(1)), int(clock.group(2))
        if h > 23 or m > 59:
            raise InputError(f"{value!r} is not a time of day")
        return now.replace(hour=h, minute=m, second=0, microsecond=0), True
    if _ISO.match(text):
        d = parse(text, tz)
        return datetime(d.year, d.month, d.day, 12, 0, tzinfo=tz), False
    try:
        dt = datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        raise InputError(
            f"{value!r}: use 'YYYY-MM-DD HH:MM', 'HH:MM' for today, or leave it out for now"
        ) from None
    return (dt if dt.tzinfo else dt.replace(tzinfo=tz)).astimezone(tz), True


def local_time(ms: float | None, tz: ZoneInfo) -> str | None:
    """Epoch milliseconds (GMT) as local `YYYY-MM-DD HH:MM`."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, UTC).astimezone(tz).strftime(
        "%Y-%m-%d %H:%M"
    )


def gmt_string_to_local(value: str | None, tz: ZoneInfo) -> str | None:
    """Garmin's `...GMT` timestamps (`2026-10-06T05:12:00.0`) as local `YYYY-MM-DD HH:MM`."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace(" ", "T").rstrip("Z")[:19])
    except ValueError:
        return value
    return dt.replace(tzinfo=UTC).astimezone(tz).strftime("%Y-%m-%d %H:%M")


def trim_local(value: str | None) -> str | None:
    """Garmin's local timestamps (`2026-10-06 07:12:00`) as `YYYY-MM-DD HH:MM`."""
    if not value:
        return None
    return value.replace("T", " ")[:16]
