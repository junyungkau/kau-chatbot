#!/usr/bin/env bash
set -euo pipefail

cd /opt/kau-chatbot

sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  python3 \
  python3-venv \
  python3-pip \
  git \
  curl \
  nginx

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip setuptools wheel
python -m pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.6.0+cpu"
python -m pip install -r requirements.txt

echo "Setup complete. Next: configure /etc/kau-chatbot.env and start the systemd service."
