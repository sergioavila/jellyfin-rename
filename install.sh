#!/bin/sh
# Install jellyfin_rename.py on a CasaOS/Jellyfin host.
#   sudo ./install.sh
#   sudo MEDIA=/data/media ./install.sh
set -e

if [ "$(id -u)" -ne 0 ]; then
  echo "Corre esto con sudo (muchas carpetas de Media son de root)." >&2
  exit 1
fi

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SRC="$HERE/jellyfin_rename.py"
MEDIA="${MEDIA:-/DATA/Media}"
PREFIX="${PREFIX:-/DATA/AppData/jellyfin-rename}"
PY=$(command -v python3) || {
  echo "Falta python3 en el host." >&2
  exit 1
}

if [ ! -f "$SRC" ]; then
  echo "No encuentro jellyfin_rename.py junto a install.sh." >&2
  exit 1
fi
if [ ! -d "$MEDIA" ]; then
  echo "No existe $MEDIA. Ejemplo: sudo MEDIA=/ruta/Media ./install.sh" >&2
  exit 1
fi

mkdir -p "$PREFIX"
cp "$SRC" "$PREFIX/jellyfin_rename.py"
chmod 755 "$PREFIX/jellyfin_rename.py"
"$PY" "$PREFIX/jellyfin_rename.py" --self-test

echo
echo "Dry-run (no renombra):"
"$PY" "$PREFIX/jellyfin_rename.py" "$MEDIA" | tail -n 8
echo

# Scheduled --apply lives in the web panel behind an explicit opt-in.
crontab -l 2>/dev/null | grep -v jellyfin_rename.py | crontab - || true

echo "Instalado en $PREFIX"
echo "Scheduled renaming is off until you enable it in the panel."
echo "Dry-run: sudo $PY $PREFIX/jellyfin_rename.py $MEDIA"
echo "Apply:   sudo $PY $PREFIX/jellyfin_rename.py $MEDIA --apply --log $PREFIX/log.jsonl"
