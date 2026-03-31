import unittest

from remote_protocol import parse_server_message
from translation_core import Image
from translation_server_ws import TranslationSession


class FakeTranslator:
    def __init__(self):
        self.calls = []

    def translate(self, text):
        self.calls.append(text)
        return {"猫": "cat", "犬": "dog"}.get(text, f"translated:{text}")


class FakeOcrEngine:
    def __init__(self, text="猫"):
        self.text = text

    def extract_text(self, image):
        return self.text


class TranslationSessionTests(unittest.TestCase):
    def test_handle_ocr_text_waits_for_stability(self):
        session = TranslationSession(
            translator=FakeTranslator(),
            ocr_engine=FakeOcrEngine(),
            history_size=5,
            stable_frames=3,
            max_image_bytes=1024,
        )

        first = parse_server_message(session.handle_ocr_text("req-1", "猫"))
        second = parse_server_message(session.handle_ocr_text("req-2", "猫"))
        third = parse_server_message(session.handle_ocr_text("req-3", "猫"))

        self.assertEqual(first["type"], "noop")
        self.assertEqual(second["type"], "noop")
        self.assertEqual(third["type"], "translation")
        self.assertEqual(third["translated_text"], "cat")

    def test_handle_ocr_text_deduplicates_repeated_translation(self):
        session = TranslationSession(
            translator=FakeTranslator(),
            ocr_engine=FakeOcrEngine(),
            history_size=3,
            stable_frames=1,
            max_image_bytes=1024,
        )

        first = parse_server_message(session.handle_ocr_text("req-1", "猫"))
        second = parse_server_message(session.handle_ocr_text("req-2", "猫"))

        self.assertEqual(first["type"], "translation")
        self.assertEqual(second["type"], "noop")
        self.assertEqual(second["reason"], "duplicate_translation")

    def test_handle_ocr_text_returns_noop_when_no_japanese(self):
        session = TranslationSession(
            translator=FakeTranslator(),
            ocr_engine=FakeOcrEngine(),
            history_size=3,
            stable_frames=1,
            max_image_bytes=1024,
        )

        response = parse_server_message(session.handle_ocr_text("req-1", "camera"))
        self.assertEqual(response["type"], "noop")
        self.assertEqual(response["reason"], "no_japanese")

    def test_handle_text_request_translates_japanese(self):
        session = TranslationSession(
            translator=FakeTranslator(),
            ocr_engine=FakeOcrEngine(),
            history_size=3,
            stable_frames=1,
            max_image_bytes=1024,
        )

        response = parse_server_message(session.handle_text_request("req-1", "猫"))
        self.assertEqual(response["type"], "translation")
        self.assertEqual(response["translated_text"], "cat")

    def test_handle_text_request_rejects_non_japanese_text(self):
        session = TranslationSession(
            translator=FakeTranslator(),
            ocr_engine=FakeOcrEngine(),
            history_size=3,
            stable_frames=1,
            max_image_bytes=1024,
        )

        response = parse_server_message(session.handle_text_request("req-1", "camera"))
        self.assertEqual(response["type"], "error")
        self.assertEqual(response["code"], "BAD_REQUEST")

    @unittest.skipUnless(Image is not None, "Pillow is required for frame payload tests.")
    def test_handle_frame_request_rejects_invalid_image_payload(self):
        session = TranslationSession(
            translator=FakeTranslator(),
            ocr_engine=FakeOcrEngine(),
            history_size=3,
            stable_frames=1,
            max_image_bytes=32,
        )

        response = parse_server_message(
            session.handle_frame_request(
                {
                    "request_id": "req-1",
                    "image_jpeg_base64": "not-base64",
                }
            )
        )
        self.assertEqual(response["type"], "error")
        self.assertEqual(response["code"], "BAD_IMAGE")


if __name__ == "__main__":
    unittest.main()
