    return self._loop.run_until_complete(task)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^
  File "/usr/lib/python3.13/asyncio/base_events.py", line 725, in run_until_complete
    return future.result()
           ~~~~~~~~~~~~~^^
  File "/home/treyson/clear-oled/translate_input_oled.py", line 132, in run_remote
    oled = OLEDDisplay(rotate=args.rotate, dc_pin=args.dc_pin, rst_pin=args.rst_pin)
  File "/home/treyson/clear-oled/oled_display.py", line 283, in __init__
    driver_class, backend_name = resolve_driver_class()
                                 ~~~~~~~~~~~~~~~~~~~~^^
  File "/home/treyson/clear-oled/oled_display.py", line 90, in resolve_driver_class
    raise RuntimeError(
    ...<8 lines>...
    )
RuntimeError: No supported OLED driver found.
Preferred path: download Waveshare's OLED_Module_Code package and place its RaspberryPi/python/lib directory at ./lib in this repo.
Fallback path on the Raspberry Pi: install the maintained package and its hardware dependencies with `python3 -m pip install waveshare-transparent-oled pillow adafruit-blinka`.
Current interpreter: /home/treyson/clear-oled/.venv/bin/python3
Driver import details:
- waveshare_OLED: ModuleNotFoundError: No module named 'smbus'
- waveshare_transparent_oled: ModuleNotFoundError: No module named 'lgpio'
