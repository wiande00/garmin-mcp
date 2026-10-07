"""Turning Garmin payloads into short answers.

Curated output drops empty values and keeps units in the key names
(`distance_km`, `avg_hr_bpm`), so the model never has to guess a unit. Everything
leaves as compact JSON, capped in length so one call can't flood the context.
"""

from __future__ import annotations

import json
from typing import Any

EMPTY = (None, "", [], {})


def compact(value: Any) -> Any:
    """Recursively drop empty values from dicts. Inside lists only empty dicts and lists
    go: a None in a list holds a position (a column in a row, the low end of a range)."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            v = compact(v)
            if not _is_empty(v):
                out[k] = v
        return out
    if isinstance(value, (list, tuple)):
        return [c for c in (compact(v) for v in value) if not (isinstance(c, (dict, list)) and not c)]
    return value


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def dumps(value: Any, *, keep_empty: bool = False) -> str:
    """Compact JSON. Non-ASCII stays readable (å ä ö)."""
    data = value if keep_empty else compact(value)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def raw(value: Any) -> str:
    """Garmin's own payload, unchanged except for whitespace."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def cap(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n[truncated: {len(text) - max_chars} of {len(text)} characters left out. "
        "Ask for less: a shorter date range, fewer sections, or detail='summary'.]"
    )


def pick(source: dict | None, fields: dict[str, Any]) -> dict:
    """Build an output dict. Each value is a source key, or a callable taking the source."""
    if not source:
        return {}
    out = {}
    for out_key, spec in fields.items():
        out[out_key] = spec(source) if callable(spec) else source.get(spec)
    return out


def first(*values: Any) -> Any:
    """The first value that is not None."""
    for v in values:
        if v is not None:
            return v
    return None


def dig(source: Any, *path: Any) -> Any:
    """Follow keys and indexes, returning None at the first gap."""
    cur = source
    for key in path:
        if cur is None:
            return None
        if isinstance(key, int):
            if not isinstance(cur, list) or not -len(cur) <= key < len(cur):
                return None
            cur = cur[key]
        else:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
    return cur


def downsample(points: list, max_points: int) -> list:
    """Evenly thin a series, always keeping the first and last point."""
    if len(points) <= max_points or max_points < 2:
        return points
    step = (len(points) - 1) / (max_points - 1)
    return [points[round(i * step)] for i in range(max_points)]
