import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from transcription_audio import FRAME_BYTES, PhraseEvent
from transcription_service import TranscriptionService
from transcription_store import TranscriptionStore


def build_frame(value):
    return bytes([value]) * FRAME_BYTES


UTC = timezone.utc


class FakeDetector:
    timestamp = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)

    def feed(self, frame_bytes):
        marker = frame_bytes[0]
        if marker == 1:
            return [PhraseEvent("phrase_started", self.timestamp, self.timestamp, b"")]
        if marker == 2:
            return [PhraseEvent("partial_ready", self.timestamp, self.timestamp, b"partial")]
        if marker == 3:
            return [PhraseEvent("phrase_finished", self.timestamp, self.timestamp, b"final")]
        return []

    def flush(self):
        return None


class FakeEngine:
    backend_name = "fake-whisper"
    model_name = "fake-tiny"

    def transcribe_pcm16(self, audio_bytes, *, sample_rate=16000):
        if audio_bytes == b"partial":
            return "hello world"
        if audio_bytes == b"final":
            return "hello world again"
        return ""


class FakeDisplayClient:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.sent = []

    async def _ensure_websocket(self):
        return True

    async def send_text(self, text):
        if self.fail:
            raise RuntimeError("display down")
        self.sent.append(text)
        return {"type": "ack"}

    async def close(self):
        return None


class FakeAudioInput:
    def __init__(self, payload):
        self.payload = payload

    def start(self, *, loop, queue):
        loop.call_soon(queue.put_nowait, self.payload)

    def stop(self):
        return None


class TranscriptionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "transcription.db"
        self.store = TranscriptionStore(self.db_path)

    async def collect_events(self, queue):
        messages = []
        while not queue.empty():
            messages.append(queue.get_nowait().decode("utf-8"))
        return messages

    async def test_duplicate_partial_text_is_not_resent(self):
        display_client = FakeDisplayClient()
        payload = build_frame(1) + build_frame(2) + build_frame(2) + build_frame(3)
        service = TranscriptionService(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            audio_input_factory=lambda device_id=None: FakeAudioInput(payload),
            detector_factory=lambda: FakeDetector(),
            engine_factory=lambda model_name=None: FakeEngine(),
            display_client_factory=lambda: display_client,
        )
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
        payload = build_frame(1) + build_frame(2) + build_frame(3)
        service = TranscriptionService(
            store=self.store,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            audio_input_factory=lambda device_id=None: FakeAudioInput(payload),
            detector_factory=lambda: FakeDetector(),
            engine_factory=lambda model_name=None: FakeEngine(),
            display_client_factory=lambda: display_client,
        )

        await service.start_session()
        await asyncio.sleep(0.05)
        await service.stop_session()

        session = self.store.list_sessions(limit=1)[0]
        detail = self.store.get_session_with_segments(session.id)

        self.assertEqual(len(detail["segments"]), 1)
        self.assertFalse(session.display_connected)
        self.assertEqual(service.status_snapshot()["display_state"], "disconnected")


if __name__ == "__main__":
    unittest.main()
