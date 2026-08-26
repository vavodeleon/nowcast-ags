#!/usr/bin/env bash
# Instalador para un servidor Ubuntu (Oracle Cloud capa gratuita).
#
# Deja el nowcast corriendo cada 15 minutos de verdad, con systemd, que sí
# cumple horarios. Es idempotente: se puede volver a ejecutar sin romper nada.
#
#   bash instalar.sh
#
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/vavodeleon/nowcast-ags.git}"
DESTINO="${DESTINO:-$HOME/nowcast-ags}"
ENTORNO="$HOME/.nowcast.env"

azul()  { printf "\033[1;34m%s\033[0m\n" "$*"; }
verde() { printf "\033[1;32m%s\033[0m\n" "$*"; }
rojo()  { printf "\033[1;31m%s\033[0m\n" "$*"; }

azul "0/7  Reconociendo la máquina"
MODELO="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo desconocida)"
ARQ="$(uname -m)"
RAM_MB="$(free -m | awk '/^Mem:/{print $2}')"
echo "     $MODELO"
echo "     arquitectura $ARQ · $RAM_MB MB de RAM"

if echo "$MODELO" | grep -qi raspberry; then
  verde "     Raspberry Pi detectada: se aplicarán límites para convivir"
  verde "     con otros servicios (LoRa) sin quitarles CPU ni disco."
  if [ "$ARQ" = "armv7l" ]; then
    rojo ""
    rojo "     AVISO: estás en Raspberry Pi OS de 32 bits."
    rojo "     scipy y h5py tardarán muchísimo en compilar y algunos"
    rojo "     paquetes no traen versión lista. Se recomienda reinstalar"
    rojo "     con la versión de 64 bits, donde todo llega precompilado."
    rojo ""
    read -r -p "     ¿Continuar de todas formas? [s/N] " seguir
    [ "${seguir:-n}" = "s" ] || exit 1
  fi
  echo ""
  echo "     Nota sobre la tarjeta SD: el sistema escribe ~96 commits al día."
  echo "     Las SD se desgastan con la escritura. Si el Pi va a estar años"
  echo "     con esto, considera arrancar desde un SSD por USB."
  echo ""
fi

azul "1/7  Paquetes del sistema"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git \
     libhdf5-dev pkg-config build-essential

azul "2/7  Dónde va a vivir el proyecto"
PADRE="$(dirname "$DESTINO")"
mkdir -p "$PADRE"
FS="$(df -T "$PADRE" | awk 'NR==2{print $2}')"
MONTAJE="$(df "$PADRE" | awk 'NR==2{print $NF}')"
echo "     $DESTINO"
echo "     sistema de archivos: $FS  ·  montado en: $MONTAJE"

case "$FS" in
  vfat|exfat|fuseblk|ntfs)
    rojo ""
    rojo "     PROBLEMA: $FS no sirve para un repositorio de git."
    rojo "     No guarda permisos de archivo ni enlaces simbólicos, y git"
    rojo "     se corrompe de formas raras y difíciles de diagnosticar."
    rojo "     Formatea el disco externo como ext4 antes de seguir."
    rojo "     Instrucciones en deploy/README.md"
    exit 1 ;;
esac

# Un disco duro mecanico por USB puede pedir ~0.9 A al arrancar el plato, y
# los puertos USB del Pi comparten un fusible de ~1.1 A. La consecuencia no es
# que no arranque, sino bajones de voltaje intermitentes que desconectan el
# disco a media escritura y corrompen el repositorio.
if [ "$MONTAJE" != "/" ] && command -v vcgencmd >/dev/null 2>&1; then
  ESTADO="$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)"
  echo "     alimentación: $ESTADO"
  if [ "$ESTADO" != "0x0" ] && [ -n "$ESTADO" ]; then
    rojo ""
    rojo "     AVISO: el Pi reporta problemas de alimentación ($ESTADO)."
    rojo "     Si el disco externo se alimenta por USB, es casi seguro la causa."
    rojo "     Un bajón mientras git escribe deja el repositorio corrupto."
    rojo "     Solución: disco de 3.5\" con su propia corriente, o un hub USB"
    rojo "     alimentado. Detalles en deploy/README.md"
    rojo ""
    read -r -p "     ¿Continuar de todas formas? [s/N] " seguir
    [ "${seguir:-n}" = "s" ] || exit 1
  else
    verde "     alimentación estable"
  fi

  # Un disco que se duerme hara ~96 arranques de plato al dia. Los ciclos de
  # aparcado son lo que desgasta un HDD; girando constante dura mas.
  DISCO="$(df "$PADRE" | awk 'NR==2{print $1}' | sed 's/[0-9]*$//')"
  if [ -b "$DISCO" ] && [ "$(cat "/sys/block/$(basename "$DISCO")/queue/rotational" 2>/dev/null)" = "1" ]; then
    echo "     $DISCO es un disco mecánico"
    if command -v hdparm >/dev/null 2>&1; then
      sudo hdparm -S 0 -B 255 "$DISCO" >/dev/null 2>&1 \
        && verde "     apagado automático desactivado (menos ciclos de arranque)" \
        || echo "     no se pudo ajustar hdparm; no es grave"
    else
      echo "     instala hdparm para evitar que pare y arranque cada 15 min:"
      echo "       sudo apt-get install -y hdparm && sudo hdparm -S 0 -B 255 $DISCO"
    fi
  fi
