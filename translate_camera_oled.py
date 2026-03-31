#!/usr/bin/env python3

import argparse
import asyncio
import shutil
import subprocess
import sys
import time
from io import BytesIO

try:
    from PIL import Image
except ModuleNotFoundError:
    Image = None

from oled_display import OLEDDisplay
from remote_client import (
    RemoteAuthenticationError,
    RemoteConnectionError,
    RemoteProtocolError,
    RemoteTranslationClient,
)
from translation_core import (
    ArgosTranslator,
    StabilityGate,
    TesseractOcrEngine,
    center_crop,
    contains_japanese,
    normalize_text,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Capture Japanese text with the Pi camera and display the English result on the OLED."
    )
    parser.add_argument("--backend", choices=("local", "remote"), default="local")
    parser.add_argument("--remote-url", help="WebSocket URL for the remote OCR/translation server.")
    parser.add_argument("--token", help="Shared auth token required by the remote server.")
    parser.add_argument("--connect-timeout", type=float, default=5.0, help="Seconds to wait while connecting.")
    parser.add_argument("--reconnect-delay", type=float, default=3.0, help="Seconds to wait before retrying.")
    parser.add_argument("--status-text", default="SERVER DOWN", help="OLED text shown while the server is down.")
    parser.add_argument("--rotate", dest="rotate", action="store_true", default=True)
    parser.add_argument("--no-rotate", dest="rotate", action="store_false")
    parser.add_argument("--dc-pin", type=int, help="Optional BCM GPIO override for D/C.")
    parser.add_argument("--rst-pin", type=int, help="Optional BCM GPIO override for reset.")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between capture attempts.",
    )
    parser.add_argument(
        "--crop-width",
        type=int,
        default=320,
        help="Width of the center crop used for OCR.",
    )
    parser.add_argument(
        "--crop-height",
        type=int,
        default=160,
        help="Height of the center crop used for OCR.",
    )
    parser.add_argument(
        "--stable-frames",
        type=int,
        default=3,
        help="Number of matching OCR results required before updating the OLED.",
    )
    parser.add_argument(
        "--history-size",
        type=int,
        default=5,
        help="Number of recent OCR results to keep for stability checks.",
    )
    parser.add_argument(
        "--camera-width",
        type=int,
        default=640,
        help="Capture width passed to the camera command.",
    )
    parser.add_argument(
        "--camera-height",
        type=int,
        default=480,
        help="Capture height passed to the camera command.",
    )
    parser.add_argument(
        "--camera-cmd",
        help="Override the camera command. Defaults to rpicam-still or libcamera-still.",
    )
    parser.add_argument(
        "--ocr-lang",
        default="jpn",
        help="Tesseract language code to use for OCR.",
    )
    parser.add_argument(
        "--ocr-psm",
        default="6",
        help="Tesseract page segmentation mode.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print OCR and translation diagnostics to stderr.",
    )
    return parser.parse_args()


def validate_args(args):
    if args.stable_frames > args.history_size:
        raise ValueError("--stable-frames cannot be greater than --history-size.")

    if args.backend == "remote":
        if not args.remote_url:
            raise ValueError("--remote-url is required when --backend remote is selected.")
        if not args.token:
            raise ValueError("--token is required when --backend remote is selected.")


def debug_log(enabled, message):
    if enabled:
        print(message, file=sys.stderr)


def detect_camera_command(preferred=None):
    candidates = [preferred] if preferred else ["rpicam-still", "libcamera-still"]

    for candidate in candidates:
        if candidate and shutil.which(candidate):
            return candidate

    raise RuntimeError(
        "No supported camera command found. Install `rpicam-still` or `libcamera-still`, "
        "or pass --camera-cmd with the correct executable."
    )


def capture_frame(camera_cmd, width, height):
    if Image is None:
        raise RuntimeError("Pillow is required. Install it with `python3 -m pip install pillow`.")

    command = [
        camera_cmd,
        "--immediate",
        "--nopreview",
        "--encoding",
        "jpg",
        "--width",
        str(width),
        "--height",
        str(height),
        "-o",
        "-",
    ]

    result = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    return Image.open(BytesIO(result.stdout)).convert("RGB")


def sleep_until_next_cycle(start_time, interval_seconds):
    elapsed = time.monotonic() - start_time
    remaining = interval_seconds - elapsed
    if remaining > 0:
        time.sleep(remaining)


async def sleep_until_next_cycle_async(start_time, interval_seconds):
    elapsed = time.monotonic() - start_time
    remaining = interval_seconds - elapsed
    if remaining > 0:
        await asyncio.sleep(remaining)


