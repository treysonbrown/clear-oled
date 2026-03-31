import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from reminder_store import ReminderStore
from reminder_types import ReminderConflictError


UTC = timezone.utc


class ReminderStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "reminders.db"
        self.store = ReminderStore(self.db_path)
        self.now = datetime(2026, 3, 31, 18, 0, tzinfo=UTC)

    def test_creates_schema_on_first_boot(self):
        connection = sqlite3.connect(self.db_path)
        self.addCleanup(connection.close)
        with connection:
            row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'reminders'"
            ).fetchone()

        self.assertEqual(row[0], "reminders")

    def test_create_reminder_stores_utc_conversion_metadata(self):
        reminder = self.store.create_reminder(
            "Take medicine",
            "2026-03-31T20:30",
            "America/Denver",
            now=self.now,
        )

        self.assertEqual(reminder.scheduled_at_utc, "2026-04-01T02:30:00Z")
        self.assertEqual(reminder.timezone, "America/Denver")
        self.assertEqual(reminder.display_duration_seconds, 20)

    def test_list_reminders_returns_scheduled_items_in_order(self):
        first = self.store.create_reminder(
            "first",
            "2026-03-31T20:30",
            "America/Denver",
            now=self.now,
        )
        second = self.store.create_reminder(
            "second",
            "2026-03-31T20:45",
            "America/Denver",
            now=self.now,
        )

        reminders = self.store.list_reminders(status="scheduled")

        self.assertEqual([item.id for item in reminders], [first.id, second.id])

    def test_soft_cancel_hides_reminder_from_scheduled_query(self):
        reminder = self.store.create_reminder(
            "cancel me",
            "2026-03-31T20:30",
            "America/Denver",
            now=self.now,
        )

        canceled = self.store.cancel_reminder(reminder.id, now=self.now)
        scheduled = self.store.list_reminders(status="scheduled")

        self.assertEqual(canceled.status, "canceled")
        self.assertEqual(scheduled, [])

    def test_overdue_reminders_are_returned_by_due_query(self):
        reminder = self.store.create_reminder(
            "show now",
            "2026-03-31T20:30",
            "America/Denver",
            now=self.now,
        )

        due = self.store.get_due_reminders(
            now=datetime(2026, 4, 1, 2, 31, tzinfo=UTC),
        )

        self.assertEqual([item.id for item in due], [reminder.id])

    def test_editing_delivered_or_canceled_reminders_is_rejected(self):
        delivered = self.store.create_reminder(
            "done",
            "2026-03-31T20:30",
            "America/Denver",
            now=self.now,
        )
        canceled = self.store.create_reminder(
            "stop",
            "2026-03-31T20:45",
            "America/Denver",
            now=self.now,
        )

        self.store.mark_delivering(delivered.id, now=datetime(2026, 4, 1, 2, 31, tzinfo=UTC))
        self.store.mark_delivered(delivered.id, now=datetime(2026, 4, 1, 2, 32, tzinfo=UTC))
        self.store.cancel_reminder(canceled.id, now=self.now)

        with self.assertRaises(ReminderConflictError):
            self.store.update_reminder(
                delivered.id,
                message="new",
                scheduled_at_local="2026-04-01T21:00",
                timezone_name="America/Denver",
                now=datetime(2026, 4, 1, 2, 33, tzinfo=UTC),
            )

        with self.assertRaises(ReminderConflictError):
            self.store.update_reminder(
                canceled.id,
                message="new",
                scheduled_at_local="2026-04-01T21:00",
                timezone_name="America/Denver",
                now=datetime(2026, 3, 31, 18, 1, tzinfo=UTC),
            )


if __name__ == "__main__":
    unittest.main()
