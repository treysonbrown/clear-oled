#!/usr/bin/env python3

import inspect
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ModuleNotFoundError:
    Image = None
    ImageDraw = None
    ImageFont = None


WIDTH = 128
HEIGHT = 64
DEFAULT_PADDING = 2
DEFAULT_LINE_SPACING = 2
REPO_ROOT = Path(__file__).resolve().parent


def add_vendor_search_paths():
    candidates = [
        REPO_ROOT / "lib",
        REPO_ROOT / "vendor" / "lib",
        REPO_ROOT.parent / "OLED_Module_Code" / "RaspberryPi" / "python" / "lib",
    ]

    for path in candidates:
        if path.exists():
            sys.path.insert(0, str(path))


def resolve_driver_class():
    add_vendor_search_paths()

    try:
        from waveshare_OLED import OLED_1in51 as vendor_module

        return vendor_module.OLED_1in51, "waveshare_OLED"
    except ImportError:
        pass

    try:
        from waveshare_transparent_oled import OLED_1in51 as external_class

        return external_class, "waveshare_transparent_oled"
    except ImportError as exc:
        raise RuntimeError(
            "No supported OLED driver found.\n"
            "Preferred path: download Waveshare's OLED_Module_Code package and place "
            "its RaspberryPi/python/lib directory at ./lib in this repo.\n"
            "Fallback path on the Raspberry Pi: install the maintained package with "
            "`pip install waveshare-transparent-oled pillow`."
        ) from exc


def ensure_pillow():
    if Image is None or ImageDraw is None or ImageFont is None:
        raise RuntimeError("Pillow is required. Install it with `python3 -m pip install pillow`.")


def measure_text(draw, font, text):
    if not text:
        return 0, 0

    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def text_fits(draw, font, text, max_width):
    width, _ = measure_text(draw, font, text)
    return width <= max_width


def split_long_token(draw, font, token, max_width):
    parts = []
    current = ""

    for character in token:
        candidate = current + character
        if current and not text_fits(draw, font, candidate, max_width):
            parts.append(current)
            current = character
        else:
            current = candidate

    if current:
        parts.append(current)

    return parts or [token]


def normalize_wrap_tokens(text):
    lines = []

    for paragraph in text.splitlines() or [""]:
        tokens = paragraph.split()
        if not tokens:
            lines.append([""])
        else:
            lines.append(tokens)

    return lines or [[""]]


def truncate_to_width(draw, font, text, max_width):
    if text_fits(draw, font, text, max_width):
        return text

    ellipsis = "..."
    if not text_fits(draw, font, ellipsis, max_width):
        return ""

    candidate = text
    while candidate and not text_fits(draw, font, candidate + ellipsis, max_width):
        candidate = candidate[:-1]

    return candidate + ellipsis


