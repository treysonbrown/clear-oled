#!/usr/bin/env python3

import argparse
import inspect
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH = 128
HEIGHT = 64

REPO_ROOT = Path(__file__).resolve().parent


def parse_args():
    parser = argparse.ArgumentParser(
        description="Render text on the Waveshare 1.51-inch Transparent OLED."
    )
    parser.add_argument("--text", default="hello", help="Text to display.")
    parser.add_argument(
        "--rotate",
        dest="rotate",
        action="store_true",
        default=True,
        help="Rotate the rendered image 180 degrees before display (default).",
    )
    parser.add_argument(
        "--no-rotate",
        dest="rotate",
        action="store_false",
        help="Disable the 180-degree rotation.",
    )
    parser.add_argument(
        "--dc-pin",
        type=int,
        help="Optional BCM GPIO override for D/C if the selected backend supports it.",
    )
    parser.add_argument(
        "--rst-pin",
        type=int,
        help="Optional BCM GPIO override for reset if the selected backend supports it.",
    )
    return parser.parse_args()


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
            "`pip install waveshare-transparent-oled pillow`.\n"
            "Validate the hardware first with the official Waveshare C demo before "
            "running this script."
        ) from exc


def build_frame(text, rotate):
    image = Image.new("1", (WIDTH, HEIGHT), "WHITE")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    text_box = draw.textbbox((0, 0), text, font=font)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    text_x = (WIDTH - text_width) // 2
    text_y = (HEIGHT - text_height) // 2
    draw.text((text_x, text_y), text, font=font, fill=0)

    if rotate:
        image = image.rotate(180)

    return image


def instantiate_display(driver_class, args):
    try:
        signature = inspect.signature(driver_class)
    except (TypeError, ValueError):
        signature = None

    kwargs = {}

    if signature and "dc_pin" in signature.parameters and args.dc_pin is not None:
        kwargs["dc_pin"] = args.dc_pin
    if signature and "rst_pin" in signature.parameters and args.rst_pin is not None:
        kwargs["rst_pin"] = args.rst_pin

    display = driver_class(**kwargs)
    ignored = []

    if args.dc_pin is not None and (not signature or "dc_pin" not in signature.parameters):
        ignored.append("dc-pin")
    if args.rst_pin is not None and (not signature or "rst_pin" not in signature.parameters):
        ignored.append("rst-pin")

    if ignored:
        ignored_flags = ", ".join(f"--{name}" for name in ignored)
        print(
            f"Selected backend does not support {ignored_flags}; using driver defaults.",
            file=sys.stderr,
        )

    return display


def initialize_display(display, backend_name):
    display.Init()
    display.clear()
    print(f"Initialized OLED with backend: {backend_name}", file=sys.stderr)


def main():
    args = parse_args()
    try:
        driver_class, backend_name = resolve_driver_class()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc

    display = instantiate_display(driver_class, args)

    try:
        initialize_display(display, backend_name)
        frame = build_frame(args.text, args.rotate)
        display.ShowImage(display.getbuffer(frame))

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if hasattr(display, "module_exit"):
            display.module_exit()


if __name__ == "__main__":
    main()
