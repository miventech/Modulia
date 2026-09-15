
# ModulAI — Plan de producto y arquitectura

## 1. Resumen ejecutivo

ModulAI será un asistente personal, privado y modular, escrito principalmente en Python. Su utilidad inicial no dependerá de inteligencia artificial: el núcleo recibirá mensajes, reconocerá comandos explícitos y delegará el trabajo en módulos instalados manualmente. La IA se añadirá después como una capacidad opcional capaz de responder mensajes y solicitar comandos registrados, sin convertirse en una dependencia del núcleo.

La aplicación se ejecutará inicialmente de forma manual en Windows y ofrecerá una interfaz visual local. Los distintos medios de comunicación —interfaz local, consola, WebSocket, API HTTP, Telegram y eventualmente WhatsApp— serán adaptadores independientes. Cualquier combinación de adaptadores podrá estar activa al mismo tiempo según la configuración.

La primera función útil será la transcripción local de audio. La arquitectura quedará preparada para tareas en segundo plano, cancelación, programación por fecha u hora, conversaciones explícitas, memoria persistente, auditoría y métricas.

## Estado de implementación

Actualmente están implementadas las Entregas 0 y 1, el administrador de trabajos y la
primera versión de la Entrega 2: transcripción local mediante `faster-whisper`, carga de
audio desde la UI, progreso y cancelación. También están disponibles los adaptadores de
YouTube y Telegram. Telegram permanece desactivado por defecto hasta que se configure el
token y la allowlist del usuario.

## 2. Objetivos

### 2.1 Objetivos principales

- Obtener cuanto antes una versión local y visual que ejecute comandos reales.
- Mantener el núcleo independiente de Telegram, WhatsApp, WebSocket, la UI y cualquier proveedor de IA.
- Permitir instalar módulos copiándolos a uno o más directorios configurables.
- Detectar automáticamente los módulos al iniciar la aplicación.
- Permitir activar, desactivar y configurar módulos desde la UI.
- Admitir varios canales activos simultáneamente.
- Ejecutar trabajos largos sin bloquear la recepción de nuevos mensajes.
- Registrar comandos, resultados, errores, duración y uso de recursos.
- Mantener la posibilidad de publicar el proyecto y aceptar módulos de terceros en el futuro.

### 2.2 No objetivos de la primera versión

- Marketplace de módulos.
- Recarga de módulos sin reiniciar.
- Síntesis de voz.
- Continuidad automática de una conversación entre canales.
- Roles o permisos complejos.
- Recuperación de trabajos interrumpidos tras cerrar o colgarse la aplicación.
- Aislamiento seguro de módulos no confiables.
- Un asistente de IA incluido obligatoriamente.
- Despliegue público multiusuario.

## 3. Principios de diseño

1. **Primero comandos, después IA.** El comportamiento esencial debe ser determinista, comprobable y utilizable sin conexión a un modelo.
2. **Núcleo pequeño.** El núcleo coordina mensajes, comandos, tareas, configuración y persistencia; las funciones concretas viven en módulos.
3. **Capacidades, no tipos rígidos de plugin.** Un mismo módulo puede registrar comandos, manejadores de mensajes, trabajos programados o adaptadores, pero cada capacidad usa una interfaz separada.
4. **Dependencias débiles.** Los módulos no importan directamente otros módulos. Solicitan capacidades o ejecutan comandos mediante el núcleo.
5. **Configuración validada.** Toda configuración tiene un esquema, valores predeterminados y mensajes de error comprensibles.
6. **Seguro por defecto.** Los servidores locales escuchan solo en `127.0.0.1`; los canales externos utilizan listas de usuarios permitidos; los secretos no se guardan en Git.
7. **Errores contenidos.** El fallo de un módulo no debe derribar otros canales ni el núcleo.
8. **Evolución incremental.** La primera entrega debe ser pequeña y útil, sin implementar anticipadamente un sistema distribuido.

## 4. Alcance funcional

### 4.1 Comandos

Los comandos comienzan con `/` y se resuelven antes que cualquier manejador conversacional.

Ejemplos:

```text
/ayuda
/modulos
/transcribir archivo.wav
/tareas
/cancelar 01J...
/conversacion
/fin_conversacion
/no_olvidar Prefiero los resúmenes cortos
/recuerdos
```

Reglas:

- Un comando tiene nombre canónico, alias, descripción, esquema de argumentos y función ejecutora.
- Los nombres canónicos usarán minúsculas, números y guion bajo, sin espacios ni tildes.
- El analizador conservará el texto original para parámetros libres.
- Si el nombre no existe, el usuario recibirá una sugerencia y podrá consultar `/ayuda`.
- Ningún mensaje se enviará a un módulo de IA antes de comprobar si es un comando.
- Los módulos podrán declarar si un comando es rápido o debe convertirse en trabajo de fondo.

