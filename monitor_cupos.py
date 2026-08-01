#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monitor de cupos UBB -> WhatsApp (CallMeBot)
============================================

Abre la pagina de inscripcion con un navegador real (Playwright), lee la tabla
de cupos, la compara con la lectura anterior y avisa por WhatsApp SOLO de lo
que cambio.

La sesion queda guardada en una carpeta de perfil local: el login lo haces UNA
vez a mano y despues el script entra solo mientras la sesion siga viva. Tu
contrasena nunca se guarda en ningun archivo.

Modos de uso
------------
    python monitor_cupos.py --dump       # inspeccionar las tablas de la pagina
    python monitor_cupos.py --once       # una sola pasada
    python monitor_cupos.py              # loop cada N minutos
    python monitor_cupos.py --test-wsp   # probar solo el envio de WhatsApp
    python monitor_cupos.py --reset      # borrar el estado guardado
"""

import argparse
import json
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, time as dtime
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
STATE_PATH = BASE / "estado_cupos.json"
LOG_PATH = BASE / "monitor.log"


# ---------------------------------------------------------------- utilidades

def log(msg):
    linea = "[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] " + str(msg)
    print(linea, flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass


def norm(s):
    """Minusculas, sin acentos, sin espacios repetidos."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def cargar_config():
    if not CONFIG_PATH.exists():
        sys.exit("ERROR: falta config.json. Copia config.example.json a config.json y editalo.")
    # utf-8-sig tolera el BOM que agrega PowerShell al guardar con Set-Content.
    with CONFIG_PATH.open(encoding="utf-8-sig") as f:
        cfg = json.load(f)
    # Variables de entorno: en un servidor las claves NO se guardan en archivos.
    env = os.environ
    if env.get("TELEGRAM_TOKEN"):
        cfg.setdefault("telegram", {})["token"] = env["TELEGRAM_TOKEN"]
    if env.get("TELEGRAM_CHAT_IDS"):
        cfg.setdefault("telegram", {})["chat_id"] = [
            c.strip() for c in env["TELEGRAM_CHAT_IDS"].split(",") if c.strip()
        ]
    if env.get("UBB_URL_LOGIN"):
        cfg.setdefault("login", {})["url"] = env["UBB_URL_LOGIN"]
    if env.get("UBB_USUARIO"):
        cfg.setdefault("login", {})["usuario"] = env["UBB_USUARIO"]
    if env.get("UBB_CLAVE"):
        cfg.setdefault("login", {})["clave"] = env["UBB_CLAVE"]

    tiene_login = bool((cfg.get("login") or {}).get("url"))
    if not cfg.get("url_cupos") and not tiene_login:
        sys.exit("ERROR: falta 'url_cupos' (o login.url) en config.json")
    canal = cfg.get("canal", "twilio")
    if canal not in ENVIADORES:
        sys.exit("ERROR: canal '" + canal + "' no valido. Opciones: " + ", ".join(sorted(ENVIADORES)))
    if not cfg.get(canal):
        sys.exit("ERROR: falta la seccion '" + canal + "' en config.json")
    return cfg


def cargar_estado():
    if STATE_PATH.exists():
        with STATE_PATH.open(encoding="utf-8-sig") as f:
            return json.load(f)
    return {}


def guardar_estado(estado):
    tmp = STATE_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=1)
    tmp.replace(STATE_PATH)


def dentro_de_horario(cfg):
    ventana = cfg.get("horario_activo")
    if not ventana:
        return True
    try:
        h_desde = dtime(*[int(x) for x in ventana["desde"].split(":")])
        h_hasta = dtime(*[int(x) for x in ventana["hasta"].split(":")])
    except Exception:
        return True
    ahora = datetime.now().time()
    return h_desde <= ahora <= h_hasta


# ------------------------------------------------------- deteccion de columnas

