import unittest
from unittest.mock import patch

import translate_macbook_camera_oled
from translate_macbook_camera_oled import (
    StabilityGate,
    contains_meaningful_japanese,
    process_frame,
    validate_args,
)


try:
    from PIL import Image

    HAS_PIL = True
except ModuleNotFoundError:
    HAS_PIL = False


class FakeOcrEngine:
    def __init__(self, text):
        self.text = text

    def extract_text(self, image):
        return self.text


class FakeTranslator:
    def __init__(self, translated):
        self.translated = translated

    def translate(self, text):
        return self.translated


class TranslateMacbookCameraOledLogicTests(unittest.TestCase):
    def test_validate_args_requires_display_url_and_token(self):
        args = type(
            "Args",
            (),
            {
                "display_url": None,
                "token": None,
                "stable_frames": 3,
                "history_size": 5,
            },
        )()

        with self.assertRaises(ValueError):
            validate_args(args)

    def test_validate_args_rejects_stable_frames_greater_than_history(self):
        args = type(
            "Args",
            (),
            {
                "display_url": "ws://127.0.0.1:8766",
                "token": "secret",
                "stable_frames": 6,
                "history_size": 5,
            },
        )()

        with self.assertRaises(ValueError):
            validate_args(args)

    def test_ensure_opencv_raises_clear_error_when_missing(self):
        with patch.object(translate_macbook_camera_oled, "cv2", None):
            with self.assertRaisesRegex(RuntimeError, "opencv-python"):
                translate_macbook_camera_oled.ensure_opencv()

    def test_contains_meaningful_japanese_rejects_prolonged_sound_mark_only(self):
        self.assertFalse(contains_meaningful_japanese("ー"))
        self.assertTrue(contains_meaningful_japanese("猫"))


@unittest.skipUnless(HAS_PIL, "Pillow is required for image tests.")
class TranslateMacbookCameraOledImageTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new("RGB", (640, 480), "white")

    def test_process_frame_translates_stable_japanese_text(self):
        gate = StabilityGate(history_size=3, min_stable=1)

        translated, last_translation = process_frame(
            image=self.image,
            ocr_engine=FakeOcrEngine("猫"),
            translator=FakeTranslator("cat"),
            gate=gate,
            crop_width=320,
            crop_height=160,
            last_translation=None,
        )

        self.assertEqual(translated, "cat")
        self.assertEqual(last_translation, "cat")

    def test_process_frame_skips_non_japanese_text(self):
        gate = StabilityGate(history_size=3, min_stable=1)

        translated, last_translation = process_frame(
            image=self.image,
            ocr_engine=FakeOcrEngine("camera"),
            translator=FakeTranslator("camera"),
            gate=gate,
            crop_width=320,
            crop_height=160,
            last_translation=None,
        )

        self.assertIsNone(translated)
        self.assertIsNone(last_translation)

    def test_process_frame_does_not_resend_duplicate_translation(self):
        gate = StabilityGate(history_size=3, min_stable=1)

        translated, last_translation = process_frame(
            image=self.image,
            ocr_engine=FakeOcrEngine("猫"),
            translator=FakeTranslator("cat"),
            gate=gate,
            crop_width=320,
            crop_height=160,
            last_translation="cat",
        )

        self.assertIsNone(translated)
        self.assertEqual(last_translation, "cat")

    def test_process_frame_skips_non_meaningful_japanese_text(self):
        gate = StabilityGate(history_size=3, min_stable=1)

        translated, last_translation = process_frame(
            image=self.image,
            ocr_engine=FakeOcrEngine("ー"),
            translator=FakeTranslator(""),
            gate=gate,
            crop_width=320,
            crop_height=160,
            last_translation=None,
        )

        self.assertIsNone(translated)
        self.assertIsNone(last_translation)


if __name__ == "__main__":
    unittest.main()
