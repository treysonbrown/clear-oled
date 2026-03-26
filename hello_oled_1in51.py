#!/usr/bin/env python3

import argparse
import time

from oled_display import OLEDDisplay


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


def main():
    args = parse_args()
    oled = OLEDDisplay(rotate=args.rotate, dc_pin=args.dc_pin, rst_pin=args.rst_pin)

    try:
        oled.display_text(args.text)

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        oled.close()


if __name__ == "__main__":
    main()
