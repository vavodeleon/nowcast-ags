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

azul "1/6  Paquetes del sistema"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git \
     libhdf5-dev pkg-config build-essential

# Con 1 GB de RAM (instancia AMD micro) pip se queda sin memoria al compilar.
# Un poco de swap lo resuelve y no estorba en las máquinas grandes.
if [ ! -f /swapfile ] && [ "$(free -m | awk '/^Mem:/{print $2}')" -lt 2000 ]; then
  azul "     poca RAM detectada: añadiendo 2 GB de swap"
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

azul "2/6  Código"
if [ -d "$DESTINO/.git" ]; then
  git -C "$DESTINO" pull --rebase --autostash
else
  git clone --depth 50 "$REPO_URL" "$DESTINO"
fi

azul "3/6  Entorno de Python"
python3 -m venv "$DESTINO/.venv"
"$DESTINO/.venv/bin/pip" install --quiet --upgrade pip
"$DESTINO/.venv/bin/pip" install --quiet -r "$DESTINO/requirements.txt"

azul "4/6  Comprobando que el motor funciona"
cd "$DESTINO"
if ! "$DESTINO/.venv/bin/python" selftest.py >/dev/null 2>&1; then
  rojo "     las pruebas del motor fallaron; revisa antes de seguir"
  exit 1
fi
verde "     motor correcto"

azul "5/6  Credenciales"
if [ ! -f "$ENTORNO" ]; then
  cp "$DESTINO/deploy/entorno.ejemplo" "$ENTORNO"
  chmod 600 "$ENTORNO"
  rojo "     Falta rellenar $ENTORNO con tus claves."
  rojo "     Ábrelo con:  nano $ENTORNO"
else
  verde "     $ENTORNO ya existe, no se toca"
fi

azul "6/6  Programando cada 15 minutos con systemd"
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
