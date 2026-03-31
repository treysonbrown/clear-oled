cd /home/treyson/clear-oled

deactivate 2>/dev/null || true
rm -rf .venv

sudo apt-get update
sudo apt-get install -y python3-pil python3-lgpio python3-spidev python3-smbus

python3 -m venv --system-site-packages .venv-pi
source .venv-pi/bin/activate

python -c "import sys; print(sys.executable)"
python -c "import smbus, lgpio, spidev; print('hardware imports ok')"

python -m pip install -r requirements-pi.txt

python hello_oled_1in51.py --text hello