fi

if [ "$MONTAJE" = "/" ]; then
  echo ""
  echo "     Aviso: el proyecto quedará en la misma tarjeta que el sistema."
  echo "     Son ~96 escrituras de git al día. Para no desgastar la SD,"
  echo "     puedes reinstalar apuntando a un disco externo:"
  echo "       DESTINO=/mnt/datos/nowcast-ags bash instalar.sh"
  echo ""
else
  verde "     El proyecto vive fuera de la tarjeta SD: la SD casi no se escribirá."
fi

azul "3/7  Memoria"
# Con 1 GB de RAM pip se queda sin memoria al compilar. El swap lo resuelve.
if [ "$RAM_MB" -lt 2000 ]; then
  # El swap se pone junto al proyecto. Si DESTINO esta en un disco externo,
  # el swap tambien: escribir swap en la tarjeta SD es la forma mas rapida
  # de matarla.
  DISCO_SWAP="$(dirname "$DESTINO")"
  ARCHIVO_SWAP="$DISCO_SWAP/.swapfile"
  # OJO: no basta con preguntar si HAY swap. Raspberry Pi OS trae 100 MB
  # por omision, que es inservible para compilar. Hay que mirar el TAMAÑO.
  SWAP_MB="$(free -m | awk '/^Swap:/{print $2}')"
  echo "     swap actual: ${SWAP_MB} MB"
  if [ "${SWAP_MB:-0}" -lt 1024 ]; then
    azul "     poca RAM: añadiendo 2 GB de swap en $DISCO_SWAP"
    sudo fallocate -l 2G "$ARCHIVO_SWAP"
    sudo chmod 600 "$ARCHIVO_SWAP"
    sudo mkswap "$ARCHIVO_SWAP" >/dev/null
    sudo swapon "$ARCHIVO_SWAP"
    echo "$ARCHIVO_SWAP none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
  else
    verde "     swap suficiente, no se toca"
  fi
fi

azul "4/7  Código"
if [ -d "$DESTINO/.git" ]; then
  git -C "$DESTINO" pull --rebase --autostash
else
  git clone --depth 50 "$REPO_URL" "$DESTINO"
fi

azul "5/7  Entorno de Python"
python3 -m venv "$DESTINO/.venv"
"$DESTINO/.venv/bin/pip" install --quiet --upgrade pip
"$DESTINO/.venv/bin/pip" install --quiet -r "$DESTINO/requirements.txt"

azul "6/7  Comprobando que el motor funciona"
cd "$DESTINO"
if ! "$DESTINO/.venv/bin/python" selftest.py >/dev/null 2>&1; then
  rojo "     las pruebas del motor fallaron; revisa antes de seguir"
  exit 1
fi
verde "     motor correcto"

azul "7/7  Credenciales"
if [ ! -f "$ENTORNO" ]; then
  cp "$DESTINO/deploy/entorno.ejemplo" "$ENTORNO"
  chmod 600 "$ENTORNO"
  rojo "     Falta rellenar $ENTORNO con tus claves."
  rojo "     Ábrelo con:  nano $ENTORNO"
else
  verde "     $ENTORNO ya existe, no se toca"
fi

azul "      Programando cada 15 minutos con systemd"
sudo cp "$DESTINO/deploy/nowcast.service" /etc/systemd/system/
sudo cp "$DESTINO/deploy/nowcast.timer"   /etc/systemd/system/
sudo sed -i "s|__USUARIO__|$USER|g; s|__DESTINO__|$DESTINO|g; s|__ENTORNO__|$ENTORNO|g" \
     /etc/systemd/system/nowcast.service
sudo systemctl daemon-reload
sudo systemctl enable --now nowcast.timer

verde ""
verde "Listo. El sistema corre cada 15 minutos."
verde ""
echo "Comandos útiles:"
echo "  systemctl list-timers nowcast.timer     # cuándo toca la próxima"
echo "  journalctl -u nowcast -n 50 --no-pager  # qué hizo la última"
echo "  sudo systemctl start nowcast            # forzar una ahora"
echo ""
echo "Si acabas de crear $ENTORNO, rellénalo y luego:"
echo "  sudo systemctl start nowcast"
