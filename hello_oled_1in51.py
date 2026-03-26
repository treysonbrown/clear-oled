#!/usr/bin/env python3

import time

import lgpio
import spidev
from PIL import Image, ImageDraw, ImageFont


WIDTH = 128
HEIGHT = 64

SPI_BUS = 0
SPI_DEVICE = 0

DC_PIN = 25
RST_PIN = 27


def chunk_bits(data, size):
    """Yield fixed-size chunks from a list of 1-bit pixels."""
    for index in range(0, len(data), size):
        yield data[index:index + size]


class SSD1309:
    def __init__(self):
        # Open SPI0 CE0 for the OLED data/command stream.
        self.spi = spidev.SpiDev()
        self.spi.open(SPI_BUS, SPI_DEVICE)
        self.spi.max_speed_hz = 10_000_000
        self.spi.mode = 0

        # Use lgpio for the D/C and reset control lines on Bookworm.
        self.gpio = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(self.gpio, DC_PIN)
        lgpio.gpio_claim_output(self.gpio, RST_PIN)

    def cleanup(self):
        self.spi.close()
        lgpio.gpiochip_close(self.gpio)

    def reset(self):
        # Hardware reset puts the controller into a known state.
        lgpio.gpio_write(self.gpio, RST_PIN, 1)
        time.sleep(0.05)
        lgpio.gpio_write(self.gpio, RST_PIN, 0)
        time.sleep(0.05)
        lgpio.gpio_write(self.gpio, RST_PIN, 1)
        time.sleep(0.05)

    def write_command(self, command):
        # D/C low means the next byte is a command.
        lgpio.gpio_write(self.gpio, DC_PIN, 0)
        self.spi.writebytes([command])

    def write_data(self, data):
        # D/C high means the following bytes are display RAM data.
        lgpio.gpio_write(self.gpio, DC_PIN, 1)
        self.spi.writebytes(list(data))

    def init(self):
        self.reset()

        init_sequence = [
            0xAE,  # display off
            0xD5, 0x80,  # clock divide
            0xA8, 0x3F,  # multiplex ratio = 63
            0xD3, 0x00,  # display offset
            0x40,  # start line = 0
            0x8D, 0x14,  # enable charge pump
            0x20, 0x00,  # horizontal addressing mode
            0xA1,  # segment remap
            0xC8,  # COM scan direction remapped
            0xDA, 0x12,  # COM pins hardware config
            0x81, 0x7F,  # contrast
            0xD9, 0xF1,  # pre-charge
            0xDB, 0x40,  # VCOMH deselect level
            0xA4,  # resume RAM display
            0xA6,  # normal display
            0xAF,  # display on
        ]

        for command in init_sequence:
            self.write_command(command)

    def clear(self):
        self.show(Image.new("1", (WIDTH, HEIGHT), 255))

    def show(self, image):
        # Convert the Pillow image into page-oriented bytes for SSD1309.
        image = image.convert("1")
        pixels = list(image.getdata())
        pages = []

        for page in range(HEIGHT // 8):
            for x in range(WIDTH):
                value = 0
                for bit in range(8):
                    y = page * 8 + bit
                    if pixels[y * WIDTH + x] == 0:
                        value |= 1 << bit
                pages.append(value)

        self.write_command(0x21)  # set column address
        self.write_command(0x00)
        self.write_command(WIDTH - 1)
        self.write_command(0x22)  # set page address
        self.write_command(0x00)
        self.write_command((HEIGHT // 8) - 1)

        for block in chunk_bits(pages, 4096):
            self.write_data(block)


def main():
    oled = SSD1309()

    try:
        oled.init()
        oled.clear()

        # Build a simple monochrome frame that only shows HELLO.
        image = Image.new("1", (WIDTH, HEIGHT), 255)
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()

        text = "HELLO"
        text_box = draw.textbbox((0, 0), text, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        text_x = (WIDTH - text_width) // 2
        text_y = (HEIGHT - text_height) // 2
        draw.text((text_x, text_y), text, font=font, fill=0)

        oled.show(image)

        # Keep the frame visible until the program is stopped.
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        oled.cleanup()


if __name__ == "__main__":
    main()