### 4.2 Mensajes que no son comandos

El flujo será configurable:

1. Si existe una conversación explícita activa, se consultan los manejadores conversacionales habilitados.
2. Si no existe una conversación activa, solo se consultan módulos que acepten mensajes libres y estén configurados para ello.
3. Los manejadores se evalúan por prioridad.
4. El primero que reclame el mensaje produce la respuesta.
5. Si nadie lo reclama, el núcleo responde indicando que espera un comando.

Un futuro módulo de IA será un manejador conversacional. Podrá solicitar capacidades del núcleo o invocar comandos autorizados, pero no importará ni controlará directamente otros módulos.

### 4.3 Conversaciones

- `/conversacion` abre una sesión explícita en el canal y conversación actuales.
- `/fin_conversacion` la cierra.
- Cada canal mantiene sus propias conversaciones; no habrá continuidad automática entre canales.
- La clave inicial de conversación será `(canal, id_externo_de_conversacion)`.
- El historial se persistirá en SQLite.
- Cada módulo conversacional decidirá cuánto historial consumir, respetando límites globales.

### 4.4 Memoria persistente

- `/no_olvidar <texto>` guarda una nota permanente.
- `/recuerdos` muestra las notas guardadas.
- `/olvidar <id>` elimina una nota concreta con confirmación.
- Inicialmente las notas pertenecen al único usuario local, pero el modelo de datos conservará un `principal_id` para futuras cuentas.
- Guardar una nota y guardar el historial son operaciones diferentes.
- La memoria no se inyectará automáticamente en todos los módulos; cada módulo deberá solicitarla mediante una interfaz del núcleo.

### 4.5 Trabajos en segundo plano

- Los comandos largos devuelven inmediatamente un identificador de trabajo.
- El usuario puede consultar `/tareas` y cancelar con `/cancelar <id>`.
- Los cambios de estado son: `queued`, `running`, `succeeded`, `failed` y `cancelled`.
- La cancelación será cooperativa: el módulo debe comprobar la señal de cancelación y cerrar procesos externos.
- El resultado se entrega por el mismo canal que originó el trabajo.
- Los trabajos no se reanudarán después de reiniciar la aplicación.
- El estado final y la auditoría sí permanecerán en SQLite.

### 4.6 Programación de tareas

La programación será una capacidad modular, no una responsabilidad fija del núcleo. Un módulo futuro podrá:

- Ejecutar un comando en una fecha y hora.
- Ejecutarlo con una repetición definida.
- Listar, pausar y eliminar programaciones.
- Publicar el resultado en el canal configurado.

El programador llamará al mismo bus de comandos que los canales. No ejecutará funciones internas de otros módulos directamente.

## 5. Arquitectura propuesta

### 5.1 Estilo

Se recomienda un **monolito modular asíncrono**. Todos los componentes vivirán inicialmente en un mismo proceso de Python. Esta opción permite avanzar rápido, depurar con facilidad y mantener interfaces que más adelante podrían moverse a procesos separados si fuera necesario.

```text
Telegram ─────┐
WhatsApp* ────┤
WebSocket ────┤      ┌───────────────┐      ┌──────────────────┐
API HTTP ─────┼─────▶│ Message Router │─────▶│ Command Registry │
UI local ─────┤      └───────┬───────┘      └────────┬─────────┘
Consola ──────┘              │                       │
                             ▼                       ▼
                    Conversation Router       Job Manager
                             │                       │
                             └──────────┬────────────┘
                                        ▼
                         Modules / Capability Registry
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
                  SQLite           Event Bus          Observability

* WhatsApp requiere infraestructura externa adicional.
```

### 5.2 Componentes del núcleo

#### Bootstrap

- Carga configuración global.
- Configura logs y base de datos.
- Descubre y valida módulos.
- Construye los registros de capacidades.
- Inicia módulos y adaptadores habilitados.
- Coordina el apagado ordenado.

#### Module Manager

- Busca módulos en directorios configurados.
- Lee sus manifiestos sin ejecutar código innecesario.
- Valida versión, identificador y compatibilidad.
- Resuelve el orden de inicio mediante capacidades opcionales.
- Aísla errores de carga y los muestra en la UI.
- Activa o desactiva módulos para el siguiente reinicio.

#### Message Router

- Recibe un mensaje normalizado desde cualquier adaptador.
- Aplica autenticación o lista de permitidos.
- Registra la entrada en auditoría.
- Decide entre comando y mensaje libre.
- Devuelve una o varias respuestas mediante el adaptador de origen.

