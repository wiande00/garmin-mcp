"""Workout templates and the workout reference, as MCP resources (taxuspt had the same
five). Templates are written in garmin_create_workout's step language, so they can be
passed straight to it after adjusting."""

from __future__ import annotations

import json
from typing import Any

from .workout_dsl import CONDITIONS, SPORTS, STEP_TYPES, TARGETS

TEMPLATES: dict[str, dict[str, Any]] = {
    "simple-run": {
        "name": "Easy run 40 min",
        "sport": "running",
        "steps": [
            {"type": "warmup", "duration": "10:00", "hr_zone": 1},
            {"type": "interval", "duration": "25:00", "hr_zone": 2},
            {"type": "cooldown", "duration": "5:00"},
        ],
    },
    "interval-running": {
        "name": "6 x 400 m",
        "sport": "running",
        "steps": [
            {"type": "warmup", "duration": "15:00", "hr_zone": 2},
            {
                "repeat": 6,
                "steps": [
                    {"type": "interval", "distance_km": 0.4, "pace_min_km": ["3:50", "4:05"]},
                    {"type": "recovery", "duration": "1:30"},
                ],
            },
            {"type": "cooldown", "duration": "10:00"},
        ],
    },
    "tempo-run": {
        "name": "Tempo 3 x 8 min",
        "sport": "running",
        "steps": [
            {"type": "warmup", "duration": "12:00", "hr_zone": 2},
            {
                "repeat": 3,
                "steps": [
                    {"type": "interval", "duration": "8:00", "hr_zone": 4},
                    {"type": "recovery", "duration": "2:00", "hr_zone": 1},
                ],
            },
            {"type": "cooldown", "duration": "10:00"},
        ],
    },
    "strength-circuit": {
        "name": "Full body circuit",
        "sport": "strength_training",
        "steps": [
            {
                "repeat": 3,
                "steps": [
                    {"exercise": "Barbell Back Squat", "reps": 8, "weight_kg": 60},
                    {"type": "rest", "duration": "1:00"},
                    {"exercise": "Barbell Bench Press", "reps": 8, "weight_kg": 50},
                    {"type": "rest", "duration": "1:00"},
                    {"exercise": "Pull-up", "reps": 6},
                    {"type": "rest", "duration": "2:00"},
                ],
            }
        ],
    },
}


def structure() -> dict[str, Any]:
    """How garmin_create_workout's steps work, and Garmin's own ids for raw uploads."""
    return {
        "step_language": {
            "step": "{type, one ending, at most one target, strength fields, note}",
            "type": ["warmup", "interval", "recovery", "rest", "cooldown", "other"],
            "endings": {
                "duration": "'m:ss', 'h:mm:ss' or seconds",
                "distance_km": "number",
                "reps": "strength repetitions",
                "lap_button": "true",
            },
            "targets": {
                "hr_zone": "1-5",
                "hr_bpm": "[low, high]",
                "pace_min_km": "['4:30', '4:45']",
                "speed_kmh": "[low, high]",
                "power_w": "[low, high]",
                "power_zone": "1-7",
                "cadence": "[low, high]",
            },
            "strength": {"exercise": "catalog name", "weight_kg": "number"},
            "repeat": "{repeat: n, steps: [...]}",
        },
        "garmin_ids": {
            "sports": {k: v[0] for k, v in SPORTS.items()},
            "step_types": {k: v[0] for k, v in STEP_TYPES.items()},
            "end_conditions": CONDITIONS,
            "target_types": TARGETS,
            "rules": [
                "Numeric ids and keys must agree; Garmin goes by the id.",
                "targetValueOne/Two and zoneNumber belong on the step, not inside targetType.",
                "A zoneNumber and a custom range are mutually exclusive.",
                "Watt ranges: target type 2 'power.zone' with values (never type 6).",
                "Pace and speed ranges are m/s; for pace the slower speed is targetValueOne.",
                "Repeat groups: endCondition 7 'iterations' and numberOfIterations.",
                (
                    "Strength: endCondition 10 'reps', category + exerciseName from the catalog, "
                    "weightValue in grams with unit kilogram."
                ),
                "Workout names are at most 256 characters.",
            ],
        },
    }


def _reader(text: str):
    def read() -> str:
        return text

    return read


def register(server) -> None:
    for slug, template in TEMPLATES.items():
        server.resource(
            f"workout://templates/{slug}",
            name=f"workout-template-{slug}",
            description=f"Workout template: {template['name']} (garmin_create_workout format)",
            mime_type="application/json",
        )(_reader(json.dumps(template, indent=2)))
    server.resource(
        "workout://reference/structure",
        name="workout-structure",
        description="How workout steps, targets and Garmin's workout ids fit together",
        mime_type="application/json",
    )(_reader(json.dumps(structure(), indent=2)))
