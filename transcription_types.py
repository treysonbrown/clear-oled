#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from translation_core import normalize_text


SESSION_STATUS_RUNNING = "running"
SESSION_STATUS_STOPPED = "stopped"
SESSION_STATUS_INTERRUPTED = "interrupted"
SESSION_STATUS_FAILED = "failed"
SESSION_STATUSES = {
    SESSION_STATUS_RUNNING,
    SESSION_STATUS_STOPPED,
    SESSION_STATUS_INTERRUPTED,
    SESSION_STATUS_FAILED,
}

SERVICE_STATE_IDLE = "idle"
SERVICE_STATE_STARTING = "starting"
SERVICE_STATE_RUNNING = "running"
SERVICE_STATE_STOPPING = "stopping"

MIC_STATE_IDLE = "idle"
MIC_STATE_READY = "ready"
MIC_STATE_LISTENING = "listening"
MIC_STATE_PERMISSION_DENIED = "permission_denied"
MIC_STATE_ERROR = "error"

DISPLAY_STATE_UNKNOWN = "unknown"
DISPLAY_STATE_CONNECTED = "connected"
DISPLAY_STATE_DISCONNECTED = "disconnected"

DEFAULT_LANGUAGE = "en"
DEFAULT_MODEL = "macos-speech"
UTC_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
MAX_SEGMENT_TEXT_LENGTH = 400


class TranscriptionError(RuntimeError):
    pass


class TranscriptionConflictError(TranscriptionError):
    pass


class TranscriptionNotFoundError(TranscriptionError):
    pass


class TranscriptionValidationError(TranscriptionError):
    pass


def now_utc():
    return datetime.now(timezone.utc)


def format_utc(value):
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime(UTC_DATETIME_FORMAT)


def parse_utc(value):
    if value is None:
        return None
    return datetime.strptime(value, UTC_DATETIME_FORMAT).replace(tzinfo=timezone.utc)


def normalize_transcript_text(text):
    normalized = normalize_text(text)
    if len(normalized) > MAX_SEGMENT_TEXT_LENGTH:
        normalized = normalized[:MAX_SEGMENT_TEXT_LENGTH].rstrip()
    return normalized


def optional_bool_to_int(value):
    if value is None:
        return None
    return 1 if value else 0


def int_to_bool(value):
    if value is None:
        return None
    return bool(value)


@dataclass(frozen=True)
class TranscriptionSessionRecord:
    id: str
    status: str
    started_at_utc: str
    stopped_at_utc: Optional[str]
    device_name: Optional[str]
    model_name: str
    language: str
    display_connected: Optional[bool]
    last_error: Optional[str]

    def to_api_dict(self):
        return {
            "id": self.id,
            "status": self.status,
            "started_at_utc": self.started_at_utc,
            "stopped_at_utc": self.stopped_at_utc,
            "device_name": self.device_name,
            "model_name": self.model_name,
            "language": self.language,
            "display_connected": self.display_connected,
            "last_error": self.last_error,
        }


@dataclass(frozen=True)
class TranscriptionSegmentRecord:
    id: str
    session_id: str
    sequence_no: int
    started_at_utc: str
    ended_at_utc: str
    text: str
    oled_text: str
    created_at_utc: str

    def to_api_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "sequence_no": self.sequence_no,
            "started_at_utc": self.started_at_utc,
            "ended_at_utc": self.ended_at_utc,
            "text": self.text,
            "oled_text": self.oled_text,
            "created_at_utc": self.created_at_utc,
        }


def build_session_record(
    *,
    device_name,
    model_name,
    language=DEFAULT_LANGUAGE,
    session_id=None,
    now=None,
):
    started_at = format_utc(now or now_utc())
    return TranscriptionSessionRecord(
        id=session_id or str(uuid4()),
        status=SESSION_STATUS_RUNNING,
        started_at_utc=started_at,
        stopped_at_utc=None,
        device_name=normalize_transcript_text(device_name or "") or None,
        model_name=normalize_transcript_text(model_name) or DEFAULT_MODEL,
        language=language or DEFAULT_LANGUAGE,
        display_connected=None,
        last_error=None,
    )


def build_segment_record(
    *,
    session_id,
    sequence_no,
    started_at,
    ended_at,
    text,
    oled_text,
    created_at=None,
    segment_id=None,
):
    normalized_text = normalize_transcript_text(text)
    normalized_oled = normalize_transcript_text(oled_text)
    if not normalized_text:
        raise TranscriptionValidationError("Transcript segment text cannot be empty.")
    return TranscriptionSegmentRecord(
        id=segment_id or str(uuid4()),
        session_id=session_id,
        sequence_no=sequence_no,
        started_at_utc=format_utc(started_at),
        ended_at_utc=format_utc(ended_at),
        text=normalized_text,
        oled_text=normalized_oled,
        created_at_utc=format_utc(created_at or now_utc()),
    )
