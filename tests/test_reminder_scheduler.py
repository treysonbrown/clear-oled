import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reminder_scheduler import ReminderScheduler
from reminder_store import ReminderStore


UTC = timezone.utc


class FakeClock:
    def __init__(self, current):
        self.current = current

    def now(self):
        return self.current

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)


class FakeDisplayClient:
    sent_messages = []
    close_count = 0
    send_error = None

    def __init__(self, url, token, connect_timeout):
        self.url = url
        self.token = token
        self.connect_timeout = connect_timeout

    async def send_text(self, text):
        if self.send_error is not None:
            raise self.send_error
        self.sent_messages.append(text)

    async def close(self):
        type(self).close_count += 1


class ReminderSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup_tempdir)
        self.db_path = Path(self.tempdir.name) / "reminders.db"
        self.store = ReminderStore(self.db_path)
        self.clock = FakeClock(datetime(2026, 3, 31, 18, 0, tzinfo=UTC))
        self.sleeps = []
        FakeDisplayClient.sent_messages = []
        FakeDisplayClient.close_count = 0
        FakeDisplayClient.send_error = None

        async def fake_sleep(seconds):
            self.sleeps.append(seconds)

        self.scheduler = ReminderScheduler(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            default_display_seconds=20,
            display_client_factory=FakeDisplayClient,
            sleep=fake_sleep,
            now_fn=self.clock.now,
        )

    async def _cleanup_tempdir(self):
        self.tempdir.cleanup()

    def create_due_reminder(self, message, scheduled_at_local, *, created_at=None):
        return self.store.create_reminder(
            message,
            scheduled_at_local,
            "America/Denver",
            now=created_at or self.clock.now(),
        )

    async def test_due_reminder_is_delivered_once_and_marked_delivered(self):
        reminder = self.create_due_reminder("Take medicine", "2026-03-31T20:30")
        self.clock.current = datetime(2026, 4, 1, 2, 31, tzinfo=UTC)

        await self.scheduler.poll_due_reminders()
        delivered = await self.scheduler.deliver_next_reminder()
        stored = self.store.get_reminder(reminder.id)

        self.assertTrue(delivered)
        self.assertEqual(FakeDisplayClient.sent_messages, ["Take medicine"])
        self.assertEqual(FakeDisplayClient.close_count, 1)
        self.assertEqual(stored.status, "delivered")
        self.assertEqual(self.sleeps, [20])

    async def test_multiple_due_reminders_are_sent_fifo(self):
        self.create_due_reminder(
            "first",
            "2026-03-31T20:30",
            created_at=datetime(2026, 3, 31, 18, 0, tzinfo=UTC),
        )
        self.create_due_reminder(
            "second",
            "2026-03-31T20:30",
            created_at=datetime(2026, 3, 31, 18, 1, tzinfo=UTC),
        )
        self.clock.current = datetime(2026, 4, 1, 2, 31, tzinfo=UTC)

        await self.scheduler.poll_due_reminders()
        await self.scheduler.deliver_next_reminder()
        await self.scheduler.deliver_next_reminder()

        self.assertEqual(FakeDisplayClient.sent_messages, ["first", "second"])

    async def test_failure_records_error_and_schedules_retry(self):
        reminder = self.create_due_reminder("retry me", "2026-03-31T20:30")
        self.clock.current = datetime(2026, 4, 1, 2, 31, tzinfo=UTC)
        FakeDisplayClient.send_error = RuntimeError("Pi down")

        await self.scheduler.poll_due_reminders()
        delivered = await self.scheduler.deliver_next_reminder()
        stored = self.store.get_reminder(reminder.id)

        self.assertFalse(delivered)
        self.assertEqual(stored.status, "scheduled")
        self.assertEqual(stored.attempt_count, 1)
        self.assertEqual(stored.next_attempt_at_utc, "2026-04-01T02:31:15Z")
        self.assertIn("Pi down", stored.last_error)
        self.assertEqual(self.scheduler.pi_delivery_state, "retrying")

    async def test_overdue_reminders_after_restart_are_retried_immediately(self):
        reminder = self.create_due_reminder("late", "2026-03-31T20:30")
        self.clock.current = datetime(2026, 4, 1, 2, 45, tzinfo=UTC)

        scheduler = ReminderScheduler(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            default_display_seconds=20,
            display_client_factory=FakeDisplayClient,
            sleep=self.scheduler.sleep,
            now_fn=self.clock.now,
        )

        await scheduler.poll_due_reminders()
        await scheduler.deliver_next_reminder()
        stored = self.store.get_reminder(reminder.id)

        self.assertEqual(stored.status, "delivered")
        self.assertEqual(FakeDisplayClient.sent_messages, ["late"])

    async def test_duplicate_poll_cycles_do_not_queue_same_reminder_twice(self):
        self.create_due_reminder("once", "2026-03-31T20:30")
        self.clock.current = datetime(2026, 4, 1, 2, 31, tzinfo=UTC)

        await self.scheduler.poll_due_reminders()
        await self.scheduler.poll_due_reminders()

        self.assertEqual(self.scheduler.queue.qsize(), 1)

    async def test_delivery_closes_websocket_after_display_duration(self):
        self.create_due_reminder("close me", "2026-03-31T20:30")
        self.clock.current = datetime(2026, 4, 1, 2, 31, tzinfo=UTC)

        await self.scheduler.poll_due_reminders()
        await self.scheduler.deliver_next_reminder()

        self.assertEqual(FakeDisplayClient.close_count, 1)
        self.assertEqual(self.sleeps, [20])


if __name__ == "__main__":
    unittest.main()
