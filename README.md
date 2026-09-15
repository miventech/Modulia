# ModulAI

ModulAI es un asistente personal, privado y modular. Su núcleo ejecuta comandos
deterministas y descubre extensiones desde directorios configurables. La IA es una
capacidad futura y opcional, no una dependencia del sistema.

La especificación completa está en [PLAN.md](PLAN.md).

## Estado actual

Las Entregas 0 y 1 incluyen:

- configuración TOML;
- logging;
- contratos de mensajes, comandos y módulos;
- descubrimiento y ciclo de vida de módulos;
- registro de auditoría en SQLite;
- módulo de ejemplo con el comando `/hola`;
- ejecución única, consola interactiva e interfaz visual local;
- comandos de ayuda, módulos, conversaciones y memoria persistente;
- API HTTP local validada;
- configuración de módulos desde la UI, aplicada después de reiniciar;
- adaptador Telegram opcional con long polling y allowlist;
- pruebas unitarias y de integración.

Incluye también trabajos en segundo plano, audio de YouTube y transcripción local.
Todavía no incluye WebSocket ni WhatsApp.

## Requisitos

- Windows.
- Python 3.10 o superior.
- Para desarrollo completo se recomienda una distribución oficial de Python con
  `pip` y soporte para entornos virtuales.

## Ejecución rápida sin instalar

Desde Símbolo del sistema (CMD), usa el lanzador incluido:

```bat
run.cmd --command "/hola"
```

Este lanzador configura `PYTHONPATH` y selecciona primero el entorno virtual de Windows
incluido en el proyecto. También se puede indicar otro ejecutable compatible:

```bat
set "MODULAI_PYTHON=C:\ruta\a\python.exe"
run.cmd --command "/hola"
```

En PowerShell, desde la raíz del repositorio, también se puede ejecutar directamente:

```powershell
$env:PYTHONPATH = "src"
python -m modulai --command "/hola"
```

La asignación `$env:PYTHONPATH = "src"` es sintaxis de PowerShell. El equivalente en CMD
es `set "PYTHONPATH=src"`.

Consola interactiva:

```powershell
$env:PYTHONPATH = "src"
python -m modulai --console
```

Escribe `/salir` para detener la consola.

Interfaz visual local:

```bat
run.cmd --web
```

La aplicación abre `http://127.0.0.1:8765` automáticamente.
También puedes hacer doble clic en `Abrir-ModulAI.bat`; usa el entorno virtual del
proyecto y conserva la ventana abierta si el arranque falla.

## Distribución Windows

`build-exe.cmd` genera una distribución autocontenida en `dist/ModulAI/`. Ejecuta
`ModulAI.exe` para abrir la interfaz. Conserva las carpetas `config/` y `modules/` junto
al ejecutable: los módulos se descubren al iniciar, por lo que puedes añadir uno nuevo y
reiniciar sin recompilar. La distribución no incluye `.env` ni `config/app.toml`.

## IA opcional con OpenCode

## OCR local

El módulo `local.ocr` procesa imágenes y PDF mediante Tesseract. Instálalo y déjalo
disponible en el `PATH` de Windows, o configura la ruta a `tesseract.exe` desde la
tarjeta del módulo. Usa `/ocr` con un archivo adjunto o una ruta local. `/ultimo_ocr`
devuelve el último resultado, el texto extraído y las rutas de origen y salida para que
también pueda consultarlo la IA mediante herramientas.

El runtime preempaquetado de OCR se mantiene fuera de Git para evitar publicar más de
100 MB de binarios. Puede incluirse por separado en una distribución de escritorio bajo
`modules/ocr/runtime/tesseract`.

El módulo `local.opencode_ai` permite conectar ModulAI con OpenCode Zen o cualquier
endpoint compatible con OpenAI Chat Completions. Está disponible al iniciar, pero no
realiza llamadas hasta que configures un modelo. En la configuración de **Módulos**, indica
el modelo exacto y conserva la clave en `.env`:

```text
OPENCODE_API_KEY=tu_clave
```

Configura `api_key_env = "OPENCODE_API_KEY"` y el endpoint, guarda y reinicia. La pantalla
de configuración consulta `/models` y muestra un desplegable con los modelos publicados
por ese endpoint. Después puedes usar `/ia <pregunta>` o simplemente escribir texto normal
cuando la IA esté activa.
La IA recibe el catálogo de comandos instalados y puede invocarlos como herramientas para
consultar o modificar finanzas, tareas, memoria y otros módulos. `/ia_estado` comprueba
la configuración sin revelar la clave. Para un servidor local compatible no hace falta
clave; usa una URL `http://127.0.0.1` y el modelo que exponga ese servidor.

## Descargar audio de YouTube

El módulo `local.youtube_audio` añade el comando:

```text
/audio https://www.youtube.com/watch?v=ID_DEL_VIDEO
```

También puedes indicar el nombre de una canción o video; se descargará el primer
resultado encontrado en YouTube:

