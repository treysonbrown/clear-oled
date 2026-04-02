import unittest
from unittest.mock import Mock, call

from display_client import DisplayUpdateClient, websockets as client_websockets
from display_protocol import (
    CLOSE_BAD_AUTH,
    build_auth_message,
    build_clear_message,
    build_display_text_message,
    parse_server_message,
)
from display_server_ws import DisplayServer, websockets as server_websockets


HAS_WEBSOCKETS = client_websockets is not None and server_websockets is not None


@unittest.skipUnless(HAS_WEBSOCKETS, "websockets is required for display server tests.")
class DisplayServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.oled = Mock()
        self.server = DisplayServer(
            oled=self.oled,
            token="secret",
            status_text="SERVER DOWN",
        )
        self.listener = await server_websockets.serve(self.server.handler, "127.0.0.1", 0)
        self.port = self.listener.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.listener.close()
        await self.listener.wait_closed()

    async def test_successful_auth_sends_auth_ok(self):
        async with client_websockets.connect(f"ws://127.0.0.1:{self.port}") as websocket:
            await websocket.send(build_auth_message("secret", "macbook"))
            response = parse_server_message(await websocket.recv())

        self.assertEqual(response["type"], "auth_ok")

    async def test_display_update_renders_text(self):
        async with client_websockets.connect(f"ws://127.0.0.1:{self.port}") as websocket:
            await websocket.send(build_auth_message("secret", "macbook"))
            await websocket.recv()
            await websocket.send(build_display_text_message("req-1", "cat"))
            response = parse_server_message(await websocket.recv())

        self.assertEqual(response["type"], "ack")
        self.assertIn(call("cat"), self.oled.display_text.call_args_list)

    async def test_duplicate_display_update_does_not_redraw(self):
        async with client_websockets.connect(f"ws://127.0.0.1:{self.port}") as websocket:
            await websocket.send(build_auth_message("secret", "macbook"))
            await websocket.recv()
            await websocket.send(build_display_text_message("req-1", "cat"))
            await websocket.recv()
            await websocket.send(build_display_text_message("req-2", "cat"))
            await websocket.recv()

        cat_calls = [item for item in self.oled.display_text.call_args_list if item == call("cat")]
        self.assertEqual(len(cat_calls), 1)

    async def test_clear_request_clears_oled(self):
        async with client_websockets.connect(f"ws://127.0.0.1:{self.port}") as websocket:
            await websocket.send(build_auth_message("secret", "macbook"))
            await websocket.recv()
            await websocket.send(build_display_text_message("req-1", "cat"))
            await websocket.recv()
            await websocket.send(build_clear_message("req-2"))
            response = parse_server_message(await websocket.recv())

        self.assertEqual(response["type"], "ack")
        self.assertTrue(self.oled.clear.called)

    async def test_disconnect_restores_status_text(self):
        async with client_websockets.connect(f"ws://127.0.0.1:{self.port}") as websocket:
            await websocket.send(build_auth_message("secret", "macbook"))
            await websocket.recv()

        self.assertIn(call("SERVER DOWN"), self.oled.display_text.call_args_list)

    async def test_bad_token_closes_connection(self):
        async with client_websockets.connect(f"ws://127.0.0.1:{self.port}") as websocket:
            await websocket.send(build_auth_message("wrong", "macbook"))

            with self.assertRaises(Exception) as context:
                await websocket.recv()

        self.assertEqual(getattr(context.exception, "code", None), CLOSE_BAD_AUTH)

    async def test_second_authenticated_client_is_rejected(self):
        first = await client_websockets.connect(f"ws://127.0.0.1:{self.port}")
        self.addAsyncCleanup(first.close)
        await first.send(build_auth_message("secret", "macbook-1"))
        await first.recv()

        async with client_websockets.connect(f"ws://127.0.0.1:{self.port}") as second:
            await second.send(build_auth_message("secret", "macbook-2"))
            with self.assertRaises(Exception) as context:
                await second.recv()

        self.assertEqual(getattr(context.exception, "code", None), 4400)


if __name__ == "__main__":
    unittest.main()
