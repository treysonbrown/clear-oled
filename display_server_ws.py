#!/usr/bin/env python3

import argparse
import asyncio
import sys
import uuid

from display_protocol import (
    CLOSE_BAD_AUTH,
    CLOSE_BAD_REQUEST,
    CLOSE_INTERNAL_ERROR,
    MessageValidationError,
    build_ack_message,
    build_auth_ok_message,
    parse_client_message,
)
from oled_display import OLEDDisplay
from translation_core import normalize_text

try:
    import websockets
except ModuleNotFoundError:
    websockets = None


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run an OLED-backed WebSocket server that accepts translated display text updates."
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=8766, help="TCP port to bind.")
    parser.add_argument("--token", required=True, help="Shared auth token required by clients.")
    parser.add_argument(
        "--status-text",
        default="SERVER DOWN",
        help="OLED text shown when no authenticated sender is connected.",
    )
    parser.add_argument("--rotate", dest="rotate", action="store_true", default=True)
    parser.add_argument("--no-rotate", dest="rotate", action="store_false")
    parser.add_argument("--dc-pin", type=int, help="Optional BCM GPIO override for D/C.")
    parser.add_argument("--rst-pin", type=int, help="Optional BCM GPIO override for reset.")
    parser.add_argument("--debug", action="store_true", help="Print debug logs to stderr.")
    return parser.parse_args()


def debug_log(enabled, message):
    if enabled:
        print(message, file=sys.stderr)


class DisplaySession:
    def __init__(self, oled, status_text, debug=False):
        self.oled = oled
        self.status_text = status_text
        self.debug = debug
        self.last_displayed = None

    def show_status(self):
        self.oled.display_text(self.status_text)
        self.last_displayed = self.status_text

    def handle_display_text(self, request_id, text):
        normalized = normalize_text(text)
        if not normalized:
            return build_ack_message(request_id)

        if normalized == self.last_displayed:
            return build_ack_message(request_id)

        self.oled.display_text(normalized)
        self.last_displayed = normalized
        return build_ack_message(request_id)


class DisplayServer:
    def __init__(self, oled, token, status_text, debug=False):
        self.oled = oled
        self.token = token
        self.status_text = status_text
        self.debug = debug
        self.connections = 0
        self.active_session_id = None
        self.lock = asyncio.Lock()

    async def handler(self, websocket):
        self.connections += 1
        session_id = str(uuid.uuid4())
        session = DisplaySession(self.oled, self.status_text, debug=self.debug)

        debug_log(self.debug, f"[display] client connected session={session_id} active={self.connections}")

        try:
            raw_auth = await websocket.recv()
            try:
                auth_message = parse_client_message(raw_auth)
            except MessageValidationError as exc:
                await websocket.close(code=CLOSE_BAD_REQUEST, reason=str(exc))
                return

            if auth_message["type"] != "auth":
                await websocket.close(code=CLOSE_BAD_REQUEST, reason="First message must be auth.")
                return

            if auth_message["token"] != self.token:
                await websocket.close(code=CLOSE_BAD_AUTH, reason="Invalid token.")
                return

            async with self.lock:
                if self.active_session_id is not None:
                    await websocket.close(code=CLOSE_BAD_REQUEST, reason="Another display sender is already active.")
                    return
                self.active_session_id = session_id

            await websocket.send(build_auth_ok_message(session_id))

            async for raw_message in websocket:
                try:
                    message = parse_client_message(raw_message)
                except MessageValidationError as exc:
                    await websocket.close(code=CLOSE_BAD_REQUEST, reason=str(exc))
                    return

                if message["type"] == "auth":
                    await websocket.close(code=CLOSE_BAD_REQUEST, reason="Auth can only be sent once.")
                    return

                response = session.handle_display_text(message["request_id"], message["text"])
                await websocket.send(response)
        except Exception as exc:
            debug_log(self.debug, f"[display] session={session_id} failed: {exc}")
            try:
                await websocket.close(code=CLOSE_INTERNAL_ERROR, reason="Internal server error.")
            except Exception:
                pass
        finally:
            async with self.lock:
                if self.active_session_id == session_id:
                    self.active_session_id = None
                    session.show_status()
            self.connections -= 1
            debug_log(self.debug, f"[display] client disconnected session={session_id} active={self.connections}")


async def run_server(args):
    if websockets is None:
        raise RuntimeError(
            "The `websockets` package is required to run the display server. "
            "Install it with `python3 -m pip install websockets`."
        )

    oled = OLEDDisplay(rotate=args.rotate, dc_pin=args.dc_pin, rst_pin=args.rst_pin)
    oled.display_text(args.status_text)
    server = DisplayServer(
        oled=oled,
        token=args.token,
        status_text=args.status_text,
        debug=args.debug,
    )

    debug_log(
        args.debug,
        f"[display] starting host={args.host} port={args.port} status_text={args.status_text!r}",
    )

    try:
        async with websockets.serve(server.handler, args.host, args.port):
            await asyncio.Future()
    finally:
        oled.close()


def main():
    args = parse_args()
    asyncio.run(run_server(args))


if __name__ == "__main__":
    main()
