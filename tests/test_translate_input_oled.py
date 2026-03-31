import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import translate_input_oled


class TranslateInputOledTests(unittest.TestCase):
    def test_translate_and_display_local_renders_translation(self):
        oled = Mock()
        translator = Mock()
        translator.translate.return_value = "cat"

        translated = translate_input_oled.translate_and_display_local(oled, translator, "猫")

        self.assertEqual(translated, "cat")
        oled.display_text.assert_called_once_with("cat")

    def test_translate_and_display_local_rejects_non_japanese(self):
        with self.assertRaises(ValueError):
            translate_input_oled.translate_and_display_local(Mock(), Mock(), "cat")


class TranslateInputOledAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_remote_text_keeps_display_visible_after_success(self):
        oled = Mock()
        client = Mock()
        client.close = AsyncMock()

        with patch.object(translate_input_oled, "OLEDDisplay", return_value=oled), patch.object(
            translate_input_oled,
            "RemoteTranslationClient",
            return_value=client,
        ), patch.object(
            translate_input_oled,
            "translate_and_display_remote",
            AsyncMock(return_value="cat"),
        ):
            args = type(
                "Args",
                (),
                {
                    "rotate": True,
                    "dc_pin": None,
                    "rst_pin": None,
                    "remote_url": "ws://host:8765",
                    "token": "secret",
                    "connect_timeout": 5.0,
                    "text": "猫",
                },
            )()

            await translate_input_oled.run_remote(args)

        oled.close.assert_not_called()
        self.assertTrue(client.close.called)


if __name__ == "__main__":
    unittest.main()
