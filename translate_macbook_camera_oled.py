#!/usr/bin/env python3

import argparse
import asyncio
import sys
import time

from display_client import (
    DisplayAuthenticationError,
    DisplayConnectionError,
    DisplayProtocolError,
    DisplayUpdateClient,
)
from translation_core import (
    ArgosTranslator,
    StabilityGate,
    TesseractOcrEngine,
    center_crop,
    contains_japanese,
    normalize_text,
)

try:
    import cv2
except ModuleNotFoundError:
    cv2 = None

try:
    from PIL import Image
except ModuleNotFoundError:
    Image = None


def parse_args():
    parser = argparse.ArgumentParser(
        description="Capture Japanese text with the MacBook webcam, translate it, and send English text to the Pi OLED."
    )
    parser.add_argument("--display-url", required=True, help="WebSocket URL for the Pi display server.")
    parser.add_argument("--token", required=True, help="Shared auth token required by the display server.")
    parser.add_argument("--connect-timeout", type=float, default=5.0, help="Seconds to wait while connecting.")
    parser.add_argument("--reconnect-delay", type=float, default=3.0, help="Seconds to wait before retrying.")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between capture attempts.",
    )
    parser.add_argument("--camera-index", type=int, default=0, help="OpenCV camera index.")
    parser.add_argument("--camera-width", type=int, default=640, help="Requested camera capture width.")
    parser.add_argument("--camera-height", type=int, default=480, help="Requested camera capture height.")
    parser.add_argument("--crop-width", type=int, default=320, help="Width of the center crop used for OCR.")
    parser.add_argument("--crop-height", type=int, default=160, help="Height of the center crop used for OCR.")
    parser.add_argument(
        "--stable-frames",
        type=int,
        default=3,
        help="Number of matching OCR results required before sending an update.",
    )
    parser.add_argument(
        "--history-size",
        type=int,
        default=5,
        help="Number of recent OCR results to keep for stability checks.",
    )
    parser.add_argument("--ocr-lang", default="jpn", help="Tesseract language code to use for OCR.")
    parser.add_argument("--ocr-psm", default="6", help="Tesseract page segmentation mode.")
    parser.add_argument("--debug", action="store_true", help="Print OCR and transport diagnostics to stderr.")
    return parser.parse_args()


def validate_args(args):
    if not args.display_url:
        raise ValueError("--display-url is required.")
    if not args.token:
        raise ValueError("--token is required.")
    if args.stable_frames > args.history_size:
        raise ValueError("--stable-frames cannot be greater than --history-size.")


def debug_log(enabled, message):
    if enabled:
        print(message, file=sys.stderr)


def ensure_opencv():
    if cv2 is None:
        raise RuntimeError(
            "OpenCV is required for MacBook camera capture. "
            "Install it with `python3 -m pip install opencv-python`."
        )


def ensure_pillow():
    if Image is None:
        raise RuntimeError("Pillow is required. Install it with `python3 -m pip install pillow`.")


def open_camera(camera_index, width, height):
    ensure_opencv()
    camera = cv2.VideoCapture(camera_index)
    if width:
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height:
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return camera


def frame_to_image(frame):
    ensure_opencv()
    ensure_pillow()
    if frame is None:
        raise RuntimeError("Camera did not return a frame.")
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb_frame)


def read_camera_image(camera):
    if camera is None or not camera.isOpened():
        raise RuntimeError("Unable to open the MacBook camera.")

    ok, frame = camera.read()
    if not ok:
        raise RuntimeError("Unable to read a frame from the MacBook camera.")

    return frame_to_image(frame)


def process_frame(image, ocr_engine, translator, gate, crop_width, crop_height, last_translation, debug=False):
    crop = center_crop(image, crop_width, crop_height)
    ocr_text = ocr_engine.extract_text(crop)
    stable_text = gate.observe(ocr_text)
    debug_log(debug, f"OCR raw: {normalize_text(ocr_text)!r}")

    if not stable_text:
        return None, last_translation

    if not contains_japanese(stable_text):
        debug_log(debug, f"Stable OCR has no Japanese characters: {stable_text!r}")
        return None, last_translation

    translated = normalize_text(translator.translate(stable_text))
    debug_log(debug, f"Stable OCR: {stable_text!r} -> {translated!r}")

    if not translated or translated == last_translation:
        return None, last_translation

    return translated, translated


async def sleep_until_next_cycle_async(start_time, interval_seconds):
    elapsed = time.monotonic() - start_time
    remaining = interval_seconds - elapsed
    if remaining > 0:
        await asyncio.sleep(remaining)


async def run(args):
    ensure_opencv()
    ensure_pillow()

    camera = open_camera(args.camera_index, args.camera_width, args.camera_height)
    ocr_engine = TesseractOcrEngine(language=args.ocr_lang, psm=args.ocr_psm)
    translator = ArgosTranslator()
    gate = StabilityGate(history_size=args.history_size, min_stable=args.stable_frames)
    client = DisplayUpdateClient(
        url=args.display_url,
        token=args.token,
        connect_timeout=args.connect_timeout,
        debug=args.debug,
        logger=lambda message: debug_log(True, message),
    )
    last_translation = None

    debug_log(args.debug, f"Using display server: {args.display_url}")
    debug_log(args.debug, f"Using camera index: {args.camera_index}")

    try:
        while True:
            cycle_start = time.monotonic()

            try:
                image = await asyncio.to_thread(read_camera_image, camera)
                translated, last_translation = process_frame(
                    image=image,
                    ocr_engine=ocr_engine,
                    translator=translator,
                    gate=gate,
                    crop_width=args.crop_width,
                    crop_height=args.crop_height,
                    last_translation=last_translation,
                    debug=args.debug,
                )

                if translated:
                    await client.send_text(translated)
            except DisplayAuthenticationError as exc:
                debug_log(args.debug, f"Display authentication failed: {exc}")
                await client.close()
                await sleep_until_next_cycle_async(cycle_start, args.reconnect_delay)
                continue
            except (DisplayConnectionError, DisplayProtocolError) as exc:
                debug_log(args.debug, f"Display update failure: {exc}")
                await client.close()
                await sleep_until_next_cycle_async(cycle_start, args.reconnect_delay)
                continue
            except Exception as exc:
                debug_log(args.debug, f"Capture/OCR/translation failure: {exc}")
                await sleep_until_next_cycle_async(cycle_start, args.interval)
                continue

            await sleep_until_next_cycle_async(cycle_start, args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        await client.close()
        if camera is not None:
            camera.release()


def main():
    args = parse_args()

    try:
        validate_args(args)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from exc

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
