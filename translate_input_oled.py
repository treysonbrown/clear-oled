#!/usr/bin/env python3

import argparse
import asyncio
import sys

from oled_display import OLEDDisplay
from remote_client import (
    RemoteAuthenticationError,
    RemoteConnectionError,
    RemoteProtocolError,
    RemoteTranslationClient,
)
from translation_core import ArgosTranslator, contains_japanese, normalize_text


def parse_args():
    parser = argparse.ArgumentParser(
        description="Enter Japanese text manually, translate it, and display the English result on the OLED."
    )
    parser.add_argument("--text", help="Translate one Japanese word or sentence and exit.")
    parser.add_argument("--backend", choices=("local", "remote"), default="local")
    parser.add_argument("--remote-url", help="WebSocket URL for the remote OCR/translation server.")
    parser.add_argument("--token", help="Shared auth token required by the remote server.")
    parser.add_argument("--connect-timeout", type=float, default=5.0, help="Seconds to wait while connecting.")
    parser.add_argument("--rotate", dest="rotate", action="store_true", default=True)
    parser.add_argument("--no-rotate", dest="rotate", action="store_false")
    parser.add_argument("--dc-pin", type=int, help="Optional BCM GPIO override for D/C.")
    parser.add_argument("--rst-pin", type=int, help="Optional BCM GPIO override for reset.")
    return parser.parse_args()


def validate_args(args):
    if args.backend == "remote":
        if not args.remote_url:
            raise ValueError("--remote-url is required when --backend remote is selected.")
        if not args.token:
            raise ValueError("--token is required when --backend remote is selected.")


def translate_and_display_local(oled, translator, source_text):
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


async def translate_and_display_remote(oled, client, source_text):
    normalized = normalize_text(source_text)
    if not normalized:
        return None

    if not contains_japanese(normalized):
        raise ValueError("Input does not contain Japanese characters.")

    response = await client.send_text(normalized)

    if response["type"] == "error":
        raise RuntimeError(f"{response['code']}: {response['message']}")

    if response["type"] != "translation":
        raise RuntimeError(f"Unexpected remote response: {response['type']}")

    translated = normalize_text(response["translated_text"])
    if not translated:
        raise ValueError("Translation backend returned an empty result.")

    oled.display_text(translated)
    return translated


def interactive_loop_local(oled, translator):
    while True:
        try:
            source_text = input("Japanese> ")
        except EOFError:
            print("", file=sys.stderr)
            break

        if not source_text.strip():
            continue

        try:
            translated = translate_and_display_local(oled, translator, source_text)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            continue
        except Exception as exc:
            print(f"Translation failed: {exc}", file=sys.stderr)
            continue

        print(translated)


async def interactive_loop_remote(oled, client):
    while True:
        try:
            source_text = await asyncio.to_thread(input, "Japanese> ")
        except EOFError:
            print("", file=sys.stderr)
            break

        if not source_text.strip():
            continue

        try:
            translated = await translate_and_display_remote(oled, client, source_text)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            continue
        except (RemoteAuthenticationError, RemoteConnectionError, RemoteProtocolError) as exc:
            oled.display_text("SERVER DOWN")
            print(f"Remote translation failed: {exc}", file=sys.stderr)
            continue
        except Exception as exc:
            print(f"Translation failed: {exc}", file=sys.stderr)
            continue

        print(translated)


async def run_remote(args):
    oled = OLEDDisplay(rotate=args.rotate, dc_pin=args.dc_pin, rst_pin=args.rst_pin)
    client = RemoteTranslationClient(
        url=args.remote_url,
        token=args.token,
        connect_timeout=args.connect_timeout,
    )

    try:
        if args.text:
            try:
                translated = await translate_and_display_remote(oled, client, args.text)
            except (RemoteAuthenticationError, RemoteConnectionError, RemoteProtocolError) as exc:
                oled.display_text("SERVER DOWN")
                print(f"Remote translation failed: {exc}", file=sys.stderr)
                raise SystemExit(1) from exc

            if translated:
                print(translated)
            return

        await interactive_loop_remote(oled, client)
    finally:
        await client.close()
        oled.close()


def run_local(args):
    translator = ArgosTranslator()
    oled = OLEDDisplay(rotate=args.rotate, dc_pin=args.dc_pin, rst_pin=args.rst_pin)

    try:
        if args.text:
            translated = translate_and_display_local(oled, translator, args.text)
            if translated:
                print(translated)
            return

        interactive_loop_local(oled, translator)
    finally:
        oled.close()


def main():
    args = parse_args()

    try:
        validate_args(args)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from exc

    if args.backend == "remote":
        asyncio.run(run_remote(args))
        return

    run_local(args)


if __name__ == "__main__":
    main()
