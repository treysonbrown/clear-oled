#!/usr/bin/env python3

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from transcription_types import (
    SESSION_STATUS_FAILED,
    SESSION_STATUS_INTERRUPTED,
    SESSION_STATUS_RUNNING,
    SESSION_STATUS_STOPPED,
    TranscriptionNotFoundError,
    TranscriptionSessionRecord,
    TranscriptionSegmentRecord,
    build_segment_record,
    build_session_record,
    format_utc,
    int_to_bool,
    now_utc,
    optional_bool_to_int,
)


class TranscriptionStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self):
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transcription_sessions (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at_utc TEXT NOT NULL,
                    stopped_at_utc TEXT,
                    device_name TEXT,
                    model_name TEXT NOT NULL,
                    language TEXT NOT NULL,
                    display_connected INTEGER,
                    last_error TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transcription_segments (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sequence_no INTEGER NOT NULL,
                    started_at_utc TEXT NOT NULL,
                    ended_at_utc TEXT NOT NULL,
                    text TEXT NOT NULL,
                    oled_text TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES transcription_sessions(id)
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_transcription_segments_session_sequence
                ON transcription_segments (session_id, sequence_no)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transcription_sessions_started
                ON transcription_sessions (started_at_utc DESC)
                """
            )

    def _row_to_session(self, row):
        if row is None:
            return None
        return TranscriptionSessionRecord(
            id=row["id"],
            status=row["status"],
            started_at_utc=row["started_at_utc"],
            stopped_at_utc=row["stopped_at_utc"],
            device_name=row["device_name"],
            model_name=row["model_name"],
            language=row["language"],
            display_connected=int_to_bool(row["display_connected"]),
            last_error=row["last_error"],
        )

    def _row_to_segment(self, row):
        if row is None:
            return None
        return TranscriptionSegmentRecord(
            id=row["id"],
            session_id=row["session_id"],
            sequence_no=row["sequence_no"],
            started_at_utc=row["started_at_utc"],
            ended_at_utc=row["ended_at_utc"],
            text=row["text"],
            oled_text=row["oled_text"],
            created_at_utc=row["created_at_utc"],
        )

    def mark_interrupted_sessions(self, *, now=None):
        stopped_at = format_utc(now or now_utc())
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE transcription_sessions
                SET status = ?, stopped_at_utc = COALESCE(stopped_at_utc, ?), last_error = COALESCE(last_error, ?)
                WHERE status = ?
                """,
                (SESSION_STATUS_INTERRUPTED, stopped_at, "Application restarted before session stop.", SESSION_STATUS_RUNNING),
            )

    def create_session(self, *, device_name, model_name, language, display_connected=None, now=None):
        session = build_session_record(
            device_name=device_name,
            model_name=model_name,
            language=language,
            now=now,
        )
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO transcription_sessions (
                    id, status, started_at_utc, stopped_at_utc, device_name,
                    model_name, language, display_connected, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.status,
                    session.started_at_utc,
                    session.stopped_at_utc,
                    session.device_name,
                    session.model_name,
                    session.language,
                    optional_bool_to_int(display_connected),
                    session.last_error,
                ),
            )
        return self.get_session(session.id)

    def get_session(self, session_id):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM transcription_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return self._row_to_session(row)

    def list_sessions(self, *, limit=20):
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM transcription_sessions
                ORDER BY started_at_utc DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def update_session(
        self,
        session_id,
        *,
        status=None,
        display_connected=None,
        last_error=None,
        stopped_at=None,
    ):
        session = self.get_session(session_id)
        if session is None:
            raise TranscriptionNotFoundError(f"Session {session_id} was not found.")

        next_status = status or session.status
        next_display = session.display_connected if display_connected is None else display_connected
        next_last_error = last_error if last_error is not None else session.last_error
        next_stopped_at = stopped_at if stopped_at is not None else session.stopped_at_utc

        with self._connection() as connection:
            connection.execute(
                """
                UPDATE transcription_sessions
                SET status = ?, stopped_at_utc = ?, display_connected = ?, last_error = ?
                WHERE id = ?
                """,
                (
                    next_status,
                    next_stopped_at,
                    optional_bool_to_int(next_display),
                    next_last_error,
                    session_id,
                ),
            )
        return self.get_session(session_id)

    def mark_stopped(self, session_id, *, display_connected=None, last_error=None, now=None):
        return self.update_session(
            session_id,
            status=SESSION_STATUS_STOPPED,
            display_connected=display_connected,
            last_error=last_error,
            stopped_at=format_utc(now or now_utc()),
        )

    def mark_failed(self, session_id, *, last_error, display_connected=None, now=None):
        return self.update_session(
            session_id,
            status=SESSION_STATUS_FAILED,
            display_connected=display_connected,
            last_error=last_error,
            stopped_at=format_utc(now or now_utc()),
        )

    def mark_interrupted(self, session_id, *, last_error, display_connected=None, now=None):
        return self.update_session(
            session_id,
            status=SESSION_STATUS_INTERRUPTED,
            display_connected=display_connected,
            last_error=last_error,
            stopped_at=format_utc(now or now_utc()),
        )

    def create_segment(self, *, session_id, started_at, ended_at, text, oled_text, created_at=None):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) FROM transcription_segments WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            sequence_no = int(row[0]) + 1
            segment = build_segment_record(
                session_id=session_id,
                sequence_no=sequence_no,
                started_at=started_at,
                ended_at=ended_at,
                text=text,
                oled_text=oled_text,
                created_at=created_at,
            )
            connection.execute(
                """
                INSERT INTO transcription_segments (
                    id, session_id, sequence_no, started_at_utc, ended_at_utc,
                    text, oled_text, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment.id,
                    segment.session_id,
                    segment.sequence_no,
                    segment.started_at_utc,
                    segment.ended_at_utc,
                    segment.text,
                    segment.oled_text,
                    segment.created_at_utc,
                ),
            )
        return segment

    def list_segments(self, session_id):
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM transcription_segments
                WHERE session_id = ?
                ORDER BY sequence_no ASC
                """,
                (session_id,),
            ).fetchall()
        return [self._row_to_segment(row) for row in rows]

    def get_session_with_segments(self, session_id):
        session = self.get_session(session_id)
        if session is None:
            raise TranscriptionNotFoundError(f"Session {session_id} was not found.")
        return {
            "session": session,
            "segments": self.list_segments(session_id),
        }
