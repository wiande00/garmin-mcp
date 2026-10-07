"""Menstrual cycle and pregnancy tracking (read-only), for parity with taxuspt."""

from __future__ import annotations

from ..client import Ctx
from ..dates import chunks
from ..errors import InputError
from ..shape import dumps

KINDS = ("pregnancy", "menstrual_day", "menstrual_calendar")


def womens_health(ctx: Ctx, kind: str, date_text: str | None, start: str | None, end: str | None) -> str:
    g = ctx.g
    if kind == "pregnancy":
        return dumps(g.get_pregnancy_summary())
    if kind == "menstrual_day":
        return dumps(g.get_menstrual_data_for_date(ctx.day(date_text).isoformat()))
    if kind == "menstrual_calendar":
        a, b = ctx.span(start, end, max_days=731, default_days=92)
        # Garmin answers at most about three months at a time.
        parts = [g.get_menstrual_calendar_data(s.isoformat(), e.isoformat()) for s, e in chunks(a, b, 92)]
        return dumps(parts[0] if len(parts) == 1 else parts)
    raise InputError(f"Unknown kind {kind!r}. Choose from: {', '.join(KINDS)}")
