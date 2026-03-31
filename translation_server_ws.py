#!/usr/bin/env python3

import argparse
import asyncio
import sys
import uuid

from remote_protocol import (
    CLOSE_BAD_AUTH,
    CLOSE_BAD_REQUEST,
    CLOSE_INTERNAL_ERROR,
    MessageValidationError,
    build_auth_ok_message,
    build_error_message,
    build_noop_message,
    build_translation_message,
    parse_client_message,
)
from translation_core import (
    ArgosTranslator,
    StabilityGate,
    TesseractOcrEngine,
    contains_japanese,
    decode_base64_image,
    normalize_text,
)

try:
    import websockets
except ModuleNotFoundError:
    websockets = None


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the remote OCR and translation WebSocket service for clear-oled clients."
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=8765, help="TCP port to bind.")
    parser.add_argument("--token", required=True, help="Shared auth token required by clients.")
    parser.add_argument("--ocr-lang", default="jpn", help="Default Tesseract language code.")
    parser.add_argument("--ocr-psm", default="6", help="Default Tesseract page segmentation mode.")
    parser.add_argument(
        "--stable-frames",
        type=int,
        default=3,
        help="Number of matching OCR results required before translating.",
    )
    parser.add_argument(
        "--history-size",
        type=int,
        default=5,
        help="Number of recent OCR results kept per client connection.",
    )
    parser.add_argument(
        "--max-image-bytes",
        type=int,
        default=131072,
        help="Maximum decoded image payload accepted from a client.",
    )
    parser.add_argument("--debug", action="store_true", help="Print debug logs to stderr.")
    return parser.parse_args()


def debug_log(enabled, message):
    if enabled:
        print(message, file=sys.stderr)


class TranslationSession:
    def __init__(self, translator, ocr_engine, history_size, stable_frames, max_image_bytes, debug=False):
        self.translator = translator
        self.ocr_engine = ocr_engine
        self.gate = StabilityGate(history_size=history_size, min_stable=stable_frames)
        self.max_image_bytes = max_image_bytes
        self.debug = debug
        self.last_translation = None

    def handle_ocr_text(self, request_id, raw_text):
        normalized_raw = normalize_text(raw_text)
        stable_text = self.gate.observe(raw_text)

        if self.debug:
            debug_log(True, f"[{request_id}] OCR raw: {normalized_raw!r}")

        if not stable_text:
            if normalized_raw and normalized_raw == self.gate.last_accepted:
                return build_noop_message(request_id, "duplicate_translation")
            return build_noop_message(request_id, "waiting_for_stability")

        if not contains_japanese(stable_text):
            return build_noop_message(request_id, "no_japanese")

        try:
            translated = normalize_text(self.translator.translate(stable_text))
        except Exception as exc:
            return build_error_message(request_id, "TRANSLATION_FAILURE", str(exc))

        if not translated:
            return build_error_message(request_id, "TRANSLATION_FAILURE", "Translation was empty.")

        if translated == self.last_translation:
            return build_noop_message(request_id, "duplicate_translation")

        self.last_translation = translated
        return build_translation_message(request_id, stable_text, translated)

    def handle_text_request(self, request_id, text):
        normalized = normalize_text(text)
        if not normalized or not contains_japanese(normalized):
            return build_error_message(
                request_id,
                "BAD_REQUEST",
                "Text request must contain Japanese characters.",
            )

        try:
            translated = normalize_text(self.translator.translate(normalized))
        except Exception as exc:
            return build_error_message(request_id, "TRANSLATION_FAILURE", str(exc))

        if not translated:
            return build_error_message(request_id, "TRANSLATION_FAILURE", "Translation was empty.")

        return build_translation_message(request_id, normalized, translated)

    def handle_frame_request(self, message):
        request_id = message["request_id"]

        try:
            image = decode_base64_image(
                message["image_jpeg_base64"],
                max_bytes=self.max_image_bytes,
            )
        except ValueError as exc:
            return build_error_message(request_id, "BAD_IMAGE", str(exc))

        try:
            raw_text = self.ocr_engine.extract_text(image)
        except Exception as exc:
            return build_error_message(request_id, "OCR_FAILURE", str(exc))

        return self.handle_ocr_text(request_id, raw_text)


class TranslationServer:
    def __init__(self, translator, ocr_engine, token, history_size, stable_frames, max_image_bytes, debug=False):
        self.translator = translator
        self.ocr_engine = ocr_engine
        self.token = token
        self.history_size = history_size
        self.stable_frames = stable_frames
        self.max_image_bytes = max_image_bytes
        self.debug = debug
        self.connections = 0

    async def handler(self, websocket):
        self.connections += 1
        session_id = str(uuid.uuid4())
        session = TranslationSession(
            translator=self.translator,
            ocr_engine=self.ocr_engine,
            history_size=self.history_size,
            stable_frames=self.stable_frames,
            max_image_bytes=self.max_image_bytes,
            debug=self.debug,
        )

        debug_log(self.debug, f"[server] client connected session={session_id} active={self.connections}")

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

                if message["type"] == "text":
                    response = session.handle_text_request(message["request_id"], message["text"])
                else:
                    response = session.handle_frame_request(message)

                await websocket.send(response)
        except Exception as exc:
            debug_log(self.debug, f"[server] session={session_id} failed: {exc}")
            try:
                await websocket.close(code=CLOSE_INTERNAL_ERROR, reason="Internal server error.")
            except Exception:
                pass
        finally:
            self.connections -= 1
            debug_log(self.debug, f"[server] client disconnected session={session_id} active={self.connections}")


async def run_server(args):
    if websockets is None:
        raise RuntimeError(
            "The `websockets` package is required to run the server. "
            "Install it with `python3 -m pip install websockets`."
        )

    if args.stable_frames > args.history_size:
        raise RuntimeError("--stable-frames cannot be greater than --history-size.")

    translator = ArgosTranslator()
    ocr_engine = TesseractOcrEngine(language=args.ocr_lang, psm=args.ocr_psm)
    server = TranslationServer(
        translator=translator,
        ocr_engine=ocr_engine,
        token=args.token,
        history_size=args.history_size,
        stable_frames=args.stable_frames,
        max_image_bytes=args.max_image_bytes,
        debug=args.debug,
    )

    debug_log(
        args.debug,
        f"[server] starting host={args.host} port={args.port} "
        f"ocr_lang={args.ocr_lang} ocr_psm={args.ocr_psm}",
    )

    async with websockets.serve(server.handler, args.host, args.port):
        await asyncio.Future()


def main():
    args = parse_args()
    asyncio.run(run_server(args))


if __name__ == "__main__":
    main()
