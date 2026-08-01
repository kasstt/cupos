#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ayudante: obtener los chat_id de Telegram
=========================================

Para usar el canal "telegram" necesitas el token del bot y el chat_id de cada
persona que va a recibir los avisos. Este script te muestra los chat_id de
todos los que ya le escribieron al bot.

Pasos previos:
  1. En Telegram, escribele a @BotFather y manda /newbot
  2. Elige un nombre y un usuario que termine en 'bot'
  3. BotFather te responde con un token largo tipo 8123456789:AAH...
  4. TU y la otra persona le mandan cualquier mensaje al bot recien creado
     (busquenlo por su nombre de usuario y aprieten Start)
  5. Corran este script

Uso:
    python obtener_chat_id.py
    python obtener_chat_id.py 8123456789:AAHxxxxxxxxxxxxx
"""

import json
import sys
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"


def obtener_token():
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip()
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open(encoding="utf-8") as f:
                cfg = json.load(f)
            token = str(cfg.get("telegram", {}).get("token", "")).strip()
            if token:
                return token
        except Exception:
            pass
    return input("Pega el token que te dio @BotFather: ").strip()


def main():
    token = obtener_token()
    if not token:
        sys.exit("No hay token. Consiguelo con @BotFather en Telegram.")

    try:
        resp = requests.get(
            "https://api.telegram.org/bot" + token + "/getUpdates",
            timeout=30,
        )
    except Exception as e:
        sys.exit("Error de red: " + str(e))

    if resp.status_code == 404:
        sys.exit("Token invalido. Revisa que lo hayas copiado completo.")
    if resp.status_code != 200:
        sys.exit("HTTP " + str(resp.status_code) + ": " + resp.text[:300])

    datos = resp.json()
    if not datos.get("ok"):
        sys.exit("Telegram respondio con error: " + json.dumps(datos)[:300])

    encontrados = {}
    for upd in datos.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None:
            continue
        nombre = " ".join(
            str(chat.get(k, "")) for k in ("first_name", "last_name", "title")
        ).strip()
        usuario = chat.get("username")
        etiqueta = nombre or "(sin nombre)"
        if usuario:
            etiqueta += " (@" + str(usuario) + ")"
        encontrados[str(cid)] = etiqueta + " [" + str(chat.get("type", "?")) + "]"

    if not encontrados:
        print("\nNo hay mensajes todavia.")
        print("Cada persona que quiera recibir avisos debe buscar el bot en")
        print("Telegram, apretar START y mandarle cualquier mensaje.")
        print("Despues vuelve a correr este script.")
        return

    print("\nChats encontrados:\n")
    for cid, etiqueta in encontrados.items():
        print("  " + cid + "   " + etiqueta)

    lista = json.dumps(list(encontrados.keys()))
    print("\nCopia esto en tu config.json:\n")
    print('  "canal": "telegram",')
    print('  "telegram": {')
    print('    "token": "' + token + '",')
    print('    "chat_id": ' + lista)
    print("  },")
    print("")


if __name__ == "__main__":
    main()
