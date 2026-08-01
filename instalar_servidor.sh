#!/usr/bin/env bash
# Instalador para un servidor Ubuntu limpio (DigitalOcean, Oracle, Azure...).
# Uso:  bash instalar_servidor.sh
set -e

cd "$(dirname "$0")"

echo "==> 1/5  Actualizando el sistema"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip curl unzip

echo "==> 2/5  Creando el entorno virtual"
python3 -m venv .venv

echo "==> 3/5  Instalando dependencias de Python"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

echo "==> 4/5  Instalando Chromium y sus librerias del sistema"
.venv/bin/playwright install --with-deps chromium

echo "==> 5/5  Verificando"
.venv/bin/python -c "import pandas, requests, playwright; print('   dependencias OK')"

cat <<'FIN'

=========================================================
 Instalacion terminada.

 Siguientes pasos:
   1. Crear /etc/monitor-cupos.env con tus claves
   2. sudo cp monitor-cupos.service /etc/systemd/system/
   3. sudo systemctl daemon-reload
   4. sudo systemctl enable --now monitor-cupos

 Ver el estado:   systemctl status monitor-cupos
 Ver los avisos:  journalctl -u monitor-cupos -f
=========================================================
FIN
