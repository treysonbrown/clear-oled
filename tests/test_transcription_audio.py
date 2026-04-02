import unittest
from datetime import datetime, timedelta, timezone

from transcription_audio import AudioPhraseDetector, FRAME_BYTES


UTC = timezone.utc


class FakeVad:
    def is_speech(self, frame_bytes, sample_rate):
        return frame_bytes[:1] == b"\x01"


def build_frame(is_speech):
    value = b"\x01" if is_speech else b"\x00"
    return value * FRAME_BYTES


class AudioPhraseDetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = AudioPhraseDetector(
            start_voiced_frames=2,
            end_silence_frames=3,
            partial_interval_frames=2,
            vad=FakeVad(),
        )
        self.current_time = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)

    def feed(self, is_speech):
        self.current_time += timedelta(milliseconds=30)
        return self.detector.feed(build_frame(is_speech), self.current_time)

    def test_phrase_starts_after_required_voice_frames(self):
        events = []
        events.extend(self.feed(True))
        events.extend(self.feed(True))

        self.assertEqual([event.type for event in events], ["phrase_started"])

    def test_partial_and_final_events_fire_in_order(self):
        events = []
        events.extend(self.feed(True))
        events.extend(self.feed(True))
        events.extend(self.feed(True))
        events.extend(self.feed(True))
        events.extend(self.feed(False))
        events.extend(self.feed(False))
        events.extend(self.feed(False))

        self.assertEqual(
            [event.type for event in events],
            ["phrase_started", "partial_ready", "partial_ready", "phrase_finished"],
        )
        self.assertGreater(len(events[-1].audio_bytes), 0)


if __name__ == "__main__":
    unittest.main()
