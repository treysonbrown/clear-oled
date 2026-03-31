import asyncio
import unittest
from unittest.mock import Mock, call

from display_client import DisplayUpdateClient, websockets as client_websockets
from display_server_ws import DisplayServer, websockets as server_websockets


HAS_WEBSOCKETS = client_websockets is not None and server_websockets is not None


@unittest.skipUnless(HAS_WEBSOCKETS, "websockets is required for display integration tests.")
class DisplayRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def test_display_text_round_trip_succeeds(self):
        oled = Mock()
        server = DisplayServer(
            oled=oled,
            token="secret",
            status_text="SERVER DOWN",
        )

        async with server_websockets.serve(server.handler, "127.0.0.1", 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            client = DisplayUpdateClient(
                url=f"ws://127.0.0.1:{port}",
                token="secret",
                connect_timeout=1.0,
            )

            try:
                response = await client.send_text("cat")
            finally:
                await client.close()

            await asyncio.sleep(0)

        self.assertEqual(response["type"], "ack")
        self.assertIn(call("cat"), oled.display_text.call_args_list)
        self.assertIn(call("SERVER DOWN"), oled.display_text.call_args_list)


if __name__ == "__main__":
    unittest.main()
