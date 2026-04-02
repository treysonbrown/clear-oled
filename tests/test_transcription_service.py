import asyncio
import tempfile
import unittest
from pathlib import Path

from transcription_engine_macos import MacOSSpeechPermissionError
from transcription_service import TranscriptionService
from transcription_store import TranscriptionStore


class FakeSpeechSession:
    backend_name = "macos-speech"
    model_name = "macos-speech"

    def __init__(self, events=None, *, start_error=None):
        self.events = list(events or [])
        self.start_error = start_error
        self.stopped = False
        self.started_device_id = None

    @property
    def last_error(self):
        return None

    async def start(self, *, device_id=None):
        if self.start_error is not None:
            raise self.start_error
        self.started_device_id = device_id

    async def read_event(self):
        if self.events:
            event = self.events.pop(0)
            if isinstance(event, tuple):
                delay, payload = event
                await asyncio.sleep(delay)
                return payload
            return event
        while not self.stopped:
            await asyncio.sleep(0.01)
        return None

    async def stop(self):
        self.stopped = True


class FakeDisplayClient:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.sent = []
        self.clear_calls = 0

    async def _ensure_websocket(self):
        return True

    async def send_text(self, text):
        if self.fail:
            raise RuntimeError("display down")
        self.sent.append(text)
        return {"type": "ack"}

    async def clear(self):
        if self.fail:
            raise RuntimeError("display down")
        self.clear_calls += 1
        return {"type": "ack"}

    async def close(self):
        return None


class TranscriptionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "transcription.db"
        self.store = TranscriptionStore(self.db_path)
        self.services = []

    async def asyncTearDown(self):
        for service in self.services:
            try:
                await service.stop_session()
            except Exception:
                pass

    async def collect_events(self, queue):
        messages = []
        while not queue.empty():
            messages.append(queue.get_nowait().decode("utf-8"))
        return messages

    async def test_duplicate_partial_text_is_not_resent(self):
        display_client = FakeDisplayClient()
        helper = FakeSpeechSession(
            events=[
                {"type": "partial", "text": "hello world"},
                {"type": "partial", "text": "hello world"},
                {"type": "final", "text": "hello world again"},
            ]
        )
        service = TranscriptionService(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            speech_session_factory=lambda: helper,
            display_client_factory=lambda: display_client,
        )
        self.services.append(service)
        queue = await service.broadcaster.subscribe()

        await service.start_session()
        await asyncio.sleep(0.05)
        await service.stop_session()

        detail = self.store.get_session_with_segments(self.store.list_sessions(limit=1)[0].id)
        messages = await self.collect_events(queue)

        self.assertEqual(display_client.sent, ["hello world", "hello world again"])
        self.assertEqual(len(detail["segments"]), 1)
        self.assertIn("event: partial", "\n".join(messages))
        self.assertIn("event: final_segment", "\n".join(messages))

    async def test_transcription_continues_when_display_updates_fail(self):
        display_client = FakeDisplayClient(fail=True)
        helper = FakeSpeechSession(
            events=[
                {"type": "partial", "text": "hello world"},
                {"type": "final", "text": "hello world again"},
            ]
        )
        service = TranscriptionService(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            speech_session_factory=lambda: helper,
            display_client_factory=lambda: display_client,
        )
        self.services.append(service)

        await service.start_session()
        await asyncio.sleep(0.05)
        self.assertEqual(service.status_snapshot()["service_state"], "running")
        await service.stop_session()

        session = self.store.list_sessions(limit=1)[0]
        detail = self.store.get_session_with_segments(session.id)

        self.assertEqual(len(detail["segments"]), 1)
        self.assertFalse(session.display_connected)
        self.assertEqual(service.status_snapshot()["display_state"], "disconnected")

    async def test_helper_crash_sets_error_and_returns_to_idle(self):
        helper = FakeSpeechSession(events=[{"type": "error", "code": "runtime_error", "message": "speech crashed"}])
        service = TranscriptionService(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            speech_session_factory=lambda: helper,
            display_client_factory=lambda: FakeDisplayClient(),
        )
        self.services.append(service)

        await service.start_session()
        await asyncio.sleep(0.05)

        snapshot = service.status_snapshot()
        self.assertEqual(snapshot["service_state"], "idle")
        self.assertEqual(snapshot["mic_state"], "error")
        self.assertEqual(snapshot["last_error"], "speech crashed")

    async def test_permission_denied_during_start_sets_permission_state(self):
        service = TranscriptionService(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            speech_session_factory=lambda: FakeSpeechSession(
                start_error=MacOSSpeechPermissionError("Speech recognition access was denied.")
            ),
            display_client_factory=lambda: FakeDisplayClient(),
        )
        self.services.append(service)

        with self.assertRaises(MacOSSpeechPermissionError):
            await service.start_session()

        snapshot = service.status_snapshot()
        self.assertEqual(snapshot["service_state"], "idle")
        self.assertEqual(snapshot["mic_state"], "permission_denied")
        self.assertIn("denied", snapshot["last_error"])

    async def test_silence_timeout_clears_display(self):
        display_client = FakeDisplayClient()
        helper = FakeSpeechSession(events=[{"type": "partial", "text": "hello world"}])
        service = TranscriptionService(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            speech_session_factory=lambda: helper,
            display_client_factory=lambda: display_client,
            clear_after_silence_seconds=0.01,
        )
        self.services.append(service)

        await service.start_session()
        await asyncio.sleep(0.05)

        snapshot = service.status_snapshot()
        self.assertEqual(display_client.sent, ["hello world"])
        self.assertEqual(display_client.clear_calls, 1)
        self.assertEqual(snapshot["current_oled_text"], "")
        self.assertEqual(snapshot["current_partial"], "")

        await service.stop_session()

    async def test_new_partial_strips_recently_cleared_prefix(self):
        display_client = FakeDisplayClient()
        helper = FakeSpeechSession(
            events=[
                {"type": "partial", "text": "hello world"},
                (0.05, {"type": "partial", "text": "hello world again"}),
            ]
        )
        service = TranscriptionService(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            speech_session_factory=lambda: helper,
            display_client_factory=lambda: display_client,
            clear_after_silence_seconds=0.01,
        )
        self.services.append(service)

        await service.start_session()
        await asyncio.sleep(0.12)

        self.assertEqual(display_client.sent, ["hello world", "again"])
        self.assertGreaterEqual(display_client.clear_calls, 1)

    async def test_carryover_only_partials_stay_blank_after_timeout(self):
        display_client = FakeDisplayClient()
        helper = FakeSpeechSession(
            events=[
                {"type": "partial", "text": "hello world"},
                (0.05, {"type": "partial", "text": "hello"}),
                (0.01, {"type": "partial", "text": "hello world"}),
            ]
        )
        service = TranscriptionService(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            speech_session_factory=lambda: helper,
            display_client_factory=lambda: display_client,
            clear_after_silence_seconds=0.02,
        )
        self.services.append(service)

        await service.start_session()
        await asyncio.sleep(0.14)

        self.assertEqual(display_client.sent, ["hello world"])
        self.assertGreaterEqual(display_client.clear_calls, 1)
        self.assertEqual(service.status_snapshot()["current_oled_text"], "")

    async def test_cumulative_partials_keep_old_words_suppressed_after_timeout(self):
        display_client = FakeDisplayClient()
        helper = FakeSpeechSession(
            events=[
                {"type": "partial", "text": "hello world"},
                (0.05, {"type": "partial", "text": "hello world again"}),
                (0.01, {"type": "partial", "text": "hello world again there"}),
            ]
        )
        service = TranscriptionService(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            speech_session_factory=lambda: helper,
            display_client_factory=lambda: display_client,
            clear_after_silence_seconds=0.02,
        )
        self.services.append(service)

        await service.start_session()
        await asyncio.sleep(0.14)

        self.assertEqual(display_client.sent, ["hello world", "again", "again there"])

    async def test_final_segment_keeps_raw_text_and_trims_oled_text_after_timeout(self):
        display_client = FakeDisplayClient()
        helper = FakeSpeechSession(
            events=[
                {"type": "partial", "text": "hello world"},
                (0.05, {"type": "final", "text": "hello world again"}),
            ]
        )
        service = TranscriptionService(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            speech_session_factory=lambda: helper,
            display_client_factory=lambda: display_client,
            clear_after_silence_seconds=0.02,
        )
        self.services.append(service)

        await service.start_session()
        await asyncio.sleep(0.12)
        await service.stop_session()

        session = self.store.list_sessions(limit=1)[0]
        detail = self.store.get_session_with_segments(session.id)

        self.assertEqual(display_client.sent, ["hello world", "again"])
        self.assertEqual(len(detail["segments"]), 1)
        self.assertEqual(detail["segments"][0].text, "hello world again")
        self.assertEqual(detail["segments"][0].oled_text, "again")

    async def test_non_overlapping_partial_clears_post_timeout_suppression(self):
        display_client = FakeDisplayClient()
        helper = FakeSpeechSession(
            events=[
                {"type": "partial", "text": "hello world"},
                (0.05, {"type": "partial", "text": "good morning"}),
                (0.01, {"type": "partial", "text": "good morning everyone"}),
            ]
        )
        service = TranscriptionService(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            speech_session_factory=lambda: helper,
            display_client_factory=lambda: display_client,
            clear_after_silence_seconds=0.02,
        )
        self.services.append(service)

        await service.start_session()
        await asyncio.sleep(0.14)

        self.assertEqual(display_client.sent, ["hello world", "good morning", "good morning everyone"])

    async def test_carryover_only_speech_resets_silence_timer(self):
        display_client = FakeDisplayClient()
        helper = FakeSpeechSession(
            events=[
                {"type": "partial", "text": "hello world"},
                (0.05, {"type": "partial", "text": "hello"}),
                (0.015, {"type": "partial", "text": "hello world"}),
                (0.015, {"type": "partial", "text": "hello world again"}),
            ]
        )
        service = TranscriptionService(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            speech_session_factory=lambda: helper,
            display_client_factory=lambda: display_client,
            clear_after_silence_seconds=0.03,
        )
        self.services.append(service)

        await service.start_session()
        await asyncio.sleep(0.095)

        self.assertEqual(display_client.sent, ["hello world", "again"])
        self.assertEqual(display_client.clear_calls, 1)


if __name__ == "__main__":
    unittest.main()