#### Command Registry y Command Bus

- Mantiene los comandos registrados por el núcleo y los módulos.
- Impide nombres duplicados, salvo alias explícitos.
- Valida argumentos.
- Ejecuta comandos rápidos o crea trabajos.
- Permite que módulos como IA y scheduler invoquen comandos sin conocer su implementación.

#### Conversation Manager

- Abre y cierra sesiones.
- Persiste mensajes e historial.
- Entrega contexto al manejador conversacional seleccionado.
- Mantiene aisladas las conversaciones de cada canal.

#### Job Manager

- Ejecuta trabajos mediante `asyncio`.
- Limita la concurrencia global y por módulo.
- Expone progreso y cancelación.
- Conserva metadatos y resultados finales.
- En Windows, encapsula correctamente procesos externos para poder cancelarlos.

#### Event Bus

- Publica eventos internos tipados como `message.received`, `command.started`, `job.progress` y `job.finished`.
- Permite observación y extensiones sin acoplar componentes.
- Es local y en memoria durante el MVP; no requiere RabbitMQ, Redis ni Kafka.

#### Storage

- Expone repositorios y transacciones, evitando SQL disperso en el código.
- Usa SQLite en modo WAL.
- Aplica migraciones versionadas.
- Permite sustituir SQLite más adelante sin prometer compatibilidad automática con PostgreSQL.

#### Observability

- Logs estructurados y legibles.
- Auditoría persistente.
- Métricas locales de comandos, duración, errores y uso de modelos cuando se incorporen.
- Pantalla de diagnóstico en la UI.

## 6. Modelo común de mensajes

Todos los canales convertirán su entrada al mismo contrato antes de llegar al núcleo.

```python
@dataclass(frozen=True, slots=True)
class InboundMessage:
    id: str
    channel: str
    conversation_id: str
    principal_id: str
    text: str | None
    attachments: tuple[Attachment, ...]
    received_at: datetime
    metadata: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    text: str | None = None
    attachments: tuple[Attachment, ...] = ()
    reply_to: str | None = None
```

Los metadatos propios de Telegram o WhatsApp no deben filtrarse a los comandos normales. Si un módulo necesita datos específicos de un canal, deberá declarar esa dependencia de capacidad de forma explícita.

## 7. Contrato de módulos

### 7.1 Descubrimiento

Los directorios configurados contendrán una carpeta por módulo:

```text
modules/
└── transcription/
    ├── module.json
    └── src/
        └── modulai_transcription/
            ├── __init__.py
            └── plugin.py
```

El manifiesto `module.json` describe el módulo, pero no contiene secretos ni estado editable:

```json
{
  "schema_version": 1,
  "id": "local.transcription",
  "name": "Transcripción local",
  "version": "0.1.0",
  "entrypoint": "modulai_transcription.plugin:create_module",
  "requires_core": ">=0.1,<0.2",
  "capabilities": ["command", "background_job"],
  "config_schema": "config.schema.json"
}
```

### 7.2 Configuración editable

La configuración no se escribirá dentro de la carpeta del módulo. Se almacenará en:

```text
data/config/modules/<module_id>.json
```

Esto permite actualizar o reemplazar un módulo sin perder su configuración. El esquema JSON del módulo permitirá a la UI generar formularios, validaciones, descripciones y valores predeterminados.

Cada configuración incluirá como mínimo:

- `enabled`.
- Límites de concurrencia.
- Opciones propias del módulo.
- Referencias a secretos mediante variables, nunca valores secretos expuestos en el manifiesto.

### 7.3 Interfaz conceptual

```python
class Module(Protocol):
    manifest: ModuleManifest

    async def setup(self, context: ModuleContext) -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class ModuleContext(Protocol):
    commands: CommandRegistrar
    conversations: ConversationRegistrar
    events: EventBus
    jobs: JobService
    storage: ModuleStorage
    capabilities: CapabilityResolver
    logger: Logger
```

`setup()` registra capacidades; `start()` inicia recursos; `stop()` los libera. El núcleo nunca dependerá de clases concretas de un módulo.

### 7.4 Dependencias entre módulos

No se permitirán dependencias directas por importación. Se adoptarán estas reglas:

- Un módulo puede declarar capacidades requeridas u opcionales.
- El núcleo resuelve una capacidad por identificador y versión.
- Si falta una capacidad obligatoria, el módulo no inicia y la UI explica el motivo.
- El futuro módulo de IA usa el catálogo y bus de comandos.
- El programador usa el bus de comandos.
- Ningún módulo accede a tablas privadas de otro módulo.

