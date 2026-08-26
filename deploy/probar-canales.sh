#!/usr/bin/env bash
# Manda una notificacion de prueba a cada canal configurado.
#
#   bash deploy/probar-canales.sh
#
# Util despues de cambiar ~/.nowcast.env, o cuando alguien se suscribe a un
# canal nuevo y quiere confirmar que le llega. No toca nada del sistema:
# solo envia dos mensajes.
set -uo pipefail

AQUI="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
ENTORNO="${ENTORNO:-$HOME/.nowcast.env}"

if [ ! -f "$ENTORNO" ]; then
  echo "No encuentro $ENTORNO" >&2
  exit 1
fi

# 'set -a' exporta todo lo que se defina a continuacion, que es lo que el
# codigo espera encontrar en el entorno. Fuera de systemd nadie lo hace.
set -a
# shellcheck disable=SC1090
. "$ENTORNO"
set +a

exec "$AQUI/.venv/bin/python" - <<'PY'
from nowcast import config, notify

CANALES = [
    ("lluvia",  config.NTFY_TOPIC,
     "Prueba del canal de lluvia",
     "Si ves esto, aqui llegaran los avisos de lluvia que se acerca.",
     "cloud_with_rain"),
    ("presion", config.NTFY_TOPIC_SALUD,
     "Prueba del canal de presion",
     "Si ves esto, aqui llegaran los avisos de caida de presion barometrica.",
     "chart_with_downwards_trend"),
]

fallo = False
enviados = 0
for nombre, topic, titulo, cuerpo, tag in CANALES:
    if not topic:
        print(f"  {nombre:8s} sin configurar (vacio en el archivo de entorno)")
        continue
    # Nunca imprimir el nombre del canal: en ntfy el nombre ES la llave, y
    # esta salida acaba pegada en chats y capturas de pantalla.
    ok = notify.send(titulo, cuerpo, topic=topic, tags=tag)
    print(f"  {nombre:8s} {'enviado' if ok else 'FALLO AL ENVIAR'}")
    fallo = fallo or not ok
    enviados += int(ok)

print()
if fallo:
    print("Algo no salio. Revisa la conexion y el archivo de entorno.")
elif not enviados:
    print("No se envio nada: no hay ningun canal configurado.")
    raise SystemExit(1)
else:
    print("Listo. Lo que no llegue al telefono es cosa de la suscripcion:")
    print("el nombre del canal en la app tiene que coincidir exactamente.")
raise SystemExit(1 if fallo else 0)
PY