def run_local_loop(args):
    camera_cmd = detect_camera_command(args.camera_cmd)
    ocr_engine = TesseractOcrEngine(language=args.ocr_lang, psm=args.ocr_psm)
    translator = ArgosTranslator()
    gate = StabilityGate(history_size=args.history_size, min_stable=args.stable_frames)
    oled = OLEDDisplay(rotate=args.rotate, dc_pin=args.dc_pin, rst_pin=args.rst_pin)
    last_translation = None

    debug_log(args.debug, f"Using camera command: {camera_cmd}")
    debug_log(args.debug, f"Using OLED backend: {oled.backend_name}")
    debug_log(args.debug, "Using translation backend: local")

    try:
        while True:
            cycle_start = time.monotonic()

            try:
                frame = capture_frame(camera_cmd, args.camera_width, args.camera_height)
                crop = center_crop(frame, args.crop_width, args.crop_height)
                ocr_text = ocr_engine.extract_text(crop)
                stable_text = gate.observe(ocr_text)
                debug_log(args.debug, f"OCR raw: {normalize_text(ocr_text)!r}")
            except subprocess.CalledProcessError as exc:
                stderr = exc.stderr.strip() if isinstance(exc.stderr, str) else exc.stderr
                debug_log(args.debug, f"Capture/OCR command failed: {stderr}")
                sleep_until_next_cycle(cycle_start, args.interval)
                continue
            except Exception as exc:
                debug_log(args.debug, f"Capture/OCR failure: {exc}")
                sleep_until_next_cycle(cycle_start, args.interval)
                continue

            if not stable_text:
                sleep_until_next_cycle(cycle_start, args.interval)
                continue

            if not contains_japanese(stable_text):
                debug_log(args.debug, f"Stable OCR has no Japanese characters: {stable_text!r}")
                sleep_until_next_cycle(cycle_start, args.interval)
                continue

            try:
                translated = normalize_text(translator.translate(stable_text))
            except Exception as exc:
                debug_log(args.debug, f"Translation failure: {exc}")
                sleep_until_next_cycle(cycle_start, args.interval)
                continue

            debug_log(args.debug, f"Stable OCR: {stable_text!r} -> {translated!r}")

            if translated and translated != last_translation:
                oled.display_text(translated)
                last_translation = translated

            sleep_until_next_cycle(cycle_start, args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        oled.close()


async def run_remote_loop(args):
    camera_cmd = detect_camera_command(args.camera_cmd)
    oled = OLEDDisplay(rotate=args.rotate, dc_pin=args.dc_pin, rst_pin=args.rst_pin)
    client = RemoteTranslationClient(
        url=args.remote_url,
        token=args.token,
        connect_timeout=args.connect_timeout,
        debug=args.debug,
        logger=lambda message: debug_log(True, message),
    )
    status_visible = False

    debug_log(args.debug, f"Using camera command: {camera_cmd}")
    debug_log(args.debug, f"Using OLED backend: {oled.backend_name}")
    debug_log(args.debug, f"Using translation backend: remote url={args.remote_url}")

    try:
        while True:
            cycle_start = time.monotonic()

            try:
                frame = capture_frame(camera_cmd, args.camera_width, args.camera_height)
                crop = center_crop(frame, args.crop_width, args.crop_height)
                response = await client.send_frame(
                    image=crop,
                    source_width=frame.width,
                    source_height=frame.height,
                    crop_width=crop.width,
                    crop_height=crop.height,
                    ocr_lang=args.ocr_lang,
                    ocr_psm=args.ocr_psm,
                )
            except subprocess.CalledProcessError as exc:
                stderr = exc.stderr.strip() if isinstance(exc.stderr, str) else exc.stderr
                debug_log(args.debug, f"Capture command failed: {stderr}")
                await sleep_until_next_cycle_async(cycle_start, args.interval)
                continue
            except RemoteAuthenticationError as exc:
                debug_log(args.debug, f"Remote authentication failed: {exc}")
                if not status_visible:
                    oled.display_text(args.status_text)
                    status_visible = True
                await client.close()
                await sleep_until_next_cycle_async(cycle_start, max(args.reconnect_delay, 15.0))
                continue
            except (RemoteConnectionError, RemoteProtocolError) as exc:
                debug_log(args.debug, f"Remote connection failure: {exc}")
                if not status_visible:
                    oled.display_text(args.status_text)
                    status_visible = True
                await client.close()
                await sleep_until_next_cycle_async(cycle_start, args.reconnect_delay)
                continue
            except Exception as exc:
                debug_log(args.debug, f"Capture/remote failure: {exc}")
                await sleep_until_next_cycle_async(cycle_start, args.interval)
                continue

            if response["type"] == "translation":
                translated = normalize_text(response["translated_text"])
                if translated:
                    oled.display_text(translated)
                    status_visible = False
                    debug_log(
                        args.debug,
                        f"Remote translation: {response['source_text']!r} -> {translated!r}",
                    )
            elif response["type"] == "error":
                debug_log(
                    args.debug,
                    f"Remote server error {response['code']}: {response['message']}",
                )
            elif response["type"] == "noop":
                debug_log(args.debug, f"Remote noop: {response['reason']}")

            await sleep_until_next_cycle_async(cycle_start, args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        await client.close()
        oled.close()


def main():
    args = parse_args()

    try:
        validate_args(args)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from exc

    if args.backend == "remote":
        asyncio.run(run_remote_loop(args))
        return

    run_local_loop(args)


if __name__ == "__main__":
    main()
