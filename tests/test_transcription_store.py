import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from transcription_store import TranscriptionStore
from transcription_types import SESSION_STATUS_INTERRUPTED


UTC = timezone.utc


class TranscriptionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "transcription.db"
        self.store = TranscriptionStore(self.db_path)
        self.now = datetime(2026, 4, 1, 18, 0, tzinfo=UTC)

    def test_creates_schema_on_first_boot(self):
        connection = sqlite3.connect(self.db_path)
        self.addCleanup(connection.close)
        with connection:
            row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'transcription_sessions'"
            ).fetchone()

        self.assertEqual(row[0], "transcription_sessions")

    def test_session_and_segment_round_trip(self):
        session = self.store.create_session(
            device_name="Built-in Microphone",
            model_name="mlx-community/whisper-tiny.en",
            language="en",
            display_connected=True,
            now=self.now,
        )

        segment = self.store.create_segment(
            session_id=session.id,
            started_at=self.now,
            ended_at=self.now,
            text="hello world",
            oled_text="world",
            created_at=self.now,
        )

        detail = self.store.get_session_with_segments(session.id)

        self.assertEqual(detail["session"].id, session.id)
        self.assertEqual([item.id for item in detail["segments"]], [segment.id])
        self.assertEqual(detail["segments"][0].sequence_no, 1)

    def test_running_sessions_are_marked_interrupted_on_startup(self):
        session = self.store.create_session(
            device_name="Built-in Microphone",
            model_name="mlx-community/whisper-tiny.en",
            language="en",
            display_connected=False,
            now=self.now,
        )

        self.store.mark_interrupted_sessions(now=self.now)
        interrupted = self.store.get_session(session.id)

        self.assertEqual(interrupted.status, SESSION_STATUS_INTERRUPTED)
        self.assertIsNotNone(interrupted.stopped_at_utc)


if __name__ == "__main__":
    unittest.main()
