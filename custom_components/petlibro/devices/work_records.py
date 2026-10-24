"""Helpers for classifying and parsing PETLIBRO work records (/device/workRecord/list)."""

from __future__ import annotations

import json
import re
from typing import Any

# Feeding event types, see _feeding_event_type
FEEDING_SCHEDULED = "scheduled"
FEEDING_MANUAL = "manual"
FEEDING_LID_CLOSED = "lid_closed"
FEEDING_FAILED = "failed"
FEEDING_OTHER = "feed"
FEEDING_EVENT_TYPES = [FEEDING_SCHEDULED, FEEDING_MANUAL, FEEDING_LID_CLOSED, FEEDING_FAILED, FEEDING_OTHER]

PET_VISIT_ATE = "ate"
PET_VISIT_DETECTED = "detected"
PET_VISIT_EVENT_TYPES = [PET_VISIT_ATE, PET_VISIT_DETECTED]


def parse_params(record: dict) -> dict[str, Any]:
    """Return the record's JSON-encoded params as a dict ({} if absent/invalid)."""
    params = record.get("params")
    if isinstance(params, dict):
        return params
    if not isinstance(params, str) or not params:
        return {}
    try:
        parsed = json.loads(params)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_duration(value: Any) -> int | None:
    """Parse a duration like "05s" or "01m50s" into seconds."""
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", str(value or "").strip())
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (int(group or 0) for group in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def is_feeding_record(record: dict) -> bool:
    record_type = record.get("type") or ""
    return record_type.startswith("GRAIN_OUTPUT") or record_type == "WET_GRAIN_EVENT"


def feeding_event_type(record: dict) -> str:
    event_code = record.get("eventType") or ""
    if "FAIL" in f"{record.get('type') or ''} {event_code}":
        return FEEDING_FAILED
    # Polar: "The lid has been open for four hours and automatically closed."
    if event_code == "MANUAL_FEEDING_AUTO_SUCCESS" or "CLOSE" in event_code:
        return FEEDING_LID_CLOSED
    if "PLAN" in event_code:
        return FEEDING_SCHEDULED
    if "MANUAL" in event_code:
        return FEEDING_MANUAL
    return FEEDING_OTHER


def is_pet_visit_record(record: dict) -> bool:
    return record.get("type") == "DETECTION_EVENT"


def pet_visit_event_type(record: dict) -> str:
    return PET_VISIT_ATE if record.get("eventType") == "PET_DETECT_LEAVE_EVENT" else PET_VISIT_DETECTED


def pet_visit_duration(record: dict) -> int | None:
    """Eating duration in seconds, e.g. from params {"seconds": "05s"}."""
    return parse_duration(parse_params(record).get("seconds"))
