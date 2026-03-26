#!/usr/bin/python
# -*- coding:utf-8 -*-

import os
import sys

picdir = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "pic")
libdir = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "lib")
if os.path.exists(libdir):
    sys.path.append(libdir)

import logging
import time

from PIL import Image, ImageDraw, ImageFont
from waveshare_OLED import OLED_1in51


logging.basicConfig(level=logging.DEBUG)


try:
    disp = OLED_1in51.OLED_1in51()

    logging.info("\r1.51inch Transparent OLED")
    disp.Init()

    logging.info("clear display")
    disp.clear()

    image = Image.new("1", (disp.width, disp.height), "WHITE")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    text = "HELLO"
    text_box = draw.textbbox((0, 0), text, font=font)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    text_x = (disp.width - text_width) // 2
    text_y = (disp.height - text_height) // 2
    draw.text((text_x, text_y), text, font=font, fill=0)

    image = image.rotate(180)
    disp.ShowImage(disp.getbuffer(image))

    while True:
        time.sleep(1)

except IOError as e:
    logging.info(e)

except KeyboardInterrupt:
    logging.info("ctrl + c:")
    try:
        disp.module_exit()
    except NameError:
        pass
    exit()