```text
/audio Bohemian Rhapsody Queen
```

La orden crea un trabajo en segundo plano. Consulta el progreso con `/tareas` o desde
la sección Diagnóstico de la UI; cuando termine aparecerá un enlace para descargar el
MP3. El módulo usa `yt-dlp` y requiere `ffmpeg`/`ffprobe` disponibles en el sistema.

Para instalar la dependencia Python manualmente:

```bat
.venv-win\Scripts\python.exe -m pip install yt-dlp
```

Usa este módulo únicamente con contenido que tengas derecho a descargar y convertir.

## Descargar videos de Facebook

El módulo `local.facebook_video` descarga videos públicos de Facebook en formato MP4:

```text
/fb_video https://www.facebook.com/reel/ID_DEL_VIDEO
```

También acepta enlaces de publicaciones, `watch` y `fb.watch`. La orden crea un trabajo
en segundo plano y guarda el archivo usando el título del video. Los videos privados o
restringidos pueden requerir autenticación y no están soportados en esta primera versión.

Este módulo comparte las dependencias `yt-dlp` y `ffmpeg` con el módulo de YouTube. Úsalo
únicamente con contenido que tengas derecho a descargar.

## Descargar un video desde cualquier sitio compatible

El módulo `local.video_downloader` añade un comando general basado en `yt-dlp`:

```text
/video https://sitio.example/ruta/al/video
```

Acepta cualquier URL pública HTTP o HTTPS que sea compatible con `yt-dlp`, descarga la
mejor calidad disponible y usa el título como nombre del archivo. Por seguridad rechaza
direcciones locales, privadas, reservadas y URLs que incluyan credenciales. La compatibilidad
real depende del sitio y puede cambiar cuando este modifica su reproductor.

## Descargar una playlist

El módulo `local.playlist_downloader` descarga playlists públicas compatibles con
`yt-dlp` y crea un archivo `playlist.m3u` junto con los elementos descargados:

```text
/playlist audio https://www.youtube.com/playlist?list=ID
/playlist video https://www.youtube.com/playlist?list=ID
```

Puedes limitar el número de elementos y elegir formato y calidad:

```text
/playlist audio <url> --limite 20 --formato mp3 --calidad 192K
/playlist video <url> --limite 10 --formato mp4 --calidad 720
```

Audio admite `mp3`, `m4a` u `opus`; video admite `mp4`, `webm` o `mkv`. El límite
predeterminado es 25 y se ajusta desde la configuración del módulo. El archivo M3U
usa rutas relativas, por lo que se conserva al mover la carpeta completa.

## Gestor de tareas personal

El módulo `local.tasks` guarda tareas y notas privadas por usuario. Desde consola,
la interfaz y Telegram puedes usar:

```text
/pendiente Comprar pan --categoria Compras --color pink --importancia alta
/agenda
/hecho 1
/quitar_pendiente 1 confirmar
```

Los colores disponibles son `yellow`, `pink`, `purple`, `blue`, `green` y `orange`;
la importancia puede ser `baja`, `media` o `alta`. Si no indicas `--categoria`, la
tarea queda en **General**. La sección **Mis tareas** de la interfaz web agrupa las
notas adhesivas por categoría y permite crear, editar, completar y eliminar tareas.

## Programador y recordatorios

El módulo `local.automation` ejecuta comandos futuros y envía recordatorios por el
canal desde el que fueron creados. Una hora sola se repite cada día; una fecha y hora
se ejecuta una sola vez. También se admiten intervalos:

```text
/programar "08:00" /agenda
/programar "2026-09-15 09:00" /hola
/programar cada 2h /recuerdos
/programaciones
/desprogramar 1
/recordar_en 30m llamar a Juan
/recordar_el 2026-09-15 09:00 renovar licencia
```

En Telegram, el resultado o recordatorio llega al mismo chat. En la interfaz local se
registra como trabajo completado en **Diagnóstico**. La zona horaria predeterminada es
`America/Lima` y se puede cambiar en la configuración del módulo.

## Finanzas personales

El módulo `local.finance` registra gastos, ingresos y facturas de forma local. Desde
consola, la interfaz o Telegram puedes usar:

```text
/gasto 25.50 supermercado --categoria Alimentación --fecha 2026-09-10
/gasto 45.00 internet --mensual --pendiente
/ingreso 1000 sueldo --categoria Trabajo --fecha 2026-09-01
/factura 80 internet --vencimiento 2026-09-20
/finanzas 2026-09
/movimientos 2026-09
/pagar_gasto 4
/marcar_gasto 4 pendiente
/pagar_factura 3
```

