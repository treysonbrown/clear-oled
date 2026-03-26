#!/usr/bin/env python3

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import Counter, deque
from io import BytesIO

try:
    from PIL import Image
except ModuleNotFoundError:
    Image = None

from oled_display import OLEDDisplay


JAPANESE_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
WHITESPACE_RE = re.compile(r"\s+")
PUNCTUATION_SPACE_RE = re.compile(r"\s+([,.;:!?。、「」『』（）()])")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Capture Japanese text with the Pi camera, translate it locally, and display English on the OLED."
    )
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


def normalize_text(text):
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = WHITESPACE_RE.sub(" ", normalized).strip()
    normalized = PUNCTUATION_SPACE_RE.sub(r"\1", normalized)
    return normalized


def contains_japanese(text):
    return bool(JAPANESE_CHAR_RE.search(text or ""))


def center_crop(image, crop_width, crop_height):
    crop_width = max(1, min(crop_width, image.width))
    crop_height = max(1, min(crop_height, image.height))

    left = (image.width - crop_width) // 2
    top = (image.height - crop_height) // 2
    right = left + crop_width
    bottom = top + crop_height
    return image.crop((left, top, right, bottom))


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


class TesseractOcrEngine:
    def __init__(self, language="jpn", psm="6"):
        self.language = language
        self.psm = str(psm)
        self.binary = shutil.which("tesseract")
        if not self.binary:
            raise RuntimeError("`tesseract` is not installed or not on PATH.")

    def extract_text(self, image):
        with tempfile.NamedTemporaryFile(suffix=".png") as handle:
            image.save(handle.name)
            result = subprocess.run(
                [
                    self.binary,
                    handle.name,
                    "stdout",
                    "-l",
                    self.language,
                    "--oem",
                    "1",
                    "--psm",
                    self.psm,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=20,
            )
        return result.stdout


class ArgosTranslator:
    def __init__(self, from_code="ja", to_code="en"):
        try:
            import argostranslate.translate
        except ImportError as exc:
            raise RuntimeError(
                "Argos Translate is not installed. Install it on the Raspberry Pi with "
                "`python3 -m pip install argostranslate` and install a Japanese->English package."
            ) from exc

        installed_languages = argostranslate.translate.get_installed_languages()
        from_language = next((lang for lang in installed_languages if lang.code == from_code), None)
        to_language = next((lang for lang in installed_languages if lang.code == to_code), None)

        if from_language is None or to_language is None:
            raise RuntimeError(
                "Argos Translate language packages for Japanese and English are not installed."
            )

        translation = from_language.get_translation(to_language)
        if translation is None:
            raise RuntimeError("No installed Argos translation package found for Japanese -> English.")

        self.translation = translation

    def translate(self, text):
        return self.translation.translate(text)


class StabilityGate:
    def __init__(self, history_size=5, min_stable=3):
        self.history_size = max(1, history_size)
        self.min_stable = max(1, min_stable)
        self.history = deque(maxlen=self.history_size)
        self.last_accepted = None
        self.latest_raw = {}

    def observe(self, raw_text):
        normalized = normalize_text(raw_text)
        self.history.append(normalized)
        self.latest_raw[normalized] = raw_text

        if not normalized:
            return None

        counts = Counter(item for item in self.history if item)
        candidate, count = counts.most_common(1)[0]

        if normalized != candidate:
            return None

        if count < self.min_stable:
            return None

        if candidate == self.last_accepted:
            return None

        self.last_accepted = candidate
        return normalize_text(self.latest_raw[candidate])


def debug_log(enabled, message):
    if enabled:
        print(message, file=sys.stderr)


def sleep_until_next_cycle(start_time, interval_seconds):
    elapsed = time.monotonic() - start_time
    remaining = interval_seconds - elapsed
    if remaining > 0:
        time.sleep(remaining)


def run_loop(args):
    camera_cmd = detect_camera_command(args.camera_cmd)
    ocr_engine = TesseractOcrEngine(language=args.ocr_lang, psm=args.ocr_psm)
    translator = ArgosTranslator()
    gate = StabilityGate(history_size=args.history_size, min_stable=args.stable_frames)
    oled = OLEDDisplay(rotate=args.rotate, dc_pin=args.dc_pin, rst_pin=args.rst_pin)
    last_translation = None

    debug_log(args.debug, f"Using camera command: {camera_cmd}")
    debug_log(args.debug, f"Using OLED backend: {oled.backend_name}")

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


def main():
    args = parse_args()

    if args.stable_frames > args.history_size:
        print("--stable-frames cannot be greater than --history-size.", file=sys.stderr)
        raise SystemExit(2)

    run_loop(args)


if __name__ == "__main__":
    main()
