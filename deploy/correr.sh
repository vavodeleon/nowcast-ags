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

  # Autenticacion del push. En GitHub Actions la configuraba el runner; aqui
  # hay que darsela nosotros. El token se entrega por un ayudante de
  # credenciales que lo lee del entorno en el momento, para que NUNCA acabe
  # ni en .git/config -que se queda en disco- ni en la linea de comandos
  # -visible para cualquiera con 'ps'-.
  if [ -z "${GITHUB_TOKEN:-}" ]; then
    echo "ERROR: falta GITHUB_TOKEN; el pronostico se calculo pero no se publica" >&2
    exit 4
  fi
  AYUDANTE='!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f'

  for intento in 1 2 3; do
    git pull --rebase --autostash origin main \
      && git -c credential.helper= -c credential.helper="$AYUDANTE" \
             push -q origin HEAD:main \
      && { echo "publicado"; break; }
    if [ "$intento" = 3 ]; then
      echo "ERROR: no se pudo publicar tras 3 intentos" >&2
      exit 5
    fi
    sleep 5
  done
fi

exit $codigo
