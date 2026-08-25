#!/usr/bin/env bash
# Una pasada del nowcast en el servidor: sincroniza, corre y publica.
set -uo pipefail

DESTINO="${DESTINO:-$HOME/nowcast-ags}"
cd "$DESTINO" || exit 1

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
