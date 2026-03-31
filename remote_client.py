#!/usr/bin/env python3

import socket
import uuid
from datetime import datetime, timezone

from remote_protocol import (
    CLOSE_BAD_AUTH,
    CLOSE_BAD_REQUEST,
    MessageValidationError,
    build_auth_message,
    build_frame_message,
    build_text_message,
    parse_server_message,
)
from translation_core import encode_image_as_base64_jpeg

try:
    import websockets
except ModuleNotFoundError:
    websockets = None


class RemoteConnectionError(RuntimeError):
    pass


class RemoteAuthenticationError(RemoteConnectionError):
    pass


class RemoteProtocolError(RuntimeError):
    pass


class RemoteTranslationClient:
    def __init__(
        self,
        url,
        token,
        client_id=None,
        device="clear-oled",
        connect_timeout=5.0,
        debug=False,
        logger=None,
    ):
        if not url:
            raise ValueError("A remote WebSocket URL is required.")
        if not token:
            raise ValueError("A shared token is required for remote mode.")

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
                "The `websockets` package is required for remote mode. "
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
            raise RemoteProtocolError(f"Server returned an invalid auth response: {exc}") from exc
        except Exception as exc:
            code = getattr(exc, "code", None)
            await self.close()
            if code == CLOSE_BAD_AUTH:
                raise RemoteAuthenticationError("Remote server rejected the token.") from exc
            if code == CLOSE_BAD_REQUEST:
                raise RemoteProtocolError("Remote server rejected the auth payload.") from exc
            raise RemoteConnectionError(f"Unable to connect to remote server: {exc}") from exc

        if response["type"] != "auth_ok":
            await self.close()
            raise RemoteProtocolError(f"Expected auth_ok, received {response['type']}.")

        self._log(f"Connected to remote server with session {response['session_id']}")

    async def _send_and_receive(self, payload):
        await self._ensure_websocket()

        try:
            await self.websocket.send(payload)
            raw_response = await self.websocket.recv()
        except Exception as exc:
            code = getattr(exc, "code", None)
            await self.close()
            if code == CLOSE_BAD_AUTH:
                raise RemoteAuthenticationError("Remote server rejected the token.") from exc
            if code == CLOSE_BAD_REQUEST:
                raise RemoteProtocolError("Remote server rejected the request payload.") from exc
            raise RemoteConnectionError(f"Remote request failed: {exc}") from exc

        try:
            return parse_server_message(raw_response)
        except MessageValidationError as exc:
            await self.close()
            raise RemoteProtocolError(f"Server returned an invalid response: {exc}") from exc

    async def send_frame(
        self,
        image,
        source_width,
        source_height,
        crop_width,
        crop_height,
        ocr_lang,
        ocr_psm,
    ):
        request_id = str(uuid.uuid4())
        payload = build_frame_message(
            request_id=request_id,
            captured_at=datetime.now(timezone.utc).isoformat(),
            image_jpeg_base64=encode_image_as_base64_jpeg(image),
            source_width=source_width,
            source_height=source_height,
            crop_width=crop_width,
            crop_height=crop_height,
            ocr_lang=ocr_lang,
            ocr_psm=str(ocr_psm),
        )
        return await self._send_and_receive(payload)

    async def send_text(self, text):
        request_id = str(uuid.uuid4())
        payload = build_text_message(request_id=request_id, text=text)
        return await self._send_and_receive(payload)
