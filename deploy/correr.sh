#!/usr/bin/env bash
# Una pasada del nowcast en el servidor: sincroniza, corre y publica.
set -uo pipefail

# El script deduce donde vive a partir de su propia ruta. La version
# anterior caia en $HOME/nowcast-ags, que solo es correcto si el proyecto
# esta en el home: con DESTINO en un disco externo, systemd lo arrancaba y
# fallaba el cd. El instalador sustituye marcadores en el .service pero no
# aqui, asi que este archivo no puede depender de eso.
AQUI="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
DESTINO="${DESTINO:-$AQUI}"
if ! cd "$DESTINO"; then
  echo "ERROR: no existe $DESTINO" >&2
  exit 3
fi

# El repo tiene dos autores: este servidor y, ocasionalmente, tú desde el Mac.
# --autostash evita que un archivo a medias bloquee la sincronización.
git pull --rebase --autostash origin main || true

"$DESTINO/.venv/bin/python" -m nowcast.run
codigo=$?

git add data docs
if git diff --staged --quiet; then
  echo "sin cambios que publicar"
else
  git commit -q -m "nowcast $(date -u '+%Y-%m-%d %H:%M UTC')"
  for intento in 1 2 3; do
    git pull --rebase --autostash origin main && git push -q origin HEAD:main && break
    sleep 5
  done
fi

exit $codigo
