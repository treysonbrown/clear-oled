import unittest

from oled_display import fit_transcript_tail_text


class TranscriptionOledFormattingTests(unittest.TestCase):
    def test_prefers_tail_words_when_phrase_is_too_long(self):
        fitted = fit_transcript_tail_text(
            "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
        )

        self.assertTrue(fitted.endswith("thirteen fourteen"))
        self.assertNotEqual(
            fitted,
            "one two three four five six seven eight nine ten eleven twelve thirteen fourteen",
        )


if __name__ == "__main__":
    unittest.main()
