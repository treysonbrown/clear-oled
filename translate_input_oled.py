#!/usr/bin/env python3

import argparse
import sys

from oled_display import OLEDDisplay
from translate_camera_oled import ArgosTranslator, contains_japanese, normalize_text


def parse_args():
    parser = argparse.ArgumentParser(
        description="Enter Japanese text manually, translate it locally, and display the English result on the OLED."
    )
    parser.add_argument("--text", help="Translate one Japanese word or sentence and exit.")
    parser.add_argument("--rotate", dest="rotate", action="store_true", default=True)
    parser.add_argument("--no-rotate", dest="rotate", action="store_false")
    parser.add_argument("--dc-pin", type=int, help="Optional BCM GPIO override for D/C.")
    parser.add_argument("--rst-pin", type=int, help="Optional BCM GPIO override for reset.")
    return parser.parse_args()


def translate_and_display(oled, translator, source_text):
    normalized = normalize_text(source_text)
    if not normalized:
        return None

    if not contains_japanese(normalized):
        raise ValueError("Input does not contain Japanese characters.")

    translated = normalize_text(translator.translate(normalized))
    if not translated:
        raise ValueError("Translation backend returned an empty result.")

    oled.display_text(translated)
    return translated


def interactive_loop(oled, translator):
    while True:
        try:
            source_text = input("Japanese> ")
        except EOFError:
            print("", file=sys.stderr)
            break

        if not source_text.strip():
            continue

        try:
            translated = translate_and_display(oled, translator, source_text)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            continue
        except Exception as exc:
            print(f"Translation failed: {exc}", file=sys.stderr)
            continue

        print(translated)


def main():
    args = parse_args()
    translator = ArgosTranslator()
    oled = OLEDDisplay(rotate=args.rotate, dc_pin=args.dc_pin, rst_pin=args.rst_pin)

    try:
        if args.text:
            translated = translate_and_display(oled, translator, args.text)
            if translated:
                print(translated)
            return

        interactive_loop(oled, translator)
    finally:
        oled.close()


if __name__ == "__main__":
    main()
