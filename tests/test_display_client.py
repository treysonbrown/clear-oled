import unittest
from unittest.mock import AsyncMock, patch

import display_client
from display_client import (
    DisplayAuthenticationError,
    DisplayProtocolError,
    DisplayUpdateClient,
)


class FakeCloseError(Exception):
    def __init__(self, code):
        super().__init__(f"closed: {code}")
        self.code = code


class DisplayClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_auth_raises_display_authentication_error(self):
        with patch.object(
            display_client,
            "websockets",
            type("Websockets", (), {"connect": AsyncMock(side_effect=FakeCloseError(4401))})(),
        ):
            client = DisplayUpdateClient(url="ws://127.0.0.1:8766", token="secret", connect_timeout=1.0)

            with self.assertRaises(DisplayAuthenticationError):
                await client.send_text("cat")

    async def test_invalid_auth_response_raises_protocol_error(self):
        websocket = AsyncMock()
        websocket.recv = AsyncMock(return_value='{"type":"bogus"}')

        with patch.object(
            display_client,
            "websockets",
            type("Websockets", (), {"connect": AsyncMock(return_value=websocket)})(),
        ):
            client = DisplayUpdateClient(url="ws://127.0.0.1:8766", token="secret", connect_timeout=1.0)

            with self.assertRaises(DisplayProtocolError):
                await client.send_text("cat")


if __name__ == "__main__":
    unittest.main()
