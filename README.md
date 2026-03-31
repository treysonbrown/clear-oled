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

## 4. Choose a translation mode

This repo now supports two translation modes:

- `local`: OCR and translation run on the Raspberry Pi
- `remote`: the Pi captures images and drives the OLED, but OCR and translation run on another machine such as your MacBook

Use `local` when the Pi has enough storage for Tesseract and Argos. Use `remote` when you want the Pi to stay lightweight.

## 5. Remote MacBook mode

This is the intended path when the Pi SD card is too small for the OCR or translation dependencies.

### 5.1 Install the lightweight Pi dependencies

On the Raspberry Pi:

```bash
cd /path/to/clear-oled
python3 -m pip install -r requirements-pi.txt
```

The Pi still needs the OLED driver from section 2 and the camera command (`rpicam-still` or `libcamera-still`), but it no longer needs Argos or Tesseract in remote mode.

### 5.2 Install the server dependencies on the MacBook

On the MacBook:

```bash
cd /path/to/clear-oled
python3 -m pip install -r requirements-server.txt
```

The server host also needs Tesseract with Japanese language data installed through the local OS package manager.

Then install a Japanese -> English Argos translation package on the MacBook:

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

### 5.3 Start the WebSocket translation server

On the MacBook:

```bash
python3 translation_server_ws.py --token change-me --debug
```

By default the server listens on `0.0.0.0:8765`.

### 5.4 Validate remote translation without the camera

On the Raspberry Pi:

```bash
python3 translate_input_oled.py \
  --backend remote \
  --remote-url ws://YOUR-MACBOOK-HOSTNAME.local:8765 \
  --token change-me
```

Or translate one phrase and exit:

```bash
python3 translate_input_oled.py \
  --backend remote \
  --remote-url ws://YOUR-MACBOOK-HOSTNAME.local:8765 \
  --token change-me \
  --text "猫"
```

### 5.5 Run the remote camera pipeline

Once the camera module is installed on the Pi:

```bash
python3 translate_camera_oled.py \
  --backend remote \
  --remote-url ws://YOUR-MACBOOK-HOSTNAME.local:8765 \
  --token change-me \
  --debug
```

This runtime captures frames on the Pi, center-crops them, sends them to the MacBook over WebSocket, and updates the OLED only when the server returns a new stable translation.

If the MacBook is offline or unreachable, the Pi will show `SERVER DOWN` on the OLED and keep retrying.

## 6. Local Pi-only mode

If you want everything to run directly on the Pi, install the heavier dependencies there instead.

### 6.1 Manual translation without the camera

```bash
python3 -m pip install pillow argostranslate
```

Then install a Japanese -> English Argos package:

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
python3 translate_input_oled.py --text "猫"
```

### 6.2 Camera translation on the Pi

Install local OCR on the Pi:

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-jpn
```

Then run the local camera pipeline:

```bash
python3 translate_camera_oled.py --backend local --debug
```

This keeps the original behavior: the Pi captures frames, runs OCR locally, translates locally, and pushes the newest English result to the OLED.

## 7. Acceptance checks

After the C demo works, the Python path is considered correct when:

- `hello` renders centered and legible
- clearing the display produces a blank panel
- `--rotate` and `--no-rotate` behave predictably
- remote manual translation works without Argos or Tesseract installed on the Pi
- remote camera translation recovers automatically after the MacBook server restarts
- local mode still works when running the scripts without `--backend remote`
