#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MAX_MESSAGE_LENGTH = 120
DEFAULT_DISPLAY_DURATION_SECONDS = 20

STATUS_SCHEDULED = "scheduled"
STATUS_DELIVERING = "delivering"
STATUS_DELIVERED = "delivered"
STATUS_CANCELED = "canceled"
ALLOWED_STATUSES = {
    STATUS_SCHEDULED,
    STATUS_DELIVERING,
    STATUS_DELIVERED,
    STATUS_CANCELED,
}

LOCAL_DATETIME_FORMAT = "%Y-%m-%dT%H:%M"
UTC_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class ReminderError(RuntimeError):
    pass


class ReminderValidationError(ReminderError):
    pass


class ReminderConflictError(ReminderError):
    pass


class ReminderNotFoundError(ReminderError):
    pass


def now_utc():
    return datetime.now(timezone.utc)


def format_utc(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime(UTC_DATETIME_FORMAT)


def parse_utc(value):
    return datetime.strptime(value, UTC_DATETIME_FORMAT).replace(tzinfo=timezone.utc)


def parse_local_datetime(value):
    try:
        return datetime.strptime(value, LOCAL_DATETIME_FORMAT)
    except ValueError as exc:
        raise ReminderValidationError(
            f"`scheduled_at_local` must match {LOCAL_DATETIME_FORMAT}."
        ) from exc


def normalize_message(message):
    if not isinstance(message, str):
        raise ReminderValidationError("`message` must be a string.")

    normalized = message.strip()
    if not normalized:
        raise ReminderValidationError("`message` cannot be empty.")
    if len(normalized) > MAX_MESSAGE_LENGTH:
        raise ReminderValidationError(
            f"`message` must be {MAX_MESSAGE_LENGTH} characters or fewer."
        )
    return normalized


def resolve_timezone(timezone_name):
    if not isinstance(timezone_name, str) or not timezone_name:
        raise ReminderValidationError("`timezone` must be a non-empty string.")

    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ReminderValidationError(f"Unknown timezone: {timezone_name}") from exc


def local_to_utc(scheduled_at_local, timezone_name):
    local_dt = parse_local_datetime(scheduled_at_local)
    zone = resolve_timezone(timezone_name)
    localized = local_dt.replace(tzinfo=zone)
    return localized.astimezone(timezone.utc)


def validate_display_duration_seconds(value):
    if not isinstance(value, int) or value <= 0:
        raise ReminderValidationError("`display_duration_seconds` must be a positive integer.")
    return value


def ensure_future_datetime(scheduled_at_utc, *, now=None):
    reference = now or now_utc()
    if scheduled_at_utc <= reference:
        raise ReminderValidationError("Reminder time must be in the future.")


@dataclass(frozen=True)
class ReminderRecord:
    id: str
    message: str
    scheduled_at_local: str
    timezone: str
    scheduled_at_utc: str
    status: str
    created_at_utc: str
    updated_at_utc: str
    delivered_at_utc: Optional[str]
    next_attempt_at_utc: Optional[str]
    attempt_count: int
    last_error: Optional[str]
    display_duration_seconds: int
    canceled_at_utc: Optional[str]

    def to_api_dict(self):
        return {
            "id": self.id,
            "message": self.message,
            "scheduled_at_local": self.scheduled_at_local,
            "timezone": self.timezone,
            "scheduled_at_utc": self.scheduled_at_utc,
            "status": self.status,
            "display_duration_seconds": self.display_duration_seconds,
            "attempt_count": self.attempt_count,
            "last_error": self.last_error,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "delivered_at_utc": self.delivered_at_utc,
        }


def build_reminder_record(
    message,
    scheduled_at_local,
    timezone_name,
    *,
    display_duration_seconds=DEFAULT_DISPLAY_DURATION_SECONDS,
    reminder_id=None,
    now=None,
):
    normalized_message = normalize_message(message)
    duration = validate_display_duration_seconds(display_duration_seconds)
    scheduled_utc = local_to_utc(scheduled_at_local, timezone_name)
    reference_now = now or now_utc()
    ensure_future_datetime(scheduled_utc, now=reference_now)

    now_text = format_utc(reference_now)
    return ReminderRecord(
        id=reminder_id or str(uuid4()),
        message=normalized_message,
        scheduled_at_local=parse_local_datetime(scheduled_at_local).strftime(LOCAL_DATETIME_FORMAT),
        timezone=timezone_name,
        scheduled_at_utc=format_utc(scheduled_utc),
        status=STATUS_SCHEDULED,
        created_at_utc=now_text,
        updated_at_utc=now_text,
        delivered_at_utc=None,
        next_attempt_at_utc=None,
        attempt_count=0,
        last_error=None,
        display_duration_seconds=duration,
        canceled_at_utc=None,
    )
