#!/usr/bin/env bash
# Install shoppi as a systemd service, plus the nightly backup timer.
#
#   ./scripts/install_service.sh
#
# Run it from the Python environment the app uses (conda activate shoppi, or
# source .venv/bin/activate), or point SHOPPI_PYTHON at that interpreter.
# The service runs as the current user from this checkout. Uses sudo.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${SHOPPI_PYTHON:-$(command -v python || true)}"
RUN_AS="$(id -un)"

if [ -z "$PYTHON" ] || ! "$PYTHON" -c "import shoppi, uvicorn" >/dev/null 2>&1; then
  echo "No Python with shoppi installed found ('${PYTHON:-python}')." >&2
  echo "Activate the app's environment first, or set SHOPPI_PYTHON=/path/to/python." >&2
  exit 1
fi
PYTHON="$(cd "$(dirname "$PYTHON")" && pwd)/$(basename "$PYTHON")"   # absolute, for systemd

for unit in shoppi.service shoppi-backup.service shoppi-backup.timer; do
  sed -e "s|@USER@|$RUN_AS|g" -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@PYTHON@|$PYTHON|g" \
    "$APP_DIR/deploy/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now shoppi.service shoppi-backup.timer
echo "Installed: shoppi.service (user $RUN_AS, $PYTHON) and shoppi-backup.timer."
echo "Logs: journalctl -u shoppi -f"
