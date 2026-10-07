"""Saving files to this computer: activities (FIT, GPX, TCX, KML, CSV or the original
zip), workouts (FIT) and courses (GPX)."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from .. import config
from ..client import Ctx
from ..errors import InputError
from .courses import get_course
from .fit import unpack

KINDS = ("activity", "workout", "course")
ACTIVITY_FORMATS = ("fit", "original", "gpx", "tcx", "kml", "csv")


def _folder(ctx: Ctx, output_dir: str | None, remember: bool) -> Path:
    folder = Path(output_dir).expanduser() if output_dir else ctx.cfg.download_dir
    if output_dir and not folder.is_absolute():
        raise InputError("output_dir must be a full path, e.g. C:\\Users\\you\\Documents\\Garmin or /home/you/garmin")
    folder.mkdir(parents=True, exist_ok=True)
    if remember:
        config.write_setting(ctx.cfg.home, "download_dir", str(folder))
    return folder


def course_gpx(course: dict) -> str:
    """A GPX track from Garmin's course points, with its waypoints."""
    name = escape(course.get("courseName") or f"course {course.get('courseId')}")
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="garmin-mcp" xmlns="http://www.topografix.com/GPX/1/1">',
             f"  <metadata><name>{name}</name></metadata>"]
    for p in course.get("coursePoints") or []:
        if isinstance(p, dict) and p.get("lat") is not None and p.get("lon") is not None:
            label = escape(str(p.get("name") or p.get("pointType") or "waypoint"))
            lines.append(f'  <wpt lat="{p["lat"]}" lon="{p["lon"]}"><name>{label}</name></wpt>')
    lines.append(f"  <trk><name>{name}</name><trkseg>")
    for g in course.get("geoPoints") or []:
        lat, lon = g.get("latitude"), g.get("longitude")
        if lat is None or lon is None:
            continue
        ele = f"<ele>{g['elevation']}</ele>" if g.get("elevation") is not None else ""
        lines.append(f'    <trkpt lat="{lat}" lon="{lon}">{ele}</trkpt>')
    lines += ["  </trkseg></trk>", "</gpx>", ""]
    return "\n".join(lines)


def download(
    ctx: Ctx,
    kind: str,
    item_id: int,
    file_format: str,
    output_dir: str | None,
    remember_dir: bool,
) -> str:
    from garminconnect import Garmin

    if kind not in KINDS:
        raise InputError(f"kind is one of: {', '.join(KINDS)}")
    if kind == "activity":
        if file_format not in ACTIVITY_FORMATS:
            raise InputError(f"Activity formats: {', '.join(ACTIVITY_FORMATS)}")
        fmt = {
            "fit": Garmin.ActivityDownloadFormat.ORIGINAL,
            "original": Garmin.ActivityDownloadFormat.ORIGINAL,
            "gpx": Garmin.ActivityDownloadFormat.GPX,
            "tcx": Garmin.ActivityDownloadFormat.TCX,
            "kml": Garmin.ActivityDownloadFormat.KML,
            "csv": Garmin.ActivityDownloadFormat.CSV,
        }[file_format]
        data = ctx.g.download_activity(str(item_id), dl_fmt=fmt)
        if file_format == "fit":
            data = unpack(data)
        ext = "zip" if file_format == "original" else file_format
        name = f"activity_{item_id}.{ext}"
    elif kind == "workout":
        if file_format not in ("fit",):
            raise InputError("Workouts download as fit.")
        data = ctx.g.download_workout(int(item_id))
        name = f"workout_{item_id}.fit"
    else:
        if file_format not in ("gpx",):
            raise InputError("Courses download as gpx.")
        data = course_gpx(get_course(ctx, item_id)).encode("utf-8")
        name = f"course_{item_id}.gpx"

    if not data:
        raise InputError(f"Garmin returned an empty file for {kind} {item_id}.")
    path = _folder(ctx, output_dir, remember_dir) / name
    path.write_bytes(data)
    note = " This folder is now the default." if remember_dir else ""
    return f"Saved {path} ({len(data) / 1024:.0f} KB).{note}"
