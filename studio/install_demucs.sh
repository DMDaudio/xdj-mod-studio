#!/bin/bash
# Installs Demucs (+ PyTorch, ~2-3 GB) into its own environment so it can
# never break the emulator's .venv. Run on Wi-Fi:  bash studio/install_demucs.sh
set -e
ENV="$HOME/.xdj700-studio/venv"
PYBIN=$(command -v python3.12 || command -v python3.11 || true)
if [ -z "$PYBIN" ]; then
  echo "Needs Python 3.11 or 3.12 (PyTorch does not support newer ones yet)."
  echo "Install it with:  brew install python@3.12   then run this again."
  exit 1
fi
mkdir -p "$(dirname "$ENV")"
"$PYBIN" -m venv "$ENV"
"$ENV/bin/pip" install --upgrade pip
"$ENV/bin/pip" install torch torchaudio demucs soundfile
"$ENV/bin/python" -c "import demucs; print('Demucs ready')"
