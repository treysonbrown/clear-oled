import unittest

from remote_client import RemoteAuthenticationError, RemoteTranslationClient, websockets as client_websockets
from translation_server_ws import TranslationServer, websockets as server_websockets


class FakeTranslator:
    def translate(self, text):
        return {"猫": "cat"}.get(text, text)


class FakeOcrEngine:
    def extract_text(self, image):
        return "猫"


HAS_WEBSOCKETS = client_websockets is not None and server_websockets is not None


@unittest.skipUnless(HAS_WEBSOCKETS, "websockets is required for remote integration tests.")
class RemoteRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_round_trip_succeeds(self):
        server = TranslationServer(
            translator=FakeTranslator(),
            ocr_engine=FakeOcrEngine(),
            token="secret",
            history_size=3,
            stable_frames=1,
            max_image_bytes=1024,
        )

        async with server_websockets.serve(server.handler, "127.0.0.1", 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            client = RemoteTranslationClient(
                url=f"ws://127.0.0.1:{port}",
                token="secret",
                connect_timeout=1.0,
            )

            try:
                response = await client.send_text("猫")
            finally:
                await client.close()

        self.assertEqual(response["type"], "translation")
        self.assertEqual(response["translated_text"], "cat")

    async def test_bad_auth_raises_remote_authentication_error(self):
        server = TranslationServer(
            translator=FakeTranslator(),
            ocr_engine=FakeOcrEngine(),
            token="secret",
            history_size=3,
            stable_frames=1,
            max_image_bytes=1024,
        )

        async with server_websockets.serve(server.handler, "127.0.0.1", 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            client = RemoteTranslationClient(
                url=f"ws://127.0.0.1:{port}",
                token="wrong",
                connect_timeout=1.0,
            )

            try:
                with self.assertRaises(RemoteAuthenticationError):
                    await client.send_text("猫")
            finally:
                await client.close()


if __name__ == "__main__":
    unittest.main()