ALIAS = {
    "codigo": ["asignatura", "codigo", "cod", "sigla", "programa laboratorio", "programa"],
    "seccion": ["seccion", "seccion laboratorio", "sec"],
    "cupos": ["total cupos", "cupos totales", "cupos", "cupo", "vacantes"],
    "inscritos": ["inscritos", "matriculados", "ocupados", "inscritas"],
    "nombre": ["nombre asignatura", "nombre", "descripcion", "programa laboratorio"],
    "horario": ["horario", "horarios"],
}


def mapear_columnas(df):
    """Devuelve {campo: nombre_de_columna} para las columnas que encuentre."""
    cols = {c: norm(c) for c in df.columns}
    mapa = {}
    for campo, alias in ALIAS.items():
        mejor = None
        for al in alias:  # los alias van de mas especifico a mas generico
            for col, n in cols.items():
                if col in mapa.values():
                    continue
                if n == al:
                    mejor = col
                    break
            if mejor:
                break
        if not mejor:
            for al in alias:
                for col, n in cols.items():
                    if col in mapa.values():
                        continue
                    if al in n:
                        mejor = col
                        break
                if mejor:
                    break
        if mejor:
            mapa[campo] = mejor
    return mapa


def es_tabla_de_cupos(mapa):
    return "cupos" in mapa and "inscritos" in mapa and "seccion" in mapa


def a_entero(v):
    try:
        return int(re.sub(r"[^0-9-]", "", str(v)) or 0)
    except Exception:
        return 0


def limpiar_horario(v):
    if v is None:
        return ""
    txt = re.sub(r"\s+", " ", str(v)).strip()
    return "" if txt.lower() in ("nan", "none") else txt


# ------------------------------------------------------------------ scraping

def extraer_registros(html, cfg):
    """Parsea todas las tablas del HTML y devuelve {clave: registro}."""
    try:
        tablas = pd.read_html(StringIO(html))
    except ValueError:
        return {}

    prefijos = tuple(cfg.get("prefijos", []))
    vigilar_extra = set(str(x) for x in cfg.get("vigilar_siempre", []))
    incluir_sin_codigo = bool(cfg.get("incluir_filas_sin_codigo", False))

    registros = {}
    for df in tablas:
        df.columns = [str(c) for c in df.columns]
        mapa = mapear_columnas(df)
        if not es_tabla_de_cupos(mapa):
            continue
        # La tabla reparte los bloques de horario en varias columnas:
        # Horario, Horario.1, Horario.2, ... Hay que juntarlas todas.
        cols_horario = [c for c in df.columns if norm(c).startswith("horario")]
        for _, fila in df.iterrows():
            codigo = str(fila.get(mapa.get("codigo", ""), "")).strip()
            if codigo.lower() in ("nan", "none", ""):
                continue
            seccion = str(fila.get(mapa.get("seccion", ""), "")).strip()
            m = re.search(r"(\d+)\s*$", seccion)
            seccion_num = m.group(1).lstrip("0") or "0" if m else seccion

            es_numerico = bool(re.fullmatch(r"\d{4,8}", codigo))
            if es_numerico:
                if prefijos and not codigo.startswith(prefijos) and codigo not in vigilar_extra:
                    continue
            else:
                if not incluir_sin_codigo:
                    continue
                codigo = codigo[:60]

            cupos = a_entero(fila.get(mapa["cupos"]))
            inscritos = a_entero(fila.get(mapa["inscritos"]))
            nombre = str(fila.get(mapa.get("nombre", ""), "")).strip()
            if nombre.lower() in ("nan", "none"):
                nombre = ""
            trozos = []
            for col_h in cols_horario:
                trozo = limpiar_horario(fila.get(col_h))
                if trozo and trozo not in trozos:
                    trozos.append(trozo)
            horario = " | ".join(trozos)

            clave = codigo + "|" + str(seccion_num)
            registros[clave] = {
                "codigo": codigo,
                "seccion": str(seccion_num),
                "nombre": nombre[:80],
                "horario": horario[:200],
                "cupos": cupos,
                "inscritos": inscritos,
                "libres": cupos - inscritos,
            }
    return registros


