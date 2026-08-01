# Correr el monitor en GitHub Actions

GitHub ejecuta el monitor en sus propios computadores, gratis y sin limite en
repositorios publicos. Tu computador puede estar apagado.

## 1. Que se sube y que no

SE SUBE (es seguro que sea publico):

    monitor_cupos.py        el codigo
    obtener_chat_id.py
    requirements.txt
    config.ci.json          configuracion SIN claves
    .github/workflows/monitor.yml
    .gitignore

NUNCA SE SUBE (lo bloquea .gitignore):

    config.json             tiene tu token y tu clave
    url_aprendida.txt
    pagina_volcada.html
    monitor.log
    perfil_navegador/
    .venv/

Las claves viajan aparte, como "Secrets" del repositorio: estan cifradas, no se
pueden volver a leer y GitHub las tapa con *** en los registros.

## 2. Crear el repositorio

    cd C:\Users\dg281\OneDrive\Escritorio\monitor-cupos
    git init
    git add .
    git status          # revisa que NO aparezca config.json
    git commit -m "Monitor de cupos UBB"

Crea el repositorio vacio en github.com/new (publico, sin README) y luego:

    git remote add origin https://github.com/TU_USUARIO/monitor-cupos.git
    git branch -M main
    git push -u origin main

## 3. Cargar los Secrets

En el repositorio: Settings -> Secrets and variables -> Actions -> New
repository secret. Crea estos cinco:

    TELEGRAM_TOKEN      el token del bot (revocado y regenerado)
    TELEGRAM_CHAT_IDS   7371387588,8846457387
    UBB_URL_LOGIN       https://inscripcion.ubiobio.cl/
    UBB_USUARIO         tu usuario de la intranet
    UBB_CLAVE           tu clave de la intranet

## 4. Probar

Pestana Actions -> "Monitor de cupos UBB" -> Run workflow.

En el registro deberias ver:

    Login automatico correcto.
    fui directo a 'Horario'.
    Sin cambios (109 secciones revisadas).

Desde ahi corre solo cada 10 minutos.

## 5. Cosas que conviene saber

- Los horarios de GitHub no son exactos: "cada 10 minutos" suele ser entre 10 y
  20 minutos reales.
- El estado se guarda como un commit automatico en estado_cupos.json. Es lo que
  permite comparar una revision con la siguiente.
- Si el repositorio pasa 60 dias sin actividad, GitHub desactiva la ejecucion
  programada. Los commits del estado la mantienen viva.
- Para detenerlo: Actions -> el workflow -> ... -> Disable workflow.
