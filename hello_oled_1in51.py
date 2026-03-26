#!/usr/bin/env python3

import argparse
import time

import lgpio
import spidev
from PIL import Image, ImageDraw, ImageFont


WIDTH = 128
HEIGHT = 64
PAGES = HEIGHT // 8

SPI_BUS = 0
SPI_DEVICE = 0
SPI_SPEED_HZ = 1_000_000

# The common Raspberry Pi SSD1309 SPI pinout is CE0 + DC on GPIO24 + RST on GPIO25.
# Some guides for this Waveshare panel list other GPIOs, so these remain configurable.
DEFAULT_DC_PIN = 24
DEFAULT_RST_PIN = 25


class SSD1309:
    def __init__(
        self,
        dc_pin=DEFAULT_DC_PIN,
        rst_pin=DEFAULT_RST_PIN,
        spi_bus=SPI_BUS,
        spi_device=SPI_DEVICE,
        spi_speed_hz=SPI_SPEED_HZ,
        rotate_180=True,
    ):
        self.dc_pin = dc_pin
        self.rst_pin = rst_pin
        self.rotate_180 = rotate_180

        self.spi = spidev.SpiDev()
        self.spi.open(spi_bus, spi_device)
        self.spi.max_speed_hz = spi_speed_hz
        self.spi.mode = 0

        self.gpio = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(self.gpio, self.dc_pin, 0)
        lgpio.gpio_claim_output(self.gpio, self.rst_pin, 1)

    def cleanup(self):
        self.spi.close()
        lgpio.gpiochip_close(self.gpio)

    def reset(self):
        lgpio.gpio_write(self.gpio, self.rst_pin, 1)
        time.sleep(0.05)
        lgpio.gpio_write(self.gpio, self.rst_pin, 0)
        time.sleep(0.05)
        lgpio.gpio_write(self.gpio, self.rst_pin, 1)
        time.sleep(0.10)

    def write_command(self, command):
        lgpio.gpio_write(self.gpio, self.dc_pin, 0)
        self.spi.writebytes([command])

    def write_data(self, data):
        lgpio.gpio_write(self.gpio, self.dc_pin, 1)
        self.spi.writebytes(list(data))

    def init(self):
        self.reset()

        # Page addressing mode is the most reliable way to talk to this panel.
        init_sequence = [
            0xAE,  # display off
            0xD5, 0x80,  # display clock divide / oscillator frequency
            0xA8, 0x3F,  # multiplex ratio
            0xD3, 0x00,  # display offset
            0x40,  # display start line
            0x8D, 0x14,  # charge pump on
            0x20, 0x02,  # page addressing mode
            0xA1,  # segment remap
            0xC8,  # COM scan direction remap
            0xDA, 0x12,  # COM pins hardware configuration
            0x81, 0x7F,  # contrast
            0xD9, 0xF1,  # pre-charge period
            0xDB, 0x40,  # VCOMH deselect level
            0xA4,  # resume RAM content display
            0xA6,  # normal display
            0x2E,  # deactivate scroll
            0xAF,  # display on
        ]

        for command in init_sequence:
            self.write_command(command)

    def clear(self):
        self.show(Image.new("1", (WIDTH, HEIGHT), 1))

    def image_to_buffer(self, image):
        image = image.convert("1")
        if self.rotate_180:
            image = image.rotate(180)

        pixels = image.load()
        buffer = []

        for page in range(PAGES):
            for x in range(WIDTH):
                value = 0
                for bit in range(8):
                    y = (page * 8) + bit
                    if pixels[x, y] == 0:
                        value |= 1 << bit
                buffer.append(value)

        return buffer

    def show(self, image):
        buffer = self.image_to_buffer(image)

        for page in range(PAGES):
            start = page * WIDTH
            end = start + WIDTH
            self.write_command(0xB0 + page)
            self.write_command(0x00)
            self.write_command(0x10)
            self.write_data(buffer[start:end])


def build_frame(text):
    image = Image.new("1", (WIDTH, HEIGHT), 1)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    text_box = draw.textbbox((0, 0), text, font=font)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    text_x = (WIDTH - text_width) // 2
    text_y = (HEIGHT - text_height) // 2
    draw.text((text_x, text_y), text, font=font, fill=0)
    return image


def parse_args():
    parser = argparse.ArgumentParser(
        description="Display text on the Waveshare 1.51-inch transparent OLED."
    )
    parser.add_argument("--text", default="hello", help="Text to show on the display.")
    parser.add_argument("--dc-pin", type=int, default=DEFAULT_DC_PIN, help="BCM GPIO for D/C.")
    parser.add_argument("--rst-pin", type=int, default=DEFAULT_RST_PIN, help="BCM GPIO for reset.")
    parser.add_argument(
        "--spi-speed",
        type=int,
        default=SPI_SPEED_HZ,
        help="SPI clock in Hz. Lower values are more tolerant of messy wiring.",
    )
    parser.add_argument(
        "--no-rotate",
        action="store_true",
        help="Disable the default 180-degree rotation.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    oled = SSD1309(
        dc_pin=args.dc_pin,
        rst_pin=args.rst_pin,
        spi_speed_hz=args.spi_speed,
        rotate_180=not args.no_rotate,
    )

    try:
        oled.init()
        oled.clear()
        oled.show(build_frame(args.text))

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        oled.cleanup()


if __name__ == "__main__":
    main()
