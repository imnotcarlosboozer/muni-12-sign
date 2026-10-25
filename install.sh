#!/bin/bash
# One-time setup on the Raspberry Pi. Run from this folder:  sudo ./install.sh
# Assumes the rpi-rgb-led-matrix Python bindings are installed (see sleeper-led-ticker's install.sh).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo ./install.sh"; exit 1
fi

APP_DIR="$(cd "$(dirname "$0")" && pwd)"

if ! python3 -c "import rgbmatrix" 2>/dev/null; then
  echo "rgbmatrix Python bindings not found; install rpi-rgb-led-matrix first."; exit 1
fi
if [[ ! -s "$APP_DIR/api_key.txt" ]]; then
  echo "Missing $APP_DIR/api_key.txt. Get a free key at https://511.org/open-data/token"; exit 1
fi
chmod 600 "$APP_DIR/api_key.txt"

if [[ -z "${LIB_DIR:-}" ]]; then
  for d in "$APP_DIR/../rpi-rgb-led-matrix" /opt/rpi-rgb-led-matrix; do
    if [[ -d "$d/fonts" ]]; then LIB_DIR="$(cd "$d" && pwd)"; break; fi
  done
fi
echo "==> Copying fonts from ${LIB_DIR:?no rpi-rgb-led-matrix checkout found}"
mkdir -p "$APP_DIR/fonts"
cp "$LIB_DIR"/fonts/*.bdf "$APP_DIR/fonts/"

# Only one program can drive the panels at a time.
if systemctl list-unit-files sleeper-ticker.service >/dev/null 2>&1; then
  echo "==> Disabling the injury ticker (sleeper-ticker)"
  systemctl disable --now sleeper-ticker.service || true
fi

echo "==> Installing systemd service"
sed "s#__APP_DIR__#$APP_DIR#g" "$APP_DIR/muni-sign.service" > /etc/systemd/system/muni-sign.service
systemctl daemon-reload
systemctl enable --now muni-sign.service

echo
echo "Done. Logs:  journalctl -u muni-sign -f"
