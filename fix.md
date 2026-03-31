# Pi OLED Remote Mode Fix

This fix is for the error where `translate_input_oled.py --backend remote ...` fails with:

- `No module named 'smbus'`
- `No module named 'lgpio'`
- `No supported OLED driver found`

The root cause is that remote mode still initializes the OLED on the Pi, so the Pi still needs the OLED driver and Pi hardware Python packages.

## 1. Install the Pi hardware packages

Run on the Raspberry Pi:

```bash
sudo apt-get update
sudo apt-get install -y python3-pil python3-lgpio python3-spidev python3-smbus
```

## 2. Restore the Waveshare OLED Python library

If you already downloaded the Waveshare package on the Pi:

```bash
cd /home/treyson/clear-oled
mkdir -p lib
cp -R /path/to/OLED_Module_Code/RaspberryPi/python/lib/waveshare_OLED lib/
```

If you do not have it on the Pi yet:

```bash
cd /home/treyson
sudo apt-get install -y p7zip-full
wget https://files.waveshare.com/upload/2/2c/OLED_Module_Code.7z
7z x OLED_Module_Code.7z
cd /home/treyson/clear-oled
mkdir -p lib
cp -R /home/treyson/OLED_Module_Code/RaspberryPi/python/lib/waveshare_OLED lib/
```

## 3. Verify the Pi can still drive the OLED

Use system Python, not an isolated virtualenv:

```bash
cd /home/treyson/clear-oled
python3 -c "import smbus, lgpio, spidev; print('hardware imports ok')"
python3 hello_oled_1in51.py --text hello
```

Expected result:

- the import command prints `hardware imports ok`
- the OLED shows `hello`

If this does not work, fix that first before testing remote mode.

## 4. Install the lightweight remote-mode Python packages

```bash
cd /home/treyson/clear-oled
python3 -m pip install -r requirements-pi.txt
```

## 5. Test remote translation without the camera

Replace the hostname and token with your real values:

```bash
cd /home/treyson/clear-oled
python3 translate_input_oled.py \
  --backend remote \
  --remote-url ws://YOUR-MACBOOK-HOSTNAME.local:8765 \
  --token change-me \
  --text "猫"
```

If `.local` does not work, use the Mac's LAN IP instead:

```bash
python3 translate_input_oled.py \
  --backend remote \
  --remote-url ws://192.168.x.x:8765 \
  --token change-me \
  --text "猫"
```

## 6. Important note about virtualenvs on the Pi

Do not use a plain venv for the Pi hardware path unless it includes system packages.

If you want a venv on the Pi, create it like this:

```bash
cd /home/treyson/clear-oled
python3 -m venv --system-site-packages .venv-pi
source .venv-pi/bin/activate
python -m pip install -r requirements-pi.txt
python hello_oled_1in51.py --text hello
```

Then run remote mode inside that venv:

```bash
python translate_input_oled.py \
  --backend remote \
  --remote-url ws://YOUR-MACBOOK-HOSTNAME.local:8765 \
  --token change-me \
  --text "猫"
```

## 7. If it still fails

Run these two commands on the Pi and inspect the output:

```bash
python3 -c "import smbus, lgpio, spidev; print('hardware imports ok')"
python3 hello_oled_1in51.py --text hello
```

If either one fails, the problem is still the local OLED setup on the Pi, not the MacBook server connection.
