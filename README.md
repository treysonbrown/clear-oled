# clear-oled

Bring-up notes and a Python wrapper for the Waveshare 1.51-inch Transparent OLED.

## Wiring baseline

Use the official Waveshare SPI wiring first:

- `VCC -> 3.3V`
- `GND -> GND`
- `DIN -> GPIO10 / MOSI / pin 19`
- `CLK -> GPIO11 / SCLK / pin 23`
- `CS -> GPIO8 / CE0 / pin 24`
- `DC -> GPIO25 / pin 22`
- `RST -> GPIO27 / pin 13`

Before changing software again, inspect the module and confirm it is configured for `4-wire SPI`, not `I2C`.

## 1. Prove the display with the official Waveshare C demo

Run these commands on the Raspberry Pi that is physically attached to the OLED:

```bash
cat /etc/os-release
sudo raspi-config
```

Enable `Interface Options -> SPI`, reboot, then verify the SPI device exists:

```bash
ls -l /dev/spidev0.0
```

Download and run the official demo package from Waveshare:

```bash
sudo apt-get update
sudo apt-get install -y p7zip-full
wget https://files.waveshare.com/upload/2/2c/OLED_Module_Code.7z
7z x OLED_Module_Code.7z
cd OLED_Module_Code/RaspberryPi/c
sudo make clean
sudo make -j4
sudo ./main 1.51
```

Expected result: the display shows a stable, repeatable vendor demo image. If it still shows random pixels here, stop iterating on Python and fix hardware or module mode selection first.

Hardware debug order:

1. Confirm the SPI/I2C resistor or solder-bridge setting is on `SPI`.
2. Confirm the panel is powered from `3.3V`.
3. Confirm `MOSI`, `SCLK`, and `CE0` continuity.
4. Confirm `DC` is on `GPIO25` and `RST` is on `GPIO27`.
5. Confirm `/dev/spidev0.0` exists after enabling SPI.
6. Only after the above are confirmed, try a lower SPI clock in software.

## 2. Use Python only after the C demo works

This repo's [`hello_oled_1in51.py`](/Users/treyson/clear-oled/hello_oled_1in51.py) no longer contains a handwritten SSD1309 driver. It is a thin wrapper around a proven driver and prefers the official Waveshare Python module.

### Preferred backend: official Waveshare Python library

After downloading `OLED_Module_Code.7z`, copy the Raspberry Pi Python library into this repo:

```bash
cd /path/to/clear-oled
mkdir -p lib
cp -R /path/to/OLED_Module_Code/RaspberryPi/python/lib/waveshare_OLED lib/
```

Install the Python runtime dependencies on the Pi:

```bash
sudo apt-get install -y python3-pil python3-lgpio python3-spidev
```

Then run:

```bash
python3 hello_oled_1in51.py --text hello
```

### Fallback backend: maintained external package

If you do not want to use the vendor `lib/` folder, install the maintained package on the Pi:

```bash
python3 -m pip install waveshare-transparent-oled pillow
```

Then run the same script:

```bash
python3 hello_oled_1in51.py --text hello
```

## 3. Script usage

Default orientation matches the vendor sample and rotates the image 180 degrees before upload.

```bash
python3 hello_oled_1in51.py --text hello
python3 hello_oled_1in51.py --text hello --no-rotate
```

Optional GPIO overrides are only used if the selected backend supports them:

```bash
python3 hello_oled_1in51.py --text hello --dc-pin 25 --rst-pin 27
```

## 4. Manual translation test without the camera

If the OLED is working and you want to test Japanese -> English translation before the camera arrives, install a local translation backend on the Raspberry Pi.

Install the Python packages:

```bash
python3 -m pip install pillow argostranslate
```

Then install a Japanese -> English Argos translation package. One way is:

```bash
python3 - <<'PY'
import argostranslate.package
import argostranslate.translate

available = argostranslate.package.get_available_packages()
package = next(
    pkg for pkg in available
    if pkg.from_code == "ja" and pkg.to_code == "en"
)
path = package.download()
argostranslate.package.install_from_path(path)
PY
```

Now you can type Japanese into the terminal and have the OLED show the English translation:

```bash
python3 translate_input_oled.py
```

Or translate a single phrase and exit:

```bash
python3 translate_input_oled.py --text "猫"
```

## 5. Camera translation runtime

Once the camera module is installed, use the continuous pipeline:

```bash
python3 translate_camera_oled.py --debug
```

This runtime captures frames from `rpicam-still` or `libcamera-still`, runs Japanese OCR with Tesseract, translates stable results locally, and pushes the latest English translation to the OLED.

You will need local OCR installed on the Pi for this path:

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-jpn
```

## 6. Acceptance checks

After the C demo works, the Python path is considered correct when:

- `hello` renders centered and legible
- clearing the display produces a blank panel
- `--rotate` and `--no-rotate` behave predictably
- restarting the script several times does not change the output quality
- manual Japanese input produces English output on the OLED
