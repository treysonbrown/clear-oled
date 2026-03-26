import unittest


from translate_camera_oled import StabilityGate, contains_japanese, normalize_text


try:
    from PIL import Image
    from translate_camera_oled import center_crop

    HAS_PIL = True
except ModuleNotFoundError:
    HAS_PIL = False


class TranslateCameraOledLogicTests(unittest.TestCase):
    def test_normalize_text_collapses_whitespace(self):
        self.assertEqual(normalize_text("  日本語   テスト  "), "日本語 テスト")

    def test_contains_japanese_detects_japanese_characters(self):
        self.assertTrue(contains_japanese("猫"))
        self.assertTrue(contains_japanese("カメラ"))
        self.assertFalse(contains_japanese("camera"))

    def test_stability_gate_requires_repeated_matches(self):
        gate = StabilityGate(history_size=5, min_stable=3)
        self.assertIsNone(gate.observe("猫"))
        self.assertIsNone(gate.observe("猫 "))
        self.assertEqual(gate.observe("猫"), "猫")
        self.assertIsNone(gate.observe("猫"))


@unittest.skipUnless(HAS_PIL, "Pillow is required for image crop tests.")
class TranslateCameraOledImageTests(unittest.TestCase):
    def test_center_crop_uses_middle_of_image(self):
        image = Image.new("RGB", (640, 480), "white")
        crop = center_crop(image, 320, 160)
        self.assertEqual(crop.size, (320, 160))


if __name__ == "__main__":
    unittest.main()
