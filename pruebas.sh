#!/usr/bin/env bash
# Corre todas las pruebas. Sin red, sin hardware.
#
#   bash pruebas.sh
#
# El interprete se detecta solo: el venv del proyecto si existe, si no el
# python del sistema. Node es opcional; si no esta, se salta la prueba de la
# pagina avisando, en vez de fallar.
cd "$(cd "$(dirname "$0")" && pwd)"
PY="./.venv/bin/python"; [ -x "$PY" ] || PY="python3"

# Sin dependencias, once suites fallan con ModuleNotFoundError y la salida se
# lee como "el codigo esta roto" cuando lo que falta es el entorno. Son dos
# cosas distintas y tienen que verse distintas: un diagnostico que confunde
# "no puedo probarlo" con "esta mal" es peor que no tenerlo.
if ! "$PY" -c "import numpy, h5py, requests" 2>/dev/null; then
  echo "  No hay entorno de pruebas en esta máquina."
  "$PY" -c "import numpy" 2>/dev/null || echo "    falta numpy"
  "$PY" -c "import h5py"  2>/dev/null || echo "    falta h5py"
  "$PY" -c "import requests" 2>/dev/null || echo "    falta requests"
  echo
  echo "  Esto NO dice nada sobre el código. Para probarlo de verdad:"
  echo "    en el Pi:   cd /mnt/datos/nowcast-ags && bash pruebas.sh"
  echo "    en el Mac:  python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"
  echo
  echo "  Mientras tanto, las suites que no necesitan nada:"
  for t in test_archivo test_migracion test_malla test_workflow; do
    printf "  %-16s " "$t"
    if salida="$(CLIMA_DB="" "$PY" "$t.py" 2>&1)"; then
      echo "$(echo "$salida" | tail -1)"
    else
      echo "FALLÓ"; echo "$salida" | tail -20 | sed 's/^/      /'
    fi
  done
  exit 1
fi

# La suite no debe tocar nada de la maquina. En el Raspberry existe el
# barometro de la malla, y sin esto una prueba de presion sintetica acaba
# leyendo la presion real de la casa y fallando por motivos ajenos.
export CLIMA_DB=""

fallos=0
for t in selftest test_visual test_matutino test_rayos test_ahora \
         test_tormenta test_presion test_barometro test_archivo test_migracion test_salud \
         test_malla test_contrato test_presupuesto test_workflow; do
  printf "  %-16s " "$t"
  if salida="$("$PY" "$t.py" 2>&1)"; then
    echo "$(echo "$salida" | tail -1)"
  else
    echo "FALLÓ"; echo "$salida" | tail -20 | sed 's/^/      /'
    fallos=$((fallos+1))
  fi
done

printf "  %-16s " "test_pagina"
if command -v node >/dev/null 2>&1; then
  if salida="$(node test_pagina.js 2>&1)"; then
    echo "$(echo "$salida" | tail -1)"
  else
    echo "FALLÓ"; echo "$salida" | tail -20 | sed 's/^/      /'
    fallos=$((fallos+1))
  fi
else
  echo "omitida (node no instalado)"
fi

echo
[ "$fallos" -eq 0 ] && echo "TODO EN ORDEN" || echo "$fallos suite(s) con fallos"
exit $((fallos > 0))