def parece_login(page):
    """Heuristica: si hay un campo de password visible, no estamos dentro."""
    try:
        return page.locator("input[type='password']").count() > 0
    except Exception:
        return False


def abrir_pagina(pw, cfg, headless):
    perfil = BASE / cfg.get("perfil_dir", "perfil_navegador")
    perfil.mkdir(parents=True, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(perfil),
        headless=headless,
        viewport={"width": 1400, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.set_default_timeout(int(cfg.get("timeout_seg", 45)) * 1000)
    return ctx, page


URL_APRENDIDA = BASE / "url_aprendida.txt"


def _url_valida(u):
    u = str(u or "").strip()
    return u.lower().startswith("http") and "PEGA_AQUI" not in u


def _click_texto(page, texto, espera_ms):
    """Hace click por texto visible, por selector CSS o por rol. True si pudo."""
    intentos = []
    if texto.startswith("css:"):
        intentos.append(lambda: page.click(texto[4:], timeout=espera_ms))
    else:
        intentos.append(lambda: page.click("text=" + texto, timeout=espera_ms))
        intentos.append(lambda: page.get_by_role("link", name=texto).first.click(timeout=espera_ms))
        intentos.append(lambda: page.get_by_role("button", name=texto).first.click(timeout=espera_ms))
    for intento in intentos:
        try:
            intento()
            try:
                page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass
            page.wait_for_timeout(1500)
            return True
        except Exception:
            continue
    return False


def _hacer_pasos(page, cfg):
    """Repite los clicks que llevan del menu hasta la tabla de cupos.

    En config.json, "pasos_hasta_tabla" es una lista con el TEXTO visible de
    cada link o boton que hay que apretar, en orden. Ejemplo:
        "pasos_hasta_tabla": ["Inscripcion de Asignaturas", "Buscar"]
    Si un paso empieza con "css:", se usa como selector CSS en vez de texto.
    """
    pasos = [str(p).strip() for p in (cfg.get("pasos_hasta_tabla") or []) if str(p).strip()]
    if not pasos:
        return False

    # Atajo: si el menu ya esta desplegado, basta con el ultimo paso.
    if len(pasos) > 1 and _click_texto(page, pasos[-1], 3000):
        log("   fui directo a '" + pasos[-1] + "'.")
        return True

    for texto in pasos:
        if not _click_texto(page, texto, 10000):
            log("   no encontre el paso '" + texto + "' en la pagina.")
            return False
    log("   pasos hasta la tabla completados (" + str(len(pasos)) + ").")
    return True


def hacer_login(page, cfg):
    """Login automatico, para cuando no hay nadie delante del computador.

    Solo se activa si hay usuario y clave. Detecta los campos solo: busca el
    input de tipo password y el campo de texto que lo acompana. Si el portal
    tuviera un formulario raro, se pueden fijar a mano los selectores CSS con
    "selector_usuario", "selector_clave" y "selector_boton".
    """
    d = cfg.get("login") or {}
    usuario = str(d.get("usuario", "")).strip()
    clave = str(d.get("clave", "")).strip()
    if not (usuario and clave):
        return False

    try:
        url = str(d.get("url", "")).strip()
        if _url_valida(url):
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

        sel_clave = d.get("selector_clave") or "input[type='password']"
        page.wait_for_selector(sel_clave, timeout=15000)
        campo_clave = page.locator(sel_clave).first

        sel_usuario = d.get("selector_usuario")
        if sel_usuario:
            campo_usuario = page.locator(sel_usuario).first
        else:
            campo_usuario = page.locator(
                "input[type='text'], input[type='email'], input:not([type])"
            ).first

        campo_usuario.fill(usuario)
        campo_clave.fill(clave)

        boton = d.get("selector_boton")
        if boton:
            page.click(boton, timeout=8000)
        else:
            campo_clave.press("Enter")

        try:
            page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass
        page.wait_for_timeout(2500)

        if parece_login(page):
            log("El login automatico no entro: la pagina sigue pidiendo clave.")
            return False
        log("Login automatico correcto.")
        return True
    except Exception as e:
        log("Fallo el login automatico: " + str(e).split("\n")[0])
        return False


def leer_pagina(page, cfg, interactivo):
    url = str(cfg.get("url_cupos", "")).strip()

    # Si no hay URL valida en el config, reutiliza la que se aprendio antes.
    if not _url_valida(url) and URL_APRENDIDA.exists():
        try:
            guardada = URL_APRENDIDA.read_text(encoding="utf-8").strip()
            if _url_valida(guardada):
                url = guardada
                log("Usando la URL aprendida: " + url)
        except Exception:
            pass

    # Sin URL utilizable pero con credenciales: entra por el login y llega a la
    # tabla navegando el menu. Es el camino que se usa en el servidor / GitHub.
    if not _url_valida(url) and (cfg.get("login") or {}).get("usuario"):
        if hacer_login(page, cfg):
            _hacer_pasos(page, cfg)
            return page.content()
        if not interactivo:
            raise RuntimeError(
                "El login automatico fallo y no hay ventana para hacerlo a mano. "
                "Revisa el usuario y la clave."
            )

    # Modo manual: tu navegas hasta la tabla y el script lee lo que quede en pantalla.
    if cfg.get("navegacion_manual") or not _url_valida(url):
        if not interactivo:
            raise RuntimeError(
                "No hay una URL de cupos valida en config.json y en modo --headless "
                "no se puede navegar a mano. Corre el script sin --headless una vez."
            )
        inicio = str(cfg.get("url_inicio", "")).strip()

        # Si no hay url_inicio, deduce el sitio a partir de la URL aprendida
        # (le quita el identificador de sesion y deja solo el dominio).
        if not _url_valida(inicio) and URL_APRENDIDA.exists():
            try:
                guardada = URL_APRENDIDA.read_text(encoding="utf-8").strip()
                m = re.match(r"^(https?://[^/]+)", guardada)
                if m:
                    inicio = m.group(1) + "/"
            except Exception:
                pass

        # Ultimo recurso: el portal de inscripcion de la UBB.
        if not _url_valida(inicio):
            inicio = "https://inscripcion.ubiobio.cl/"

        if page.url in ("", "about:blank"):
            try:
                page.goto(inicio, wait_until="domcontentloaded")
                log("Abri la pagina de inicio: " + inicio)
            except Exception as e:
                log("No pude abrir " + inicio + " (" + str(e).split("\n")[0] + ")."
                    " Escribe la direccion a mano en la ventana del navegador.")
        print("\n" + "=" * 66)
        print(" NAVEGACION MANUAL")
        print(" No hay una URL de cupos configurada, asi que hazlo tu:")
        print("   1. En la ventana de Chromium que se abrio, entra a la intranet")
        print("   2. Haz el login con tus datos")
        print("   3. Navega hasta VER la tabla de cupos en pantalla")
        print(" El script va a leer exactamente la pagina que quede visible.")
        print("=" * 66)
        input(" Cuando tengas la tabla a la vista, presiona ENTER aqui... ")
        page.wait_for_timeout(1200)
        actual = page.url
        if _url_valida(actual):
            try:
                URL_APRENDIDA.write_text(actual, encoding="utf-8")
                log("URL aprendida y guardada para las proximas pasadas:")
                log("   " + actual)
            except Exception:
                pass
        return page.content()

    # La intranet UBB mete un identificador de sesion dentro de la RUTA de la
    # direccion (/mc147.../intranet/inicio.php). Ese codigo caduca, asi que
    # volver a navegar a la URL guardada falla. Si el navegador ya tiene la
    # pagina abierta, es mucho mas seguro recargarla que navegar de nuevo.
    ya_abierta = page.url not in ("", "about:blank")
    try:
        if cfg.get("solo_recargar", True) and ya_abierta:
            page.reload(wait_until="domcontentloaded")
        else:
            page.goto(url, wait_until="domcontentloaded")
    except Exception as e:
        primera = str(e).split("\n")[0]
        log("No se pudo navegar (" + primera + "). Reintento recargando la pagina actual.")
        try:
            page.reload(wait_until="domcontentloaded")
        except Exception:
            try:
                if URL_APRENDIDA.exists():
                    URL_APRENDIDA.unlink()
            except Exception:
                pass
            raise RuntimeError(
                "La direccion guardada ya no responde. Suele pasar porque la intranet "
                "incluye un codigo de sesion en la URL y ese codigo caduca. Entra de "
                "nuevo a mano en la ventana de Chromium hasta ver la tabla de cupos."
            )
    espera = cfg.get("selector_tabla")
    if espera:
        try:
            page.wait_for_selector(espera, timeout=15000)
        except PWTimeout:
            pass
    else:
        page.wait_for_timeout(2500)

    if parece_login(page):
        if hacer_login(page, cfg):
            _hacer_pasos(page, cfg)
        elif not interactivo:
            raise RuntimeError(
                "La sesion expiro y el login automatico no funciono. Revisa "
                "usuario y clave, o corre el script sin --headless para entrar a mano."
            )
        else:
            print("\n" + "=" * 62)
            print(" La pagina pide login. Ingresa tus datos en la ventana abierta,")
            print(" navega hasta la tabla de cupos y vuelve aca.")
            print("=" * 62)
            input(" Cuando ya veas la tabla de cupos, presiona ENTER... ")
            page.wait_for_timeout(1500)

    html = page.content()

    # Al recargar, el portal suele volver al menu y la tabla desaparece, porque
    # el listado se genera al enviar un formulario. Si pasa eso, repetimos los
    # clicks configurados en "pasos_hasta_tabla" para volver a generarla.
    if not extraer_registros(html, cfg):
        if _hacer_pasos(page, cfg):
            html = page.content()
        elif interactivo and cfg.get("pedir_ayuda_si_falla", False):
            print("\n" + "=" * 62)
            print(" No aparece la tabla de cupos en pantalla.")
            print(" Navega hasta ella en la ventana de Chromium y vuelve aca.")
            print("=" * 62)
            input(" Cuando la tengas a la vista, presiona ENTER... ")
            page.wait_for_timeout(1200)
            html = page.content()

    return html


# ------------------------------------------------------------------ el diff

def comparar(anterior, actual, cfg):
    avisar = set(cfg.get("avisar_de", ["libero", "lleno", "nuevo"]))
    umbral = int(cfg.get("umbral_cambio", 1))
    cambios = []

    for clave, nuevo in actual.items():
        viejo = anterior.get(clave)
        if viejo is None:
            if "nuevo" in avisar and nuevo["libres"] > 0:
                cambios.append({"tipo": "nuevo", "reg": nuevo, "antes": None})
            continue
        antes = viejo.get("libres", 0)
        ahora = nuevo["libres"]
        if antes == ahora:
            continue
        if antes <= 0 < ahora:
            tipo = "libero"
        elif ahora <= 0 < antes:
            tipo = "lleno"
        elif ahora > antes:
            tipo = "subio"
        else:
            tipo = "bajo"
        if tipo not in avisar:
            continue
        if tipo in ("subio", "bajo") and abs(ahora - antes) < umbral:
            continue
        cambios.append({"tipo": tipo, "reg": nuevo, "antes": antes})

    if "desaparecio" in avisar:
        for clave, viejo in anterior.items():
            if clave not in actual:
                cambios.append({"tipo": "desaparecio", "reg": viejo, "antes": viejo.get("libres")})

    orden = {"libero": 0, "nuevo": 1, "subio": 2, "lleno": 3, "bajo": 4, "desaparecio": 5}
    cambios.sort(key=lambda c: (orden.get(c["tipo"], 9), c["reg"]["codigo"], c["reg"]["seccion"]))
    return cambios


TITULOS = {
    "libero": "\U0001F7E2 SE LIBERO CUPO",
    "nuevo": "\U0001F195 SECCION NUEVA",
    "subio": "\U0001F4C8 MAS CUPO",
    "lleno": "\U0001F534 SE LLENO",
    "bajo": "\U0001F4C9 MENOS CUPO",
    "desaparecio": "\u26AB YA NO APARECE",
}


def redactar(cambios):
    hora = datetime.now().strftime("%H:%M")
    lineas = ["\U0001F393 *Cupos UBB* \u2014 " + hora]
    tipo_actual = None
    for c in cambios:
        if c["tipo"] != tipo_actual:
            tipo_actual = c["tipo"]
            lineas.append("")
            lineas.append(TITULOS.get(tipo_actual, tipo_actual.upper()))
        r = c["reg"]
        nombre = r["nombre"] or "(sin nombre)"
        detalle = "\u2022 " + r["codigo"] + " sec." + r["seccion"] + " " + nombre
        lineas.append(detalle)
        estado = "   " + str(r["libres"]) + " libres (" + str(r["inscritos"]) + "/" + str(r["cupos"]) + ")"
        if c["antes"] is not None:
            estado += " \u2014 antes " + str(c["antes"])
        lineas.append(estado)
        for bloque in str(r["horario"]).split(" | "):
            if bloque.strip():
                lineas.append("   " + bloque.strip())
    return "\n".join(lineas)


# --------------------------------------------------------------- WhatsApp

def _lista(valor):
    """Acepta un solo destinatario o una lista y siempre devuelve una lista."""
    if valor is None:
        return []
    if isinstance(valor, (list, tuple)):
        return [str(v).strip() for v in valor if str(v).strip()]
    texto = str(valor).strip()
    if not texto:
        return []
    if "," in texto:
        return [t.strip() for t in texto.split(",") if t.strip()]
    return [texto]


def _via_twilio(parte, cfg):
    """WhatsApp por la API oficial de Twilio (sandbox o numero propio).

    'hacia' puede ser un numero o una lista de numeros. Cada destinatario debe
    haberse unido antes al sandbox mandando el mensaje 'join <codigo>'.
    """
    d = cfg["twilio"]
    sid = str(d.get("account_sid", "")).strip()
    token = str(d.get("auth_token", "")).strip()
    desde = str(d.get("desde", "+14155238886")).strip()
    destinos = _lista(d.get("hacia"))
    if not (sid and token and destinos):
        raise RuntimeError("faltan account_sid, auth_token o hacia en config.json")

    url = "https://api.twilio.com/2010-04-01/Accounts/" + sid + "/Messages.json"
    fallos = []
    for numero in destinos:
        try:
            resp = requests.post(
                url,
                auth=(sid, token),
                data={
                    "From": "whatsapp:" + desde,
                    "To": "whatsapp:" + numero,
                    "Body": parte,
                },
                timeout=40,
            )
            if resp.status_code >= 400:
                detalle = resp.text[:300]
                if "63016" in detalle or "63015" in detalle:
                    detalle += "  <-- Ventana de 24h cerrada: ese numero debe escribirle algo al sandbox."
                elif "21608" in detalle:
                    detalle += "  <-- Ese numero nunca hizo el 'join <codigo>' al sandbox."
                fallos.append(numero + " -> HTTP " + str(resp.status_code) + ": " + detalle)
            else:
                log("   ok -> " + numero)
        except Exception as e:
            fallos.append(numero + " -> " + str(e))

    if fallos and len(fallos) == len(destinos):
        raise RuntimeError(" | ".join(fallos))
    for f in fallos:
        log("   AVISO, fallo un destinatario: " + f)


def _via_callmebot(parte, cfg):
    d = cfg["callmebot"]
    telefono = str(d.get("telefono", "")).strip()
    apikey = str(d.get("apikey", "")).strip()
    if not (telefono and apikey):
        raise RuntimeError("faltan telefono o apikey en config.json")
    resp = requests.get(
        "https://api.callmebot.com/whatsapp.php",
        params={"phone": telefono, "text": parte, "apikey": apikey},
        timeout=40,
    )
    if resp.status_code != 200:
        raise RuntimeError("HTTP " + str(resp.status_code) + ": " + resp.text[:300])


def _via_telegram(parte, cfg):
    d = cfg["telegram"]
    token = str(d.get("token", "")).strip()
    chats = _lista(d.get("chat_id"))
    if not (token and chats):
        raise RuntimeError("faltan token o chat_id en config.json")
    fallos = []
    for chat_id in chats:
        resp = requests.post(
            "https://api.telegram.org/bot" + token + "/sendMessage",
            data={"chat_id": chat_id, "text": parte},
            timeout=40,
        )
        log("   -> chat " + chat_id + " HTTP " + str(resp.status_code))
        if resp.status_code != 200:
            fallos.append(chat_id + " -> HTTP " + str(resp.status_code) + ": " + resp.text[:200])
    if fallos and len(fallos) == len(chats):
        raise RuntimeError(" | ".join(fallos))
    for f in fallos:
        log("   AVISO, fallo un destinatario: " + f)


def _via_ntfy(parte, cfg):
    d = cfg["ntfy"]
    canal = str(d.get("canal", "")).strip()
    if not canal:
        raise RuntimeError("falta el nombre del canal en config.json")
    servidor = str(d.get("servidor", "https://ntfy.sh")).rstrip("/")
    resp = requests.post(
        servidor + "/" + canal,
        data=parte.encode("utf-8"),
        headers={"Title": "Cupos UBB"},
        timeout=40,
    )
    if resp.status_code >= 400:
        raise RuntimeError("HTTP " + str(resp.status_code) + ": " + resp.text[:300])


ENVIADORES = {
    "twilio": _via_twilio,
    "callmebot": _via_callmebot,
    "telegram": _via_telegram,
    "ntfy": _via_ntfy,
}


def enviar_whatsapp(texto, cfg):
    canal = cfg.get("canal", "twilio")
    enviar = ENVIADORES.get(canal)
    if enviar is None:
        log("AVISO: canal '" + str(canal) + "' desconocido, no se envia nada.")
        return False

    limite = int(cfg.get("max_caracteres", 900))
    partes, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > limite and actual:
            partes.append(actual)
            actual = linea
        else:
            actual = (actual + "\n" + linea) if actual else linea
    if actual:
        partes.append(actual)

    ok = True
    for i, parte in enumerate(partes, 1):
        sufijo = "" if len(partes) == 1 else "\n(" + str(i) + "/" + str(len(partes)) + ")"
        try:
            enviar(parte + sufijo, cfg)
            log("Mensaje enviado por " + canal + " (" + str(i) + "/" + str(len(partes)) + ")")
        except Exception as e:
            ok = False
            log("Fallo el envio por " + canal + ": " + str(e))
        if i < len(partes):
            time.sleep(4)
    return ok


# ------------------------------------------------------------------ pasadas

def una_pasada(page, cfg, interactivo):
    html = leer_pagina(page, cfg, interactivo)
    actual = extraer_registros(html, cfg)
    if not actual:
        log("No se encontro ninguna tabla de cupos. Usa --dump para revisar el HTML.")
        return

    anterior = cargar_estado()
    if not anterior:
        guardar_estado(actual)
        log("Primera lectura guardada: " + str(len(actual)) + " secciones vigiladas. Desde ahora aviso de los cambios.")
        if cfg.get("avisar_primera_lectura", True):
            con_cupo = sum(1 for r in actual.values() if r["libres"] > 0)
            enviar_whatsapp(
                "\U0001F393 *Monitor de cupos activo*\n"
                + str(len(actual)) + " secciones vigiladas, "
                + str(con_cupo) + " con cupo disponible ahora.\n"
                "Te aviso en cuanto algo se mueva.",
                cfg,
            )
        return

    cambios = comparar(anterior, actual, cfg)
    guardar_estado(actual)

    if not cambios:
        log("Sin cambios (" + str(len(actual)) + " secciones revisadas).")
        return

    log(str(len(cambios)) + " cambio(s) detectado(s).")
    mensaje = redactar(cambios)
    print("\n" + mensaje + "\n")
    enviar_whatsapp(mensaje, cfg)


def modo_dump(page, cfg):
    html = leer_pagina(page, cfg, interactivo=True)
    (BASE / "pagina_volcada.html").write_text(html, encoding="utf-8")
    log("HTML guardado en pagina_volcada.html")
    try:
        tablas = pd.read_html(StringIO(html))
    except ValueError:
        log("pandas no encontro ninguna tabla <table> en la pagina.")
        return
    log("Se encontraron " + str(len(tablas)) + " tabla(s).")
    for i, df in enumerate(tablas):
        df.columns = [str(c) for c in df.columns]
        mapa = mapear_columnas(df)
        print("\n--- TABLA " + str(i) + " | filas=" + str(len(df)) + " ---")
        print("columnas:", list(df.columns))
        print("mapeo detectado:", mapa)
        print("sirve como tabla de cupos:", es_tabla_de_cupos(mapa))
        print(df.head(4).to_string())
    registros = extraer_registros(html, cfg)
    print("\nRegistros que quedarian vigilados:", len(registros))
    for clave in list(registros)[:15]:
        print("  ", registros[clave])


def main():
    ap = argparse.ArgumentParser(description="Monitor de cupos UBB -> WhatsApp")
    ap.add_argument("--once", action="store_true", help="una sola pasada y salir")
    ap.add_argument("--dump", action="store_true", help="inspeccionar las tablas de la pagina")
    ap.add_argument("--test-wsp", action="store_true", help="enviar un mensaje de prueba")
    ap.add_argument("--reset", action="store_true", help="borrar el estado guardado")
    ap.add_argument("--headless", action="store_true", help="sin ventana (para servidores)")
    ap.add_argument("--test-login", action="store_true", help="probar solo el login automatico")
    args = ap.parse_args()

    cfg = cargar_config()

    if args.reset:
        STATE_PATH.unlink(missing_ok=True)
        log("Estado borrado.")
        return

    if args.test_wsp:
        ok = enviar_whatsapp("\u2705 Prueba del monitor de cupos UBB. Si lees esto, funciona.", cfg)
        log("Prueba " + ("OK" if ok else "FALLIDA"))
        return

    intervalo = float(cfg.get("intervalo_min", 5))
    interactivo = not args.headless

    with sync_playwright() as pw:
        ctx, page = abrir_pagina(pw, cfg, headless=args.headless)
        try:
            if args.test_login:
                if hacer_login(page, cfg):
                    log("Prueba de login OK.")
                    if _hacer_pasos(page, cfg):
                        registros = extraer_registros(page.content(), cfg)
                        log("Tras el login veo " + str(len(registros)) + " secciones vigiladas.")
                else:
                    log("Prueba de login FALLIDA. Revisa login.url, usuario y clave.")
                return
            if args.dump:
                modo_dump(page, cfg)
                return
            if args.once:
                una_pasada(page, cfg, interactivo)
                return

            log("Monitor iniciado. Reviso cada " + str(intervalo) + " min. Ctrl+C para detener.")
            while True:
                if dentro_de_horario(cfg):
                    try:
                        una_pasada(page, cfg, interactivo)
                    except Exception as e:
                        log("Error en la pasada: " + str(e))
                        if cfg.get("avisar_errores", False):
                            enviar_whatsapp("\u26A0\uFE0F Monitor con problemas: " + str(e)[:200], cfg)
                else:
                    log("Fuera del horario activo, no reviso.")
                espera = intervalo * 60 + random.uniform(0, float(cfg.get("jitter_seg", 30)))
                time.sleep(espera)
        except KeyboardInterrupt:
            log("Detenido por el usuario.")
        finally:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
