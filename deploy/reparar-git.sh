#!/usr/bin/env bash
# Repara el repositorio del Pi tras un apagado a mitad de escritura.
#
#   bash deploy/reparar-git.sh [directorio]
#
# Sale con 0 si estaba sano o quedó reparado (e imprime "reparado" en ese
# caso), y con 1 si no pudo. No toca NUNCA los archivos de trabajo: data/ y
# docs/ son la verdad, y git solo es el camino para publicarlos.
#
# Por qué existe. El 28/10/2026 a las 23:46 el Pi se reinició de golpe a mitad
# de un commit. Quedaron 14 objetos de git vacíos, el último commit entre
# ellos, y desde ahí cada corrida calculaba bien pero fallaba al subir:
# "object file ... is empty", "bad object HEAD". Doce horas sin publicar sin
# que nadie se enterara. La reparación a mano fue esta misma, en este orden.
#
# Lo que deja un apagón, y lo que se hace con cada cosa:
#   - objetos vacíos           -> se borran y se vuelve a lo que tiene GitHub
#   - candados (*.lock) viejos -> se quitan si nadie los está usando
#   - un rebase a medias       -> se aborta
set -uo pipefail
cd "${1:-.}" || exit 1
[ -d .git ] || { echo "no es un repositorio: $(pwd)" >&2; exit 1; }

tocado=0

# Candados de más de 10 minutos: ninguna operación de git de este proyecto
# dura eso, así que es de una corrida que murió. Con uno de estos, cada
# corrida siguiente falla con "Unable to create ... .lock: File exists".
for c in .git/index.lock .git/HEAD.lock .git/refs/heads/main.lock; do
  if [ -e "$c" ] && [ -n "$(find "$c" -mmin +10 2>/dev/null)" ] \
     && ! pgrep -x git >/dev/null 2>&1; then
    echo "quito candado abandonado: $c" >&2
    rm -f "$c"; tocado=1
  fi
done

# Un pull --rebase interrumpido deja el repositorio a medias.
for d in .git/rebase-merge .git/rebase-apply; do
  if [ -d "$d" ] && [ -n "$(find "$d" -maxdepth 0 -mmin +10 2>/dev/null)" ]; then
    echo "aborto un rebase a medias" >&2
    git rebase --abort >/dev/null 2>&1 || rm -rf "$d"
    tocado=1
  fi
done

# Objetos vacíos. Buscarlos es barato: git empaqueta solo los sueltos al
# pasar de ~6700, así que nunca hay muchos archivos que mirar.
vacios="$(find .git/objects -type f -empty 2>/dev/null)"
if [ -z "$vacios" ] && git status -uno --porcelain >/dev/null 2>&1; then
  [ "$tocado" = 1 ] && echo "reparado"
  exit 0
fi

n=$(printf '%s\n' "$vacios" | grep -c . || true)
echo "repositorio dañado ($n objetos vacíos); vuelvo a lo que tiene GitHub" >&2
[ -n "$vacios" ] && printf '%s\n' "$vacios" | xargs rm -f

# Primero la rama: mientras apunte a un objeto roto, hasta fetch falla.
git update-ref refs/heads/main refs/remotes/origin/main || exit 1
git fetch -q origin || exit 1
# El índice también puede citar objetos rotos: se reconstruye. El reset es
# MIXTO a propósito: mueve git, deja los archivos. Lo que se calculó desde
# el último push sigue en disco y sale en el siguiente commit.
rm -f .git/index
git reset -q origin/main || exit 1
git status -uno --porcelain >/dev/null 2>&1 || exit 1
echo "reparado"