def _wrap_text_internal(
    text,
    *,
    width=WIDTH,
    height=HEIGHT,
    padding=DEFAULT_PADDING,
    line_spacing=DEFAULT_LINE_SPACING,
    truncate_last_line=True,
):
    ensure_pillow()
    image = Image.new("1", (width, height), "WHITE")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    available_width = max(1, width - (padding * 2))
    line_height = max(1, measure_text(draw, font, "Ag")[1])
    max_lines = max(1, (height - (padding * 2) + line_spacing) // (line_height + line_spacing))
    wrapped_lines = []
    overflowed = False

    for paragraph_tokens in normalize_wrap_tokens(text):
        current = ""
        queue = list(paragraph_tokens)

        while queue:
            token = queue.pop(0)
            pieces = split_long_token(draw, font, token, available_width)

            if len(pieces) > 1:
                queue = pieces + queue
                continue

            piece = pieces[0]
            candidate = piece if not current else f"{current} {piece}"
            if current and not text_fits(draw, font, candidate, available_width):
                wrapped_lines.append(current)
                current = piece
            else:
                current = candidate

            if len(wrapped_lines) >= max_lines:
                overflowed = True
                break

        if len(wrapped_lines) >= max_lines:
            overflowed = True
            break

        wrapped_lines.append(current)

        if len(wrapped_lines) >= max_lines:
            break

    if not wrapped_lines:
        wrapped_lines = [""]

    if len(wrapped_lines) > max_lines:
        wrapped_lines = wrapped_lines[:max_lines]
        overflowed = True

    if truncate_last_line and len(wrapped_lines) == max_lines:
        wrapped_lines[-1] = truncate_to_width(draw, font, wrapped_lines[-1], available_width)

    return wrapped_lines[:max_lines], overflowed


def wrap_text(text, width=WIDTH, height=HEIGHT, padding=DEFAULT_PADDING, line_spacing=DEFAULT_LINE_SPACING):
    lines, _ = _wrap_text_internal(
        text,
        width=width,
        height=height,
        padding=padding,
        line_spacing=line_spacing,
        truncate_last_line=True,
    )
    return lines


def fit_transcript_tail_text(
    text,
    width=WIDTH,
    height=HEIGHT,
    padding=DEFAULT_PADDING,
    line_spacing=DEFAULT_LINE_SPACING,
):
    normalized = " ".join((text or "").split())
    if not normalized:
        return ""

    if Image is None or ImageDraw is None or ImageFont is None:
        words = normalized.split(" ")
        return " ".join(words[-6:])

    words = normalized.split(" ")
    for index in range(len(words)):
        candidate = " ".join(words[index:])
        lines, overflowed = _wrap_text_internal(
            candidate,
            width=width,
            height=height,
            padding=padding,
            line_spacing=line_spacing,
            truncate_last_line=False,
        )
        if not overflowed and " ".join(lines).strip():
            return candidate

    return words[-1]


def render_text_image(
    text,
    rotate=True,
    width=WIDTH,
    height=HEIGHT,
    padding=DEFAULT_PADDING,
    line_spacing=DEFAULT_LINE_SPACING,
):
    ensure_pillow()
    image = Image.new("1", (width, height), "WHITE")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    lines = wrap_text(text, width=width, height=height, padding=padding, line_spacing=line_spacing)

    _, line_height = measure_text(draw, font, "Ag")
    block_height = (len(lines) * line_height) + (max(0, len(lines) - 1) * line_spacing)
    start_y = max(padding, (height - block_height) // 2)

    for index, line in enumerate(lines):
        line_width, _ = measure_text(draw, font, line)
        x = max(padding, (width - line_width) // 2)
        y = start_y + index * (line_height + line_spacing)
        draw.text((x, y), line, font=font, fill=0)

    if rotate:
        image = image.rotate(180)

    return image


def instantiate_display(driver_class, dc_pin=None, rst_pin=None):
    try:
        signature = inspect.signature(driver_class)
    except (TypeError, ValueError):
        signature = None

    kwargs = {}
    ignored = []

    if signature and "dc_pin" in signature.parameters and dc_pin is not None:
        kwargs["dc_pin"] = dc_pin
    elif dc_pin is not None:
        ignored.append("dc-pin")

    if signature and "rst_pin" in signature.parameters and rst_pin is not None:
        kwargs["rst_pin"] = rst_pin
    elif rst_pin is not None:
        ignored.append("rst-pin")

    display = driver_class(**kwargs)

    if ignored:
        ignored_flags = ", ".join(f"--{name}" for name in ignored)
        print(
            f"Selected backend does not support {ignored_flags}; using driver defaults.",
            file=sys.stderr,
        )

    return display


class OLEDDisplay:
    def __init__(self, rotate=True, dc_pin=None, rst_pin=None):
        driver_class, backend_name = resolve_driver_class()
        self.backend_name = backend_name
        self.rotate = rotate
        self.display = instantiate_display(driver_class, dc_pin=dc_pin, rst_pin=rst_pin)
        self.display.Init()
        self.display.clear()

    def show_image(self, image):
        self.display.ShowImage(self.display.getbuffer(image))

    def display_text(self, text):
        image = render_text_image(text, rotate=self.rotate)
        self.show_image(image)

    def clear(self):
        self.display.clear()

    def close(self):
        if hasattr(self.display, "module_exit"):
            self.display.module_exit()
