#!/usr/bin/env python3

import base64
import re
import shutil
import subprocess
import tempfile
import unicodedata
from io import BytesIO

try:
    from PIL import Image
except ModuleNotFoundError:
    Image = None


JAPANESE_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
WHITESPACE_RE = re.compile(r"\s+")
PUNCTUATION_SPACE_RE = re.compile(r"\s+([,.;:!?。、「」『』（）()])")


def ensure_pillow():
    if Image is None:
        raise RuntimeError("Pillow is required. Install it with `python3 -m pip install pillow`.")


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


def encode_image_as_base64_jpeg(image, quality=85):
    ensure_pillow()
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def decode_base64_image(image_b64, max_bytes=None):
    ensure_pillow()

    try:
        payload = base64.b64decode(image_b64, validate=True)
    except Exception as exc:
        raise ValueError("Invalid base64 image payload.") from exc

    if max_bytes is not None and len(payload) > max_bytes:
        raise ValueError(f"Image payload exceeds {max_bytes} bytes.")

    try:
        return Image.open(BytesIO(payload)).convert("RGB")
    except Exception as exc:
        raise ValueError("Image payload is not a valid JPEG/PNG image.") from exc


class TesseractOcrEngine:
    def __init__(self, language="jpn", psm="6"):
        self.language = language
        self.psm = str(psm)
        self.binary = shutil.which("tesseract")
        if not self.binary:
            raise RuntimeError("`tesseract` is not installed or not on PATH.")

    def extract_text(self, image):
        ensure_pillow()
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
                "Argos Translate is not installed. Install it with "
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
        from collections import Counter, deque

        self.Counter = Counter
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

        counts = self.Counter(item for item in self.history if item)
        candidate, count = counts.most_common(1)[0]

        if normalized != candidate:
            return None

        if count < self.min_stable:
            return None

        if candidate == self.last_accepted:
            return None

        self.last_accepted = candidate
        return normalize_text(self.latest_raw[candidate])
