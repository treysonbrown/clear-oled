#!/usr/bin/env python3

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta

from display_client import DisplayUpdateClient
from reminder_types import now_utc


class ReminderScheduler:
    def __init__(
        self,
        *,
        store,
        display_url,
        token,
        default_display_seconds,
        display_client_factory=DisplayUpdateClient,
        poll_interval=1.0,
        retry_delay_seconds=15.0,
        sleep=asyncio.sleep,
        now_fn=now_utc,
    ):
        self.store = store
        self.display_url = display_url
        self.token = token
        self.default_display_seconds = default_display_seconds
        self.display_client_factory = display_client_factory
        self.poll_interval = poll_interval
        self.retry_delay_seconds = retry_delay_seconds
        self.sleep = sleep
        self.now_fn = now_fn
        self.queue = asyncio.Queue()
        self.queued_ids = set()
        self._stop_event = asyncio.Event()
        self._tasks = []
        self.pi_delivery_state = "idle"
        self.last_delivery_error = None

    def status_snapshot(self):
        return {
            "scheduler_now_utc": self.now_fn(),
            "pi_delivery_state": self.pi_delivery_state,
            "last_delivery_error": self.last_delivery_error,
            "display_duration_seconds_default": self.default_display_seconds,
        }

    async def start(self):
        if self._tasks:
            return

        self._stop_event.clear()
        self._tasks = [
            asyncio.create_task(self._poll_loop(), name="reminder-poll-loop"),
            asyncio.create_task(self._delivery_loop(), name="reminder-delivery-loop"),
        ]

    async def stop(self):
        self._stop_event.set()
        tasks = list(self._tasks)
        self._tasks = []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _poll_loop(self):
        while not self._stop_event.is_set():
            await self.poll_due_reminders()
            await self.sleep(self.poll_interval)

    async def _delivery_loop(self):
        while not self._stop_event.is_set():
            delivered = await self.deliver_next_reminder(wait=False)
            if not delivered:
                await self.sleep(min(self.poll_interval, 0.25))

    async def poll_due_reminders(self):
        due_reminders = self.store.get_due_reminders(now=self.now_fn())
        for reminder in due_reminders:
            if reminder.id in self.queued_ids:
                continue
            self.queued_ids.add(reminder.id)
            await self.queue.put(reminder.id)
        return due_reminders

    async def deliver_next_reminder(self, *, wait=False):
        try:
            reminder_id = await self.queue.get() if wait else self.queue.get_nowait()
        except asyncio.QueueEmpty:
            return False

        try:
            reminder = self.store.get_reminder(reminder_id)
            if reminder is None or reminder.status != "scheduled":
                return False

            if not self.store.mark_delivering(reminder_id, now=self.now_fn()):
                return False

            reminder = self.store.get_reminder(reminder_id)
            display_client = self.display_client_factory(
                url=self.display_url,
                token=self.token,
                connect_timeout=5.0,
            )
            try:
                self.pi_delivery_state = "delivering"
                self.last_delivery_error = None
                await display_client.send_text(reminder.message)
                await self.sleep(reminder.display_duration_seconds)
                self.store.mark_delivered(reminder_id, now=self.now_fn())
                self.pi_delivery_state = "idle"
                return True
            except Exception as exc:
                retry_at = self.now_fn() + timedelta(seconds=self.retry_delay_seconds)
                self.store.mark_retry(
                    reminder_id,
                    last_error=exc,
                    retry_at=retry_at,
                    now=self.now_fn(),
                )
                self.pi_delivery_state = "retrying"
                self.last_delivery_error = str(exc)
                return False
            finally:
                with suppress(Exception):
                    await display_client.close()
        finally:
            self.queued_ids.discard(reminder_id)
            self.queue.task_done()
