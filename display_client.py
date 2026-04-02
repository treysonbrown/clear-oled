#!/usr/bin/env python3

import socket
import uuid

from display_protocol import (
    CLOSE_BAD_AUTH,
    CLOSE_BAD_REQUEST,
    MessageValidationError,
    build_auth_message,
    build_clear_message,
    build_display_text_message,
    parse_server_message,
)

try:
    import websockets
except ModuleNotFoundError:
    websockets = None


class DisplayConnectionError(RuntimeError):
    pass


class DisplayAuthenticationError(DisplayConnectionError):
    pass


class DisplayProtocolError(RuntimeError):
    pass


class DisplayUpdateClient:
    def __init__(
        self,
        url,
        token,
        client_id=None,
        device="clear-oled-mac-camera",
        connect_timeout=5.0,
        debug=False,
        logger=None,
    ):
        if not url:
            raise ValueError("A display WebSocket URL is required.")
        if not token:
            raise ValueError("A shared token is required for display updates.")

        self.url = url
        self.token = token
        self.client_id = client_id or socket.gethostname()
        self.device = device
        self.connect_timeout = connect_timeout
        self.debug = debug
        self.logger = logger
        self.websocket = None

    def _log(self, message):
        if self.debug and self.logger:
            self.logger(message)

    async def close(self):
        if self.websocket is None:
            return

        try:
            await self.websocket.close()
        finally:
            self.websocket = None

    async def _ensure_websocket(self):
        if websockets is None:
            raise RuntimeError(
                "The `websockets` package is required for display updates. "
                "Install it with `python3 -m pip install websockets`."
            )

        if self.websocket is not None:
            return

        try:
            self.websocket = await websockets.connect(self.url, open_timeout=self.connect_timeout)
            await self.websocket.send(build_auth_message(self.token, self.client_id, self.device))
            raw_response = await self.websocket.recv()
            response = parse_server_message(raw_response)
        except MessageValidationError as exc:
            await self.close()
            raise DisplayProtocolError(f"Server returned an invalid auth response: {exc}") from exc
        except Exception as exc:
            code = getattr(exc, "code", None)
            await self.close()
            if code == CLOSE_BAD_AUTH:
                raise DisplayAuthenticationError("Display server rejected the token.") from exc
            if code == CLOSE_BAD_REQUEST:
                raise DisplayProtocolError("Display server rejected the auth payload.") from exc
            raise DisplayConnectionError(f"Unable to connect to display server: {exc}") from exc

        if response["type"] != "auth_ok":
            await self.close()
            raise DisplayProtocolError(f"Expected auth_ok, received {response['type']}.")

        self._log(f"Connected to display server with session {response['session_id']}")

    async def send_text(self, text):
        await self._ensure_websocket()
        payload = build_display_text_message(str(uuid.uuid4()), text)
        return await self._send_payload(payload)

    async def clear(self):
        await self._ensure_websocket()
        payload = build_clear_message(str(uuid.uuid4()))
        return await self._send_payload(payload)

    async def _send_payload(self, payload):
        try:
            await self.websocket.send(payload)
            raw_response = await self.websocket.recv()
        except Exception as exc:
            code = getattr(exc, "code", None)
            await self.close()
            if code == CLOSE_BAD_AUTH:
                raise DisplayAuthenticationError("Display server rejected the token.") from exc
            if code == CLOSE_BAD_REQUEST:
                raise DisplayProtocolError("Display server rejected the request payload.") from exc
            raise DisplayConnectionError(f"Display update failed: {exc}") from exc

        try:
            response = parse_server_message(raw_response)
        except MessageValidationError as exc:
            await self.close()
            raise DisplayProtocolError(f"Server returned an invalid response: {exc}") from exc

        if response["type"] == "error":
            raise DisplayProtocolError(f"{response['code']}: {response['message']}")

        if response["type"] != "ack":
            raise DisplayProtocolError(f"Expected ack, received {response['type']}.")

        return response