### 7.5 Confianza y seguridad

En el MVP los módulos se ejecutarán dentro del proceso y tendrán los permisos del usuario de Windows. Por lo tanto, **instalar un módulo equivale a ejecutar código confiable en la computadora**. El manifiesto no constituye un sandbox.

El aislamiento en procesos, firma de paquetes y permisos detallados se estudiarán únicamente antes de admitir un ecosistema público de terceros.

## 8. Canales y adaptadores

### 8.1 Interfaz visual local

Para avanzar rápido y conservar un camino natural hacia una web futura, se recomienda:

- Backend local con FastAPI.
- UI web servida por la propia aplicación en `127.0.0.1`.
- HTML, CSS y JavaScript sencillos durante el MVP, sin adoptar todavía un framework pesado.
- Apertura automática del navegador al arrancar, configurable.
- Posibilidad futura de envolver la UI con un contenedor de escritorio si se desea un ejecutable con ventana propia.

Pantallas mínimas:

1. **Chat/comandos:** entrada, respuestas, adjuntos y progreso de trabajos.
2. **Módulos:** instalados, habilitados, errores y configuración generada desde esquema.
3. **Tareas:** estado, progreso, cancelación y resultados.
4. **Conversaciones y memoria:** sesiones, historial y notas persistentes.
5. **Diagnóstico:** canales activos, auditoría, métricas y logs recientes.

### 8.2 Consola

- Adaptador simple para desarrollo y recuperación.
- Lee comandos desde la terminal y muestra respuestas.
- Debe poder deshabilitarse.
- No sustituye a la UI, pero reduce el tiempo de depuración.

### 8.3 WebSocket

- Comparte el servidor local de FastAPI.
- Escucha solo en `127.0.0.1` por defecto.
- Usa mensajes JSON versionados.
- Requiere token si se configura una interfaz distinta de loopback.
- Mantiene separación entre clientes mediante `conversation_id`.
- Implementa ping/pong, tamaño máximo y cierre controlado.

### 8.4 API HTTP

- Expone salud, comandos disponibles, envío de comandos, consulta de trabajos y configuración permitida.
- No expondrá secretos.
- Servirá también como contrato para la UI local.
- Tendrá OpenAPI generado por FastAPI, aunque la API pública estable queda fuera del MVP.

### 8.5 Telegram

- Adaptador mediante bot y long polling para evitar infraestructura pública.
- Lista configurable de identificadores de usuario o chat permitidos.
- Descarga adjuntos en un área temporal administrada.
- Entrega actualizaciones de trabajos largos al chat original.
- El token se obtiene desde una variable de entorno o almacén seguro.

### 8.6 WhatsApp

WhatsApp se tratará como adaptador, pero no bloqueará la primera entrega. La integración oficial suele requerir una cuenta empresarial, credenciales externas y un webhook HTTPS accesible públicamente. Para una aplicación ejecutada manualmente en Windows habrá que decidir entre:

- un túnel HTTPS temporal durante el desarrollo; o
- un pequeño relay desplegado públicamente que reenvíe eventos al equipo local.

No se recomienda automatizar WhatsApp Web ni depender de APIs no oficiales para la base del proyecto, por fragilidad y riesgo de bloqueo. Esta decisión se revisará en una sesión dedicada antes de implementar el adaptador.

## 9. Módulo inicial: transcripción local

### 9.1 Responsabilidad

El módulo recibirá un archivo de audio, lo validará, iniciará un trabajo local, publicará progreso cuando sea posible y devolverá el texto transcrito.

Comando inicial:

```text
/transcribir <archivo o adjunto>
```

### 9.2 Configuración sugerida

```json
{
  "enabled": true,
  "engine": "faster-whisper",
  "model": "small",
  "device": "cpu",
  "compute_type": "auto",
  "language": "auto",
  "max_file_mb": 100,
  "max_duration_seconds": 3600,
  "allowed_extensions": ["wav", "mp3", "m4a", "ogg", "flac"],
  "retain_input": false,
  "retain_output": true,
  "concurrency": 1
}
```

La elección concreta del motor se validará durante la implementación contra el hardware disponible. `faster-whisper` es la primera opción de diseño, no una dependencia irrevocable.

### 9.3 Gestión de archivos

- Los adjuntos entran en `data/inbox` o en un directorio temporal.
- Se calcula identificador y tamaño antes de procesarlos.
- El módulo comprueba extensión, MIME, límite de tamaño y, cuando sea posible, duración.
- La política de retención se lee de la configuración del módulo.
- Los nombres proporcionados por canales externos nunca se usan directamente como rutas.
- Los archivos temporales se eliminan incluso después de errores o cancelaciones.