La vista **Finanzas** muestra una tabla por mes, totales de ingresos, gastos, facturas
pagadas y pendientes, además del balance. Un gasto creado con `--mensual` genera una
ocurrencia pendiente cada mes; su check **Pagado** debe activarse para que ese mes se
descuente del balance. Por defecto, la interfaz local y Telegram comparten la identidad
financiera `local-user`; puedes cambiarla o desactivar esta vinculación desde las opciones
del módulo. Los datos se guardan en la base local de ModulAI. El registro desde imágenes
El procesamiento de imágenes OCR ya está disponible mediante el módulo `local.ocr`; la
clasificación automática de gastos a partir del texto se añadirá en una entrega posterior.
fotografías.

## Transcripción local de audio

El módulo `local.transcription` añade `/transcribir` y aparece activo al iniciar. En la
interfaz local, selecciona un archivo de audio con el botón **Audio**: ModulAI lo carga
localmente y crea el trabajo de transcripción. En Telegram, una nota de voz se transcribe
automáticamente; el texto de la descripción se conserva como contexto. Los formatos aceptados incluyen WAV, MP3,
M4A, OGG/OGA, FLAC, AAC, WEBM y MP4; el límite predeterminado es 100 MB.

Instala el motor en el mismo entorno que usa `run.cmd`:

```bat
.venv-win\Scripts\python.exe -m pip install faster-whisper
```

El primer uso descarga el modelo configurado (`small` por defecto) y trabaja en CPU por defecto.
Puedes ajustar modelo, dispositivo, tipo de cómputo, idioma y límite desde la configuración del módulo; reinicia
ModulAI después de guardar esos cambios.

En Windows, ModulAI usa el almacén de certificados del sistema mediante `truststore`, por
lo que las descargas del modelo y Telegram respetan los certificados confiables de tu red.

## Instalación para desarrollo

Con una instalación de Python que incluya `pip`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
modulai --command "/hola"
```

## Pruebas

Desde CMD:

```bat
set "PYTHONPATH=src"
.venv-win\Scripts\python.exe -m unittest discover -s tests -v
```

Desde PowerShell con otro Python compatible:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Cuando estén instaladas las dependencias de desarrollo también pueden ejecutarse
con `pytest`.

## API local

La documentación interactiva se encuentra en `http://127.0.0.1:8765/api/docs` mientras
la interfaz está activa. Las rutas actuales son:

- `GET /api/v1/health`;
- `GET /api/v1/commands`;
- `GET /api/v1/modules`;
- `GET /api/v1/config`;
- `PUT /api/v1/config`;
- `PUT /api/v1/modules/{module_id}/config`;
- `GET /api/v1/memories`;
- `DELETE /api/v1/memories/{memory_id}`;
- `POST /api/v1/messages`.

El servidor solo admite direcciones locales durante esta entrega.

La sección **Configuración** de la UI crea `config/app.toml` desde
`config/app.example.toml` si todavía no existe. Permite editar aplicación, módulos,
logging, servidor local y Telegram. Los cambios se validan antes de reemplazar el
archivo y requieren reiniciar ModulAI para aplicarse.

## Activar Telegram

1. Crea un bot mediante [@BotFather](https://t.me/BotFather) y copia su token.
2. Copia `.env.example` como `.env` y completa `MODULAI_TELEGRAM_TOKEN` y
   `MODULAI_TELEGRAM_ACTIVATION_PASSWORD`.
3. Copia `config/app.example.toml` como `config/app.toml`.
4. Cambia `[telegram]` a `enabled = true`. Puedes dejar `allowed_user_ids = []`:
   el bot pedirá la contraseña al primer usuario y lo autorizará automáticamente.
5. Inicia ModulAI con `run.cmd --web` o `run.cmd`.

El adaptador usa long polling, por lo que no necesita abrir un puerto público. Telegram
queda desactivado por defecto. La contraseña nunca se guarda; los usuarios autorizados
se persisten en `data/telegram/authorized_users.json`, que está excluido de Git. Los
mensajes de usuarios fuera de la allowlist se detienen hasta completar la activación.
La librería `python-telegram-bot` se instala con:

```bat
.venv-win\Scripts\python.exe -m pip install "python-telegram-bot>=22,<23"
```

Si la red tarda en conectar con Telegram, ajusta `network_timeout_seconds` en la
sección `[telegram]` (por defecto, 30 segundos; rango permitido: 5 a 120) desde
la pantalla de configuración. Reinicia ModulAI después de guardarlo.

## Configuración

`config/app.example.toml` contiene los valores predeterminados. Para personalizarlos,
cópialo como `config/app.toml`; este último está excluido de Git.

Los datos de ejecución se escriben bajo `data/` y tampoco se versionan. Git protege
el código, no sustituye una copia de seguridad de la base de datos.

## Crear un módulo

Cada módulo vive en una carpeta con `module.json` y un paquete Python bajo `src/`.
El módulo de ejemplo `modules/hello` muestra el contrato mínimo. Los módulos se
detectan al iniciar y sus errores quedan contenidos para no impedir el arranque. Es
posible copiar un módulo nuevo a una ruta de `[modules].paths` después de distribuir la
aplicación, pero se requiere reiniciarla: todavía no existe instalación ni recarga en
caliente desde la interfaz.
