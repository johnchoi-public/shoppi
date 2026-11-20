#!/usr/bin/env bash
# Back up the shoppi database (pg_dump custom format) and uploaded documents.
#
#   ./scripts/backup.sh             # into ~/shoppi-backups, keeping the newest 14 of each
#
# Restore: pg_restore --clean --if-exists -d <owner url> shoppi-<stamp>.dump,
#          then python scripts/migrate.py; untar the uploads archive in the app directory.
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${SHOPPI_PYTHON:-$(command -v python)}"
DEST="${SHOPPI_BACKUP_DIR:-$HOME/shoppi-backups}"
KEEP="${SHOPPI_BACKUP_KEEP:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
chmod 700 "$DEST"

# Connection details from the app's own settings (.env); the password goes in
# PGPASSWORD rather than on the command line, where other users could see it.
eval "$("$PYTHON" - <<'PY'
import shlex
from sqlalchemy.engine import make_url
from shoppi.config import get_settings
u = make_url(get_settings().database_owner_url)
for k, v in {"PGHOST": u.host or "localhost", "PGPORT": u.port or 5432, "PGUSER": u.username,
             "PGPASSWORD": u.password or "", "PGDATABASE": u.database}.items():
    print(f"export {k}={shlex.quote(str(v))}")
PY
)"

pg_dump -Fc -f "$DEST/shoppi-$STAMP.dump"
UPLOADS="$("$PYTHON" -c 'from shoppi.config import get_settings; print(get_settings().upload_dir)')"
if [ -d "$UPLOADS" ]; then
  tar -czf "$DEST/uploads-$STAMP.tar.gz" "$UPLOADS"
fi

# Keep the newest $KEEP of each kind.
for pattern in 'shoppi-*.dump' 'uploads-*.tar.gz'; do
  find "$DEST" -maxdepth 1 -name "$pattern" -printf '%T@ %p\n' | sort -rn | tail -n +"$((KEEP + 1))" | cut -d' ' -f2- | xargs -r rm --
done
echo "Backup written to $DEST (shoppi-$STAMP.dump)"