### 9.4 Criterios de aceptación

- Transcribe al menos los formatos configurados que soporte el motor elegido.
- No congela la UI mientras trabaja.
- Muestra estado y resultado.
- Permite solicitar cancelación.
- Rechaza archivos fuera de límites con un mensaje claro.
- Cumple la política de retención.
- Registra duración, éxito o error sin registrar secretos.

## 10. Persistencia

### 10.1 SQLite

Se usará un archivo local, por defecto `data/modulai.db`, con migraciones. Tablas conceptuales:

| Tabla | Propósito |
|---|---|
| `principals` | Identidad normalizada del usuario o sistema. |
| `channel_identities` | Relación entre usuario y su identidad en cada canal. |
| `conversations` | Sesiones abiertas o cerradas por canal. |
| `messages` | Historial de mensajes y respuestas. |
| `memories` | Notas persistentes creadas con `/no_olvidar`. |
| `jobs` | Estado y resultado de trabajos. |
| `audit_events` | Quién ejecutó qué, cuándo y con qué resultado. |
| `metric_events` | Duración, errores, tamaños y uso futuro de IA. |
| `schema_migrations` | Versión de la base de datos. |

Los módulos con datos propios recibirán almacenamiento bajo su espacio de nombres. No podrán crear tablas con nombres genéricos ni depender del esquema privado de otro módulo.

### 10.2 Copias y control de versiones

Git versionará código, manifiestos, esquemas, documentación, migraciones y configuraciones de ejemplo. Git **no es una copia de seguridad adecuada para datos de ejecución**. Por ello:

- `data/`, bases SQLite, audios, logs y secretos estarán ignorados por Git.
- El MVP no automatizará backups, conforme al alcance decidido.
- La UI deberá indicar dónde están los datos para que el usuario pueda copiarlos manualmente.
- Antes de depender de la memoria como información crítica deberá añadirse exportación o backup real.

## 11. Configuración y secretos

### 11.1 Archivos

Propuesta:

```text
config/
├── app.example.toml
└── logging.example.toml

data/config/
├── app.toml
└── modules/
    └── local.transcription.json
```

- TOML para configuración global legible.
- JSON para configuraciones de módulos, alineado con sus JSON Schema.
- La aplicación crea configuraciones editables a partir de ejemplos y valores predeterminados.
- Los cambios que afecten el ciclo de vida de un módulo se aplican en el siguiente reinicio durante el MVP.

### 11.2 Secretos

Para el MVP:

- Variables de entorno cargadas opcionalmente desde `.env` local.
- `.env` se excluye de Git; solo se versiona `.env.example` sin valores.
- Las configuraciones de módulos contienen referencias como `${MODULAI_TELEGRAM_TOKEN}`.
- La UI enmascara secretos y nunca los devuelve mediante la API.

Como mejora posterior, Windows Credential Manager o un keyring multiplataforma podrá sustituir el archivo `.env` sin cambiar los contratos de los módulos.

## 12. Concurrencia y límites

- Se usará `asyncio` para coordinación y E/S.
- El trabajo pesado de CPU o procesos externos no se ejecutará directamente en el event loop.
- Habrá un límite global configurable de trabajos y un límite por módulo.
- El valor inicial recomendado para transcripción es una tarea simultánea para evitar agotar RAM o GPU.
- Los canales podrán recibir mensajes mientras un trabajo está activo.
- SQLite usará un escritor controlado y transacciones breves.
- No se implementará procesamiento distribuido en el MVP.

## 13. Seguridad

Aunque el asistente sea personal, se aplicarán controles mínimos:

- UI, WebSocket y API enlazados a loopback de forma predeterminada.
- Rechazo al iniciar si se intenta exponer el servidor sin autenticación configurada.
- Lista de permitidos para Telegram y posteriormente WhatsApp.
- Validación de rutas y nombres de archivos.
- Límites configurables de tamaño, duración y concurrencia.
- Auditoría de comandos y cambios de configuración.
- Secretos redactados en logs y respuestas.
- CORS cerrado a los orígenes locales configurados.
- Protección contra carga duplicada de módulos e identificadores manipulados.
- Confirmación para operaciones destructivas como `/olvidar`.

No habrá un sistema de roles en el MVP: el usuario autorizado tiene control total. Si se publica como producto, este supuesto deberá cambiar antes de permitir más usuarios.

## 14. Observabilidad

### 14.1 Logs

