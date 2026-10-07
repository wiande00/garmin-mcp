"""Body measurements: weigh-ins, body composition, blood pressure."""

from __future__ import annotations

from datetime import date
from typing import Any

from .. import units
from ..client import Ctx
from ..dates import local_time, trim_local
from ..errors import InputError
from ..shape import dumps

KINDS = ("weigh_ins", "body_composition", "blood_pressure")
MAX_DAYS = 366


def weigh_entry(w: dict, tz) -> dict:
    return {
        "id": w.get("samplePk"),
        "date": w.get("calendarDate"),
        "time": local_time(w.get("timestampGMT"), tz),
        "weight_kg": units.kg(w.get("weight")),
        "bmi": units.num(w.get("bmi")),
        "body_fat_pct": units.num(w.get("bodyFat")),
        "body_water_pct": units.num(w.get("bodyWater")),
        "bone_mass_kg": units.kg(w.get("boneMass")),
        "muscle_mass_kg": units.kg(w.get("muscleMass")),
        "visceral_fat": w.get("visceralFat"),
        "metabolic_age": w.get("metabolicAge"),
        "physique_rating": w.get("physiqueRating"),
        "source": w.get("sourceType"),
    }


def _average(avg: dict | None) -> dict | None:
    if not avg or avg.get("weight") is None:
        return None
    return {
        "weight_kg": units.kg(avg.get("weight")),
        "bmi": units.num(avg.get("bmi")),
        "body_fat_pct": units.num(avg.get("bodyFat")),
        "muscle_mass_kg": units.kg(avg.get("muscleMass")),
    }


def weigh_ins(ctx: Ctx, a: date, b: date) -> list[dict]:
    if a == b:
        data = ctx.g.get_daily_weigh_ins(a.isoformat()) or {}
        entries = data.get("dateWeightList") or []
    else:
        data = ctx.g.get_weigh_ins(a.isoformat(), b.isoformat()) or {}
        entries = [
            w for day in data.get("dailyWeightSummaries") or [] for w in day.get("allWeightMetrics") or []
        ]
    rows = [weigh_entry(w, ctx.tz) for w in entries]
    return sorted(rows, key=lambda r: (r.get("date") or "", r.get("time") or ""))


def _body_composition(ctx: Ctx, a: date, b: date) -> dict:
    data = ctx.g.get_body_composition(a.isoformat(), b.isoformat()) or {}
    return {
        "entries": [weigh_entry(w, ctx.tz) for w in data.get("dateWeightList") or []],
        "average": _average(data.get("totalAverage")),
    }


def blood_pressure_rows(data: dict) -> list[dict]:
    rows = []
    for summary in data.get("measurementSummaries") or []:
        for m in summary.get("measurements") or []:
            rows.append(
                {
                    "id": m.get("version"),
                    "date": (m.get("measurementTimestampLocal") or "")[:10] or summary.get("startDate"),
                    "time": trim_local(m.get("measurementTimestampLocal")),
                    "systolic": m.get("systolic"),
                    "diastolic": m.get("diastolic"),
                    "pulse_bpm": m.get("pulse"),
                    "category": m.get("categoryName") or m.get("category"),
                    "notes": m.get("notes"),
                    "source": m.get("sourceType"),
                }
            )
    return sorted(rows, key=lambda r: r.get("time") or "")


def body(ctx: Ctx, kind: str, start_date: str | None, end_date: str | None) -> str:
    if kind not in KINDS:
        raise InputError(f"Unknown kind {kind!r}. Choose from: {', '.join(KINDS)}")
    a, b = ctx.span(start_date, end_date or start_date, max_days=MAX_DAYS, default_days=1)
    out: dict[str, Any] = {"kind": kind, "start": a.isoformat(), "end": b.isoformat()}
    if kind == "weigh_ins":
        out["entries"] = weigh_ins(ctx, a, b)
    elif kind == "body_composition":
        out.update(_body_composition(ctx, a, b))
    else:
        out["entries"] = blood_pressure_rows(ctx.g.get_blood_pressure(a.isoformat(), b.isoformat()) or {})
    if not out.get("entries"):
        out["note"] = "Nothing recorded in this period."
    return dumps(out)
