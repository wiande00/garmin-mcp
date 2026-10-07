"""Garmin's unit quirks, in one place.

Garmin mixes units freely: metres and seconds for activities, grams for weight,
milliseconds since the epoch for timestamps, RPE stored times ten, weather in
Fahrenheit and knots with no marker. Every conversion goes through here so a quirk is fixed
once. Output is always metric.
"""

from __future__ import annotations

FEEL = {0: "very weak", 25: "weak", 50: "normal", 75: "strong", 100: "very strong"}
FEEL_VALUES = tuple(FEEL)


def num(value, digits: int = 1):
    """Round a number, passing None through."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def whole(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def km(metres, digits: int = 2):
    return None if metres is None else num(float(metres) / 1000, digits)


def kg(grams, digits: int = 1):
    """Weights arrive in grams."""
    return None if grams is None else num(float(grams) / 1000, digits)


def hours(seconds, digits: int = 2):
    return None if seconds is None else num(float(seconds) / 3600, digits)


def minutes(seconds, digits: int = 1):
    return None if seconds is None else num(float(seconds) / 60, digits)


def hms(seconds) -> str | None:
    """`1:02:03`, or `2:03` under an hour."""
    if seconds is None:
        return None
    total = round(float(seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def pace_min_km(speed_mps) -> str | None:
    """Speed in m/s as a running pace `m:ss` per km."""
    if not speed_mps:
        return None
    seconds_per_km = 1000 / float(speed_mps)
    if seconds_per_km > 3600:  # slower than an hour per km: standing still
        return None
    m, s = divmod(round(seconds_per_km), 60)
    return f"{m}:{s:02d}"


def kmh(speed_mps, digits: int = 1):
    return None if speed_mps is None else num(float(speed_mps) * 3.6, digits)


def celsius_from_f(fahrenheit, digits: int = 1):
    """Activity weather arrives in Fahrenheit with no unit marker."""
    return None if fahrenheit is None else num((float(fahrenheit) - 32) * 5 / 9, digits)


def kmh_from_knots(knots):
    """Activity weather wind arrives in knots: Garmin sent 9 for a run where Connect
    showed 17 km/h (9 kn = 16.7 km/h). The data comes from airport weather stations,
    which report knots."""
    return None if knots is None else whole(float(knots) * 1.852)


def rpe_from_garmin(value):
    """RPE is stored times ten (`directWorkoutRpe` 70 means 7)."""
    return None if value is None else num(float(value) / 10, 1)


def rpe_to_garmin(rpe: float) -> int:
    return round(rpe * 10)


def feel_label(value) -> str | None:
    if value is None:
        return None
    nearest = min(FEEL, key=lambda k: abs(k - float(value)))
    return FEEL[nearest]


def lactate_speed_mps(raw):
    """Lactate-threshold speed. Garmin stores it in units of 10 m/s, so 0.392 is 3.92 m/s
    (4:15 per km); taxuspt read it as 1/raw and reported 6:30 instead (#376, #378).
    A value already in a plausible m/s range is left alone."""
    if raw is None:
        return None
    value = float(raw)
    return value * 10 if value < 1.5 else value


def recovery_hours(minutes_value, digits: int = 1):
    """Recovery time arrives in minutes (taxuspt #187 reported minutes as hours)."""
    return None if minutes_value is None else num(float(minutes_value) / 60, digits)