- Salida legible en consola durante desarrollo.
- Archivo rotativo en `data/logs`.
- Campos estructurados: timestamp, nivel, componente, módulo, canal, conversation ID, job ID y event ID.
- Texto completo de mensajes permitido según la decisión actual, con advertencia visible de privacidad.
- Tokens, cabeceras de autorización y contenido de `.env` siempre redactados.

### 14.2 Auditoría

Cada ejecución guardará:

- Identidad y canal de origen.
- Comando y argumentos normalizados.
- Momento de inicio y fin.
- Módulo responsable.
- Resultado, error o cancelación.
- Referencias a archivos, evitando duplicar contenido binario.

### 14.3 Métricas

- Cantidad de mensajes y comandos.
- Éxitos, errores y cancelaciones.
- Duración por comando y módulo.
- Cola y concurrencia de trabajos.
- Tamaños y duración de audios.
- En el futuro: proveedor, modelo, tokens, latencia y costo de IA.

Las métricas serán locales; no se enviará telemetría externa por defecto.

## 15. Estructura propuesta del repositorio

```text
ModulAI/
├── pyproject.toml
├── README.md
├── PLAN.md
├── .gitignore
├── .env.example
├── config/
│   ├── app.example.toml
│   └── logging.example.toml
├── src/
│   └── modulai/
│       ├── __main__.py
│       ├── bootstrap.py
│       ├── core/
│       │   ├── commands.py
│       │   ├── conversations.py
│       │   ├── events.py
│       │   ├── jobs.py
│       │   ├── messages.py
│       │   └── modules.py
│       ├── application/
│       │   ├── command_bus.py
│       │   ├── message_router.py
│       │   └── services.py
│       ├── infrastructure/
│       │   ├── config/
│       │   ├── logging/
│       │   └── sqlite/
│       ├── adapters/
│       │   ├── console/
│       │   ├── http/
│       │   ├── telegram/
│       │   └── websocket/
│       └── ui/
│           ├── static/
│           └── templates/
├── modules/
│   └── transcription/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── contract/
└── data/                     # generado y excluido de Git
```

WhatsApp no tendrá carpeta hasta tomar la decisión de infraestructura. Los módulos instalados externamente también podrán vivir fuera del repositorio mediante `module_paths`.

## 16. Tecnologías candidatas

Las versiones exactas se fijarán al comenzar la implementación.

| Área | Elección inicial |
|---|---|
| Lenguaje | Python 3.10 o superior; migrar el entorno de desarrollo a 3.12 cuando sea práctico. |
| Gestión del proyecto | `pyproject.toml`; seleccionar un gestor reproducible durante el bootstrap. |
| Modelos y validación | Pydantic. |
| API y WebSocket | FastAPI con servidor ASGI. |
| UI local | HTML/CSS/JavaScript servidos localmente. |
| Base de datos | SQLite y migraciones versionadas. |
| HTTP saliente | Cliente asíncrono. |
| Telegram | Librería asíncrona compatible con long polling. |
| Transcripción | Motor local, con `faster-whisper` como candidato inicial. |
| Pruebas | pytest y soporte asíncrono. |
| Calidad | Linter/formateador y análisis de tipos configurados desde el inicio. |
| Empaquetado futuro | Ejecutable o instalador de Windows, fuera del primer corte. |

No se fijará una librería únicamente por popularidad; cada elección se comprobará en Windows y con el hardware real antes de bloquear versiones.

## 17. Estrategia de pruebas

### 17.1 Unitarias

- Análisis de comandos, comillas, adjuntos y texto libre.
- Registro de comandos y detección de duplicados.
- Ciclo de vida de módulos.
- Enrutamiento conversacional por prioridad.
- Transiciones y cancelación de trabajos.
- Validación y migración de configuración.
- Políticas de retención de archivos.

### 17.2 Contratos

- Todo adaptador debe convertir entradas al modelo común.
- Todo módulo debe cumplir `setup/start/stop`.
- Los errores de un módulo deben quedar contenidos.
- Las respuestas conservan el canal y conversación de origen.
- Los esquemas JSON producen configuración válida.

### 17.3 Integración

- FastAPI + SQLite + módulo de ejemplo.
- WebSocket con autenticación y límites.
- Telegram simulado sin acceder a la red.
- Transcripción con archivo corto de prueba y motor sustituible.
- Inicio con módulo inválido sin caída del núcleo.

### 17.4 Pruebas manuales de aceptación

- Arrancar desde Windows con un comando documentado.
- Abrir la UI local automáticamente.
- Ver módulos y modificar su configuración.
- Enviar un audio, observar progreso y recibir texto.
- Cancelar una transcripción.
- Abrir y cerrar una conversación.
- Guardar, listar y eliminar un recuerdo.
- Consultar auditoría y métricas.
- Reiniciar y comprobar que historial, recuerdos y configuración permanecen.

