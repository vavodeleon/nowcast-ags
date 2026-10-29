#!/usr/bin/env bash
# Una pasada del nowcast en el servidor: sincroniza, corre y publica.
set -uo pipefail

# El script deduce donde vive a partir de su propia ruta. La version
# anterior caia en $HOME/nowcast-ags, que solo es correcto si el proyecto
# esta en el home: con DESTINO en un disco externo, systemd lo arrancaba y
# fallaba el cd. El instalador sustituye marcadores en el .service pero no
# aqui, asi que este archivo no puede depender de eso.
AQUI="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
DESTINO="${NOWCAST_RAIZ:-${DESTINO:-$AQUI}}"

# Este script hace 'git pull' del repositorio en el que el propio script
# vive. Bash NO lee el archivo entero de golpe: lo va leyendo conforme lo
# ejecuta. Si el pull trae una version nueva de este mismo archivo a mitad
# de camino, bash sigue leyendo desde el desplazamiento anterior y ejecuta
# fragmentos partidos. Paso de verdad: dio un "Cannot rebase onto multiple
# branches" que no tenia nada que ver con git.
# La solucion es correr desde una copia en /tmp, intocable por el pull.
if [ -z "${NOWCAST_REEJECUTADO:-}" ]; then
  COPIA="$(mktemp /tmp/nowcast-correr.XXXXXX.sh)"
  cat "$(readlink -f "$0")" > "$COPIA"
  export NOWCAST_REEJECUTADO=1
  export NOWCAST_RAIZ="$DESTINO"
  bash "$COPIA"
  codigo=$?
  rm -f "$COPIA"
  exit $codigo
fi
if ! cd "$DESTINO"; then
  echo "ERROR: no existe $DESTINO" >&2
  exit 3
fi

# ------------------------------------------------------------ vigilancia
# Del 28 al 29/10/2026 el Pi pasó doce horas calculando sin poder publicar
# -un reinicio a mitad de commit dejó git roto- y nadie se enteró hasta que
# Álvaro vio "lleva 547 min sin publicar" en la página. Lo que sigue existe
# para que eso se repare solo, y si no se puede, para que avise.
#
# El estado va FUERA del repositorio: data/ y docs/ se publican.
VIG="${XDG_STATE_HOME:-$HOME/.local/state}/nowcast"
mkdir -p "$VIG"

# Un aviso al canal de Álvaro. El nombre del canal es la llave: no se
# imprime nunca, ni en un error (por eso curl va callado).
avisar() {
  echo "AVISO: $1" >&2
  [ -n "${NTFY_TOPIC:-}" ] || return 1
  command -v curl >/dev/null 2>&1 || return 1
  curl -fsS -m 15 -H "Title: Nowcast: el Pi" -H "Tags: warning" \
       -d "$1" "${NTFY_SERVER:-https://ntfy.sh}/${NTFY_TOPIC}" >/dev/null 2>&1
}

# 1. Que git mande cada objeto al disco antes de seguir. Sin esto, un corte
#    de luz deja objetos vacíos: es exactamente lo que pasó el 28/10.
git config core.fsync all 2>/dev/null || true

# 2. La alimentación. El 28/10 a las 23:46 y el 29/10 a las 11:08 el Pi se
#    reinició de golpe, las dos veces con disco y procesador ocupados. El
#    marcador de bajo voltaje se borra al reiniciar, así que hay que leerlo
#    ANTES: en cada corrida, y avisar la primera vez que cambia.
if command -v vcgencmd >/dev/null 2>&1; then
  t="$(vcgencmd get_throttled 2>/dev/null | sed 's/.*=//')"
  case "$t" in
    0x*)
      echo "alimentación: throttled=$t"
      if [ "$t" = "0x0" ]; then
        echo "0x0" > "$VIG/throttled"
      elif [ "$t" != "$(cat "$VIG/throttled" 2>/dev/null)" ]; then
        que=""
        if (( t & 0x1 ));     then que="bajo voltaje AHORA"; fi
        if (( t & 0x10000 )); then que="${que:+$que; }hubo bajo voltaje desde el arranque"; fi
        if (( t & 0x8 ));     then que="${que:+$que; }limitado por temperatura AHORA"; fi
        if (( t & 0x80000 )); then que="${que:+$que; }hubo límite por temperatura"; fi
        avisar "Alimentación del Pi: ${que:-marcador $t} (throttled=$t). Con bajo voltaje se reinicia y puede dañar lo que esté escribiendo. Un Pi 3 con disco USB necesita fuente de 5.1 V / 2.5 A y cable corto." \
          && echo "$t" > "$VIG/throttled"
      fi
      ;;
  esac
fi

# 3. Reparar lo que haya dejado un apagón: objetos vacíos, candados, un
#    rebase a medias. No toca data/ ni docs/. Ver deploy/reparar-git.sh.
reparacion="$(bash "$DESTINO/deploy/reparar-git.sh" "$DESTINO" 2>&1)"
estado_rep=$?
[ -n "$reparacion" ] && echo "$reparacion"
if [ "$estado_rep" != 0 ]; then
  avisar "El repositorio del Pi está dañado y no se pudo reparar solo. Los datos siguen en disco; hace falta mano."
elif printf '%s' "$reparacion" | grep -q "^reparado"; then
  avisar "Se reparó solo el repositorio del Pi (lo que deja un apagón). Los datos no se tocaron."
fi

# El repo tiene dos autores: este servidor y, ocasionalmente, tú desde el Mac.
# --autostash evita que un archivo a medias bloquee la sincronización.
git pull --rebase --autostash origin main || true

"$DESTINO/.venv/bin/python" -m nowcast.run
codigo=$?

fallo=""
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
    fallo="falta GITHUB_TOKEN"
  else
    AYUDANTE='!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f'
    for intento in 1 2 3; do
      git pull --rebase --autostash origin main \
        && git -c credential.helper= -c credential.helper="$AYUDANTE" \
               push -q origin HEAD:main \
        && { echo "publicado"; break; }
      if [ "$intento" = 3 ]; then
        fallo="git no pudo subir tras 3 intentos"
      else
        sleep 5
      fi
    done
  fi
fi

# 4. ¿Cuánto lleva sin publicar? Un fallo suelto no es noticia (la red se
#    cae un rato); una hora sí. Se avisa una vez al pasar la hora, y otra
#    cuando se recupera, para saber cuánto duró.
ahora_s=$(date +%s)
if [ -n "$fallo" ]; then
  [ -f "$VIG/fallando_desde" ] || echo "$ahora_s" > "$VIG/fallando_desde"
  min=$(( (ahora_s - $(cat "$VIG/fallando_desde")) / 60 ))
  echo "ERROR: $fallo; el pronostico se calculo pero no se publica (sin publicar desde hace $min min)" >&2
  if [ "$min" -ge 60 ] && [ ! -f "$VIG/avisado" ]; then
    avisar "El Pi lleva $min min sin poder publicar ($fallo). Sigue calculando y guardando; la página y la app no se actualizan. Diagnóstico: journalctl -u nowcast.service -n 40" \
      && touch "$VIG/avisado"
  fi
  exit 5
fi
if [ -f "$VIG/fallando_desde" ]; then
  min=$(( (ahora_s - $(cat "$VIG/fallando_desde")) / 60 ))
  if [ -f "$VIG/avisado" ] || [ "$min" -ge 60 ]; then
    avisar "El Pi volvió a publicar, tras $min min sin poder." || true
  fi
  rm -f "$VIG/fallando_desde" "$VIG/avisado"
fi

exit $codigo
