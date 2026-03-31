#!/usr/bin/env python3

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from reminder_types import (
    DEFAULT_DISPLAY_DURATION_SECONDS,
    ReminderConflictError,
    ReminderNotFoundError,
    ReminderRecord,
    ReminderValidationError,
    STATUS_CANCELED,
    STATUS_DELIVERED,
    STATUS_DELIVERING,
    STATUS_SCHEDULED,
    build_reminder_record,
    format_utc,
    local_to_utc,
    normalize_message,
    now_utc,
    parse_utc,
)


class ReminderStore:
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
                CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY,
                    message TEXT NOT NULL,
                    scheduled_at_utc TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    scheduled_at_local TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    delivered_at_utc TEXT,
                    next_attempt_at_utc TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    display_duration_seconds INTEGER NOT NULL,
                    canceled_at_utc TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                ON reminders (status, scheduled_at_utc, next_attempt_at_utc, created_at_utc)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminders_status_updated
                ON reminders (status, updated_at_utc)
                """
            )

    def _row_to_record(self, row):
        if row is None:
            return None

        return ReminderRecord(
            id=row["id"],
            message=row["message"],
            scheduled_at_local=row["scheduled_at_local"],
            timezone=row["timezone"],
            scheduled_at_utc=row["scheduled_at_utc"],
            status=row["status"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
            delivered_at_utc=row["delivered_at_utc"],
            next_attempt_at_utc=row["next_attempt_at_utc"],
            attempt_count=row["attempt_count"],
            last_error=row["last_error"],
            display_duration_seconds=row["display_duration_seconds"],
            canceled_at_utc=row["canceled_at_utc"],
        )

    def get_reminder(self, reminder_id):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()
        return self._row_to_record(row)

    def create_reminder(
        self,
        message,
        scheduled_at_local,
        timezone_name,
        *,
        display_duration_seconds=DEFAULT_DISPLAY_DURATION_SECONDS,
        now=None,
    ):
        reminder = build_reminder_record(
            message,
            scheduled_at_local,
            timezone_name,
            display_duration_seconds=display_duration_seconds,
            now=now,
        )
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO reminders (
                    id,
                    message,
                    scheduled_at_utc,
                    timezone,
                    scheduled_at_local,
                    status,
                    created_at_utc,
                    updated_at_utc,
                    delivered_at_utc,
                    next_attempt_at_utc,
                    attempt_count,
                    last_error,
                    display_duration_seconds,
                    canceled_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reminder.id,
                    reminder.message,
                    reminder.scheduled_at_utc,
                    reminder.timezone,
                    reminder.scheduled_at_local,
                    reminder.status,
                    reminder.created_at_utc,
                    reminder.updated_at_utc,
                    reminder.delivered_at_utc,
                    reminder.next_attempt_at_utc,
                    reminder.attempt_count,
                    reminder.last_error,
                    reminder.display_duration_seconds,
                    reminder.canceled_at_utc,
                ),
            )
        return reminder

    def list_reminders(self, *, status=None, limit=50):
        if not isinstance(limit, int) or limit <= 0:
            raise ReminderValidationError("`limit` must be a positive integer.")

        params = []
        where_clauses = []
        if status:
            where_clauses.append("status = ?")
            params.append(status)

        where_sql = ""
        if where_clauses:
            where_sql = f"WHERE {' AND '.join(where_clauses)}"

        if status == STATUS_SCHEDULED:
            order_sql = "ORDER BY scheduled_at_utc ASC, created_at_utc ASC"
        elif status in {STATUS_DELIVERED, STATUS_CANCELED}:
            order_sql = "ORDER BY updated_at_utc DESC, created_at_utc DESC"
        else:
            order_sql = "ORDER BY scheduled_at_utc ASC, created_at_utc ASC"

        params.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM reminders {where_sql} {order_sql} LIMIT ?",
                params,
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_activity(self, *, limit=10):
        if not isinstance(limit, int) or limit <= 0:
            raise ReminderValidationError("`limit` must be a positive integer.")

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reminders
                WHERE status = ? OR attempt_count > 0 OR last_error IS NOT NULL
                ORDER BY updated_at_utc DESC, created_at_utc DESC
                LIMIT ?
                """,
                (STATUS_DELIVERED, limit),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_due_reminders(self, *, now=None, limit=100):
        if not isinstance(limit, int) or limit <= 0:
            raise ReminderValidationError("`limit` must be a positive integer.")

        due_before = format_utc(now or now_utc())
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reminders
                WHERE status = ?
                  AND scheduled_at_utc <= ?
                  AND (next_attempt_at_utc IS NULL OR next_attempt_at_utc <= ?)
                ORDER BY scheduled_at_utc ASC, created_at_utc ASC
                LIMIT ?
                """,
                (STATUS_SCHEDULED, due_before, due_before, limit),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def update_reminder(
        self,
        reminder_id,
        *,
        message,
        scheduled_at_local,
        timezone_name,
        now=None,
    ):
        existing = self.get_reminder(reminder_id)
        if existing is None:
            raise ReminderNotFoundError(f"Reminder {reminder_id} was not found.")

        reference_now = now or now_utc()
        if existing.status != STATUS_SCHEDULED or parse_utc(existing.scheduled_at_utc) <= reference_now:
            raise ReminderConflictError("Only future scheduled reminders can be edited.")

        updated_message = normalize_message(message)
        updated_scheduled_at_utc = local_to_utc(scheduled_at_local, timezone_name)
        if updated_scheduled_at_utc <= reference_now:
            raise ReminderConflictError("Only future scheduled reminders can be edited.")
        updated_at = format_utc(reference_now)

        with self._connection() as connection:
            connection.execute(
                """
                UPDATE reminders
                SET message = ?,
                    scheduled_at_utc = ?,
                    timezone = ?,
                    scheduled_at_local = ?,
                    updated_at_utc = ?,
                    delivered_at_utc = NULL,
                    next_attempt_at_utc = NULL,
                    attempt_count = 0,
                    last_error = NULL,
                    canceled_at_utc = NULL
                WHERE id = ?
                """,
                (
                    updated_message,
                    format_utc(updated_scheduled_at_utc),
                    timezone_name,
                    scheduled_at_local,
                    updated_at,
                    reminder_id,
                ),
            )
        return self.get_reminder(reminder_id)

    def cancel_reminder(self, reminder_id, *, now=None):
        existing = self.get_reminder(reminder_id)
        if existing is None:
            raise ReminderNotFoundError(f"Reminder {reminder_id} was not found.")
        if existing.status != STATUS_SCHEDULED:
            raise ReminderConflictError("Only scheduled reminders can be canceled.")

        canceled_at = format_utc(now or now_utc())
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE reminders
                SET status = ?,
                    updated_at_utc = ?,
                    canceled_at_utc = ?,
                    next_attempt_at_utc = NULL
                WHERE id = ?
                """,
                (STATUS_CANCELED, canceled_at, canceled_at, reminder_id),
            )
        return self.get_reminder(reminder_id)

    def mark_delivering(self, reminder_id, *, now=None):
        updated_at = format_utc(now or now_utc())
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE reminders
                SET status = ?,
                    updated_at_utc = ?
                WHERE id = ? AND status = ?
                """,
                (STATUS_DELIVERING, updated_at, reminder_id, STATUS_SCHEDULED),
            )
        return cursor.rowcount == 1

    def mark_delivered(self, reminder_id, *, now=None):
        delivered_at = format_utc(now or now_utc())
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE reminders
                SET status = ?,
                    updated_at_utc = ?,
                    delivered_at_utc = ?,
                    next_attempt_at_utc = NULL,
                    last_error = NULL
                WHERE id = ? AND status = ?
                """,
                (STATUS_DELIVERED, delivered_at, delivered_at, reminder_id, STATUS_DELIVERING),
            )
        return cursor.rowcount == 1

    def mark_retry(self, reminder_id, *, last_error, retry_at, now=None):
        updated_at = format_utc(now or now_utc())
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE reminders
                SET status = ?,
                    updated_at_utc = ?,
                    next_attempt_at_utc = ?,
                    attempt_count = attempt_count + 1,
                    last_error = ?
                WHERE id = ? AND status = ?
                """,
                (
                    STATUS_SCHEDULED,
                    updated_at,
                    format_utc(retry_at),
                    str(last_error),
                    reminder_id,
                    STATUS_DELIVERING,
                ),
            )
        return cursor.rowcount == 1