## 18. Entregas y prioridades

El objetivo “para ya” se traduce en cortes verticales utilizables, no en implementar todos los canales antes de probar el núcleo.

### Entrega 0 — Bootstrap del repositorio

- Inicializar Git, `.gitignore`, proyecto Python y estructura mínima.
- Añadir configuración, logging y pruebas básicas.
- Documentar instalación y ejecución en Windows.
- Definir contratos de mensajes, módulos y comandos.

**Salida:** aplicación vacía que inicia, valida configuración y se apaga correctamente.

### Entrega 1 — Primer asistente local usable

- UI local y adaptador de consola.
- Message Router, Command Registry y SQLite.
- Comandos `/ayuda`, `/modulos`, `/conversacion`, `/fin_conversacion`, `/no_olvidar`, `/recuerdos` y `/olvidar`.
- Descubrimiento de módulos y pantalla de configuración.
- Auditoría y logs.

**Salida:** asistente local visual con comandos, conversación explícita y memoria.

### Entrega 2 — Transcripción local

- Job Manager, progreso y cancelación.
- Módulo de transcripción.
- Carga de audio desde UI.
- Límites y retención configurables.
- Métricas de ejecución.

**Salida:** producto mínimo realmente útil.

### Entrega 3 — Integraciones locales y remotas sencillas

- WebSocket y endpoints HTTP necesarios.
- Autenticación local cuando corresponda.
- [x] Telegram con long polling, allowlist y adjuntos.
- [ ] WebSocket.
- Pruebas de contrato compartidas entre adaptadores.

**Salida:** el mismo núcleo opera por UI, consola, WebSocket y Telegram.

### Entrega 4 — Automatización

- Módulo programador.
- Ejecución de comandos por fecha, hora o repetición.
- Gestión visual de programaciones.
- Entrega de resultados al canal elegido.

**Salida:** comandos automatizables sin incorporar IA.

### Entrega 5 — Decisión e integración de WhatsApp

- Elegir API oficial, túnel o relay.
- Implementar verificación y recepción de webhooks.
- Configurar allowlist y manejo de adjuntos.
- Documentar requisitos externos y operación.

**Salida:** WhatsApp usa los mismos contratos que los demás canales.

### Entrega 6 — IA opcional

- Contrato de proveedor intercambiable.
- Uno o más módulos de proveedor.
- Manejador conversacional habilitable.
- Catálogo de comandos visible para el modelo.
- Confirmaciones y límites para acciones peligrosas.
- Métricas de tokens, costo y latencia.

**Salida:** ModulAI puede conversar y solicitar comandos sin acoplar el núcleo a un proveedor.

## 19. Definición del MVP

El MVP queda completado al finalizar la Entrega 2. Debe incluir:

- Ejecución manual en Windows.
- UI visual local.
- Consola de desarrollo.
- Descubrimiento y configuración de módulos.
- Comandos deterministas.
- Conversaciones explícitas e historial.
- Memoria con `/no_olvidar`.
- SQLite.
- Trabajos en segundo plano y cancelación.
- Transcripción local de audio.
- Auditoría, logs y métricas básicas.

WebSocket, scheduler, WhatsApp e IA son incrementos posteriores y no retrasan la primera versión útil. Telegram ya está disponible como integración opcional.

## 20. Criterios globales de aceptación

- El núcleo inicia aunque no haya módulos opcionales instalados.
- Instalar un módulo válido requiere copiarlo a un directorio configurado y reiniciar.
- Un módulo inválido aparece como error sin cerrar la aplicación.
- Activar o desactivar un módulo no requiere editar código.
- Los canales activos pueden convivir y comparten comandos sin lógica duplicada.
- Un trabajo largo no bloquea la UI ni otros mensajes.
- Los trabajos se pueden consultar y cancelar.
- Las conversaciones de dos canales nunca se mezclan.
- Los recuerdos sobreviven a reinicios.
- Los secretos no aparecen en Git, logs, auditoría ni respuestas de API.
- La UI muestra de forma clara el estado de módulos, canales y trabajos.
- Existe documentación reproducible para instalar, ejecutar y probar en Windows.

