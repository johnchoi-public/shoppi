#!/usr/bin/env bash
# Release the current branch to the running service:
#   pull -> install -> migrate -> build the UI -> restart -> wait until it answers.
#
#   ./scripts/deploy.sh             # SKIP_PULL=1 to deploy the working tree as is
#
# Run it from the app's Python environment (or set SHOPPI_PYTHON). Restart uses sudo.
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${SHOPPI_PYTHON:-$(command -v python)}"
PORT="${SHOPPI_PORT:-8000}"

if [ -z "${SKIP_PULL:-}" ]; then
  git pull --ff-only
fi
"$PYTHON" -m pip install -q -e .
"$PYTHON" scripts/migrate.py
(cd web && npm ci && npm run build)

if systemctl list-unit-files shoppi.service >/dev/null 2>&1 && systemctl is-enabled shoppi >/dev/null 2>&1; then
  sudo systemctl restart shoppi
  for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "http://127.0.0.1:${PORT}/"; then
      echo "shoppi is up on 127.0.0.1:${PORT}."
      exit 0
    fi
    sleep 1
  done
  echo "shoppi did not answer within 30s; see: journalctl -u shoppi -n 50" >&2
  exit 1
else
  echo "Built and migrated. The service isn't installed yet: ./scripts/install_service.sh"
fi
