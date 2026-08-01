# Monitor de cupos UBB -> WhatsApp (Twilio)

Avisa cuando se mueven los cupos de las asignaturas de Formacion Integral
(codigos que empiezan con **34** y **35**) en la pagina de inscripcion.

- Revisa cada **5 minutos**
- Avisa solo de lo que **cambio**: se libero cupo, se lleno, o seccion nueva
- Envia por **WhatsApp usando la API oficial de Twilio**
- Tu contrasena UBB **no** se guarda en ningun archivo: el login lo haces una vez a mano
- El canal de envio es intercambiable: `twilio`, `callmebot`, `telegram` o `ntfy`,
  se elige con el campo `canal` del config sin tocar el codigo

---

## 1. Instalar

Necesitas Python 3.9 o superior.

```bash
cd C:\monitor-cupos
python -m venv .venv

# Windows PowerShell (si bloquea scripts, corre primero la linea de abajo)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\activate

# Mac o Linux
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

Si el `activate` te sigue dando problemas, puedes saltartelo y usar siempre
`.venv\Scripts\python.exe` en vez de `python`.

## 2. Configurar Twilio (WhatsApp Sandbox)

1. Crea una cuenta gratis en **https://www.twilio.com/try-twilio** y verifica tu
   correo y tu numero de telefono.
2. En la consola de Twilio anda a **Messaging > Try it out > Send a WhatsApp message**.
3. Ahi te muestra el numero del sandbox (normalmente **+1 415 523 8886**) y un
   codigo del tipo `join algo-algo`.
4. Desde el WhatsApp **que va a recibir los avisos**, agrega ese numero a
   contactos y mandale exactamente ese mensaje: `join algo-algo`.
   Debe responderte confirmando que estas conectado al sandbox.
5. En el panel principal de Twilio copia tu **Account SID** y tu **Auth Token**.

### Varios destinatarios

El campo `hacia` acepta una lista. Cada numero de esa lista tiene que hacer su
propio `join <codigo>` al sandbox desde su WhatsApp: Twilio no deja enviar a
nadie que no se haya unido, y esa es justamente la proteccion contra el spam.

Si un destinatario falla y el otro no, el script envia igual al que si funciona
y deja el problema anotado en el log. Solo se considera error total cuando
fallan todos.

### La limitacion importante del sandbox

Twilio solo permite enviar mensajes libres dentro de una **ventana de 24 horas**
desde el ultimo mensaje que el receptor le envio al sandbox. En la practica:

> Una vez al dia, mandale cualquier mensaje (un "hola" basta) al numero del
> sandbox desde el WhatsApp que recibe los avisos. Si no, los envios empiezan a
> fallar con el error 63016 y el log te lo va a decir con todas sus letras.

Si eso te resulta molesto, cambia `"canal": "telegram"` en el config: Telegram
no tiene ventanas de tiempo ni cupos. Los datos de Twilio pueden quedarse ahi
por si vuelves.

## 3. Crear el `config.json`

Copia `config.example.json` a **`config.json`** y rellena:

```json
"url_cupos": "la URL de la pagina donde se ve la tabla de cupos",
"canal": "twilio",
"twilio": {
  "account_sid": "ACxxxxxxxx...",
  "auth_token": "tu auth token",
  "desde": "+14155238886",
  "hacia": ["+569XXXXXXXX", "+569YYYYYYYY"]
}
```

| Campo | Que poner |
| --- | --- |
| `url_cupos` | La URL exacta de la pagina con la tabla de cupos, ya con la busqueda hecha |
| `desde` | El numero del sandbox de Twilio |
| `hacia` | Lista de numeros que reciben, cada uno con codigo de pais y sin espacios. Tambien acepta un solo numero como texto |
| `prefijos` | `["34", "35"]` = solo Formacion Integral. Deja `[]` para vigilar todo |
| `vigilar_siempre` | Codigos extra a seguir aunque no calcen con los prefijos, ej. `["220167"]` |
| `avisar_de` | `libero`, `lleno`, `nuevo`, `subio`, `bajo`, `desaparecio` |
| `intervalo_min` | Minutos entre revisiones |
| `horario_activo` | Ventana en que revisa, para no gastar la madrugada |
| `selector_tabla` | Opcional: selector CSS de la tabla, si la pagina carga lento |

### Cambiar de canal despues

Solo cambias el campo `canal` y llenas la seccion correspondiente:

- `"canal": "telegram"` -> `token` (te lo da @BotFather) y `chat_id`
- `"canal": "ntfy"` -> `canal` (un nombre largo e inventado) y la app ntfy en el celular
- `"canal": "callmebot"` -> `telefono` y `apikey`, cuando el bot tenga cupos libres

## 4. Probar que lee la tabla (el paso clave)

```bash
python monitor_cupos.py --dump
```

Se abre una ventana de Chromium. **Haz el login a mano**, navega hasta ver la
tabla de cupos y vuelve a la terminal a presionar ENTER.

Imprime todas las tablas encontradas, sus columnas y cuantos registros quedarian
vigilados. Tambien guarda `pagina_volcada.html`.

**Si dice `Registros que quedarian vigilados: 0`**, pasa la salida de la terminal
para ajustar el parser. Es lo normal en el primer intento.

## 5. Probar el envio

```bash
python monitor_cupos.py --test-wsp
```

Debe llegar un WhatsApp de prueba. Errores tipicos:

| Error en el log | Causa |
| --- | --- |
| `HTTP 401` | Account SID o Auth Token mal copiados |
| `63016` o `63015` | La ventana de 24h se cerro: manda un "hola" al sandbox |
| `21608` | El numero destino no hizo el `join` al sandbox |
| `21211` | El numero `hacia` esta mal escrito, revisa el `+56` |

## 6. Dejarlo corriendo

```bash
python monitor_cupos.py
```

La primera pasada solo guarda la foto inicial y avisa "monitor activo". Desde la
segunda en adelante te notifica cada movimiento. Deja la terminal abierta y **no
cierres la ventana del navegador**: esa ventana mantiene tu sesion.

Para detener: `Ctrl + C`.

Si ya tienes la sesion guardada y no quieres ver la ventana: `--headless`.
Si el portal boto la sesion, en headless falla a proposito y hay que correrlo
sin esa opcion para re-loguear.

---

## Archivos que se generan

| Archivo | Que es |
| --- | --- |
| `estado_cupos.json` | La ultima foto de los cupos. Borrar con `--reset` |
| `monitor.log` | Historial de todo lo que hizo el script |
| `perfil_navegador/` | Tu sesion del portal. **No la compartas** |
| `pagina_volcada.html` | Solo con `--dump`, para depurar |

## Ejemplo de mensaje que llega

```
Cupos UBB - 18:35

SE LIBERO CUPO
- 340451 sec.1 LENGUA DE SENAS CHILENA Y CULTURA
   1 libres (29/30) - antes 0
   TEO: MA 17:10 18:30 A102AC

SE LLENO
- 340567 sec.1 TALLER DE PRODUCCION ORAL Y ESCRITA
   0 libres (45/45) - antes 4
   TEO: MA 08:10 09:30 S301AD
```

## Notas

- Si la UBB cambia el HTML, el parser puede dejar de encontrar la tabla. El log
  lo avisa y se arregla ajustando el mapeo de columnas.
- Cada 5 minutos son unas 190 visitas al dia: carga baja y razonable. No lo
  bajes de 5 minutos.
- El script no toca nada del portal: solo lee la pagina. No inscribe ramos.