## 21. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Intentar soportar todos los canales desde el comienzo | Retrasa el primer resultado útil. | Cortes verticales: local, transcripción, Telegram y luego WhatsApp. |
| Módulos de terceros ejecutados en proceso | Acceso total al equipo. | Solo módulos confiables en el MVP; diseñar aislamiento antes de publicar ecosistema. |
| Modelos locales pesados | Alto consumo de RAM/GPU y mala experiencia. | Detección de hardware, modelos configurables y concurrencia inicial de uno. |
| Cancelar procesos de audio en Windows | Procesos huérfanos o archivos bloqueados. | Abstracción de proceso, cierre explícito y pruebas específicas de Windows. |
| SQLite bajo mucha escritura concurrente | Bloqueos temporales. | WAL, transacciones breves y escritor controlado. |
| Logs con conversaciones completas | Riesgo de privacidad. | Opción visible para desactivar contenido y rotación/eliminación configurable. |
| API o WebSocket expuestos accidentalmente | Control remoto no autorizado. | Loopback por defecto y rechazo de exposición sin autenticación. |
| WhatsApp exige infraestructura pública | No funciona como app puramente local. | Sesión técnica dedicada y adaptador posterior al MVP. |
| Dependencias entre módulos | Acoplamiento y orden de carga frágil. | Resolver capacidades mediante el núcleo, nunca imports directos. |
| Usar Git como único backup | Pérdida de base de datos e historial. | Advertencia explícita y futura exportación/backup antes de uso crítico. |

## 22. Decisiones registradas

| Decisión | Estado | Motivo |
|---|---|---|
| Python como plataforma principal | Aceptada | Ecosistema adecuado para automatización, audio e IA futura. |
| Monolito modular asíncrono | Aceptada | Reduce complejidad inicial sin cerrar evolución. |
| Windows primero | Aceptada | Entorno de uso actual. |
| Compatibilidad inicial con Python 3.10 | Decisión de implementación | Permite reutilizar FastAPI y Uvicorn ya disponibles en el entorno Windows actual. |
| Ejecución manual | Aceptada | Un servicio permanente no es necesario todavía. |
| UI web local | Propuesta aceptada por criterio técnico | Ofrece interfaz visual y camino hacia una web futura. |
| IA fuera del núcleo | Aceptada | La mayoría de funciones serán comandos deterministas. |
| Comandos con `/` | Aceptada | Comportamiento explícito y predecible. |
| Manejadores de texto libre configurables | Aceptada | Permite IA u otros módulos sin forzar su uso. |
| Conversaciones separadas por canal | Aceptada | No se requiere continuidad entre canales. |
| Instalación manual desde directorios | Aceptada | Es simple y prepara un marketplace futuro sin implementarlo. |
| Un módulo puede aportar varias capacidades | Decisión técnica | Evita jerarquías rígidas manteniendo interfaces separadas. |
| Dependencias mediante capacidades | Decisión técnica | Permite que IA y scheduler usen otros comandos sin acoplamiento. |
| SQLite | Aceptada | Adecuado para una aplicación local y privada. |
| Secretos mediante referencias de entorno | Decisión técnica | Implementación rápida y compatible con una mejora a keyring. |
| Loopback como red predeterminada | Decisión técnica | El uso externo aún no está definido; se elige el valor seguro. |
| Sin recuperación de trabajos | Aceptada | Evita complejidad que no aporta al primer uso. |
| Logs con contenido de mensajes | Aceptada con advertencia | Facilita diagnóstico, pero debe ser configurable por privacidad. |
| Git para código, no para datos | Aclaración técnica | Una base SQLite ignorada no queda respaldada por Git. |
| WhatsApp después del MVP | Decisión técnica | Requiere infraestructura y credenciales adicionales. |
| Telegram con long polling | Aceptada | Evita webhook público y encaja con la ejecución manual en Windows. |

## 23. Decisiones diferidas

Estas preguntas se resolverán en la entrega que las necesite:

- Motor y tamaño predeterminado de transcripción según CPU, RAM y GPU reales.
- Gestor de dependencias y estrategia final de empaquetado para Windows.
- Proveedor oficial e infraestructura para WhatsApp.
- Sintaxis exacta del módulo programador.
- Protocolo de aprobación cuando un futuro módulo de IA invoque acciones peligrosas.
- Aislamiento y firma de módulos de terceros.
- Framework de frontend solo si la UI supera lo razonable para JavaScript sencillo.
- Estrategia de exportación y backup de datos.

## 24. Siguiente paso recomendado

Comenzar únicamente la Entrega 0 y validar una demostración técnica mínima:

1. Iniciar el núcleo en Windows.
2. Descubrir un módulo de ejemplo desde `modules/`.
3. Registrar `/hola` desde ese módulo.
4. Ejecutarlo desde consola y desde una página local.
5. Guardar el evento en SQLite.
6. Detener núcleo y módulo limpiamente.

Si este recorrido funciona, las fronteras esenciales de la arquitectura estarán probadas antes de incorporar audio, Telegram o IA.
