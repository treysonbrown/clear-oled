import unittest


try:
    import oled_display

    HEIGHT = oled_display.HEIGHT
    WIDTH = oled_display.WIDTH
    render_text_image = oled_display.render_text_image
    wrap_text = oled_display.wrap_text
    HAS_PIL = oled_display.Image is not None
except ModuleNotFoundError:
    HAS_PIL = False


@unittest.skipUnless(HAS_PIL, "Pillow is required for OLED layout tests.")
class OledDisplayLayoutTests(unittest.TestCase):
    def test_render_text_image_matches_panel_size(self):
        image = render_text_image("hello world", rotate=False)
        self.assertEqual(image.size, (WIDTH, HEIGHT))

    def test_wrap_text_breaks_long_sentences_across_lines(self):
        lines = wrap_text("this is a longer sentence that should wrap on the tiny oled screen")
        self.assertGreater(len(lines), 1)
        self.assertLessEqual(len(lines), 6)

    def test_wrap_text_truncates_when_screen_is_full(self):
        text = " ".join(["translation"] * 30)
        lines = wrap_text(text)
        self.assertTrue(lines)
        self.assertLessEqual(len(lines), 6)
        self.assertTrue(lines[-1].endswith("...") or len(lines) < 6)


if __name__ == "__main__":
    unittest.main()
