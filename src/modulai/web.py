from __future__ import annotations

import asyncio
import mimetypes
import re
import webbrowser
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from modulai.bootstrap import Application
from modulai.core.messages import Attachment, InboundMessage
from modulai.infrastructure.config import read_config_values, write_config_values


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    conversation_id: str = Field(default="local-ui", min_length=1, max_length=200)
    principal_id: str = Field(default="local-user", min_length=1, max_length=200)


class TaskCreateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    color: str = Field(default="yellow", max_length=20)
    importance: str = Field(default="media", max_length=20)
    category: str = Field(default="General", min_length=1, max_length=60)
    principal_id: str = Field(default="local-user", min_length=1, max_length=200)


class TaskUpdateRequest(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    color: str | None = Field(default=None, max_length=20)
    importance: str | None = Field(default=None, max_length=20)
    category: str | None = Field(default=None, min_length=1, max_length=60)
    completed: bool | None = None
    principal_id: str = Field(default="local-user", min_length=1, max_length=200)


class FinanceCreateRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=20)
    description: str = Field(min_length=1, max_length=500)
    amount: str = Field(min_length=1, max_length=20)
    category: str = Field(default="General", min_length=1, max_length=60)
    occurred_on: str = Field(default_factory=lambda: date.today().isoformat(), max_length=10)
    due_on: str | None = Field(default=None, max_length=10)
    status: str | None = Field(default=None, max_length=20)
    recurring: bool = False
    principal_id: str = Field(default="local-user", min_length=1, max_length=200)


class FinanceUpdateRequest(BaseModel):
    kind: str | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    amount: str | None = Field(default=None, min_length=1, max_length=20)
    category: str | None = Field(default=None, min_length=1, max_length=60)
    occurred_on: str | None = Field(default=None, max_length=10)
    due_on: str | None = Field(default=None, max_length=10)
    status: str | None = Field(default=None, max_length=20)
    recurring: bool | None = None
    principal_id: str = Field(default="local-user", min_length=1, max_length=200)


SAFE_UPLOAD_NAME = re.compile(r"[^a-zA-Z0-9._-]+")
HARD_MAX_AUDIO_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MEDIA_EXTENSIONS = {
    "image": {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"},
    "video": {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"},
    "audio": {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".weba"},
}
TRANSCRIPT_DIRECTORY = "transcriptions"
MAX_TRANSCRIPT_PREVIEW_CHARS = 20_000
TASK_COLORS = {"yellow", "pink", "purple", "blue", "green", "orange"}
TASK_IMPORTANCE = {"baja", "media", "alta"}
FINANCE_KINDS = {"expense", "income", "invoice"}
FINANCE_STATUSES = {"registered", "pending", "paid"}


def create_web_app(application: Application) -> FastAPI:
    web_app = FastAPI(
        title="ModulAI Local API",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
    )
    ui_directory = Path(__file__).with_name("ui")

    @web_app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=400,
            content={
                "data": None,
                "error": {"code": "validation_error", "details": error.errors()},
                "meta": {},
            },
        )

    @web_app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.status_code,
            content={
                "data": None,
                "error": {"code": "http_error", "details": error.detail},
                "meta": {},
            },
        )

    @web_app.middleware("http")
    async def local_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @web_app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(ui_directory / "index.html")

    @web_app.get("/app.css", include_in_schema=False)
    async def stylesheet() -> FileResponse:
        return FileResponse(ui_directory / "app.css", media_type="text/css")

    @web_app.get("/app.js", include_in_schema=False)
    async def javascript() -> FileResponse:
        return FileResponse(ui_directory / "app.js", media_type="application/javascript")

    @web_app.get("/api/v1/health")
    async def health() -> dict[str, object]:
        return _success(
            {
                "status": "ok",
                "name": application.config.name,
                "active_modules": application.modules.active_count,
                "module_errors": application.modules.error_count,
                "telegram_enabled": application.config.telegram.enabled,
                "telegram_active": application._telegram_adapter is not None,
                "telegram_error": application.telegram_error,
            }
        )

    @web_app.get("/api/v1/config")
    async def get_config() -> dict[str, object]:
        try:
            values, created = read_config_values(application.root)
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=500,
                detail="No se pudo leer la configuración",
            ) from error
        return _success(
            {
                "values": values,
                "created_from_example": created,
                "restart_required": False,
            }
        )

    @web_app.put("/api/v1/config")
    async def update_config(payload: dict[str, object]) -> dict[str, object]:
        try:
            write_config_values(application.root, payload)
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _success(
            {
                "saved": True,
                "restart_required": True,
                "message": "Configuración guardada. Reinicia ModulAI para aplicarla.",
            }
        )

    @web_app.get("/api/v1/commands")
    async def commands() -> dict[str, object]:
        return _success(
            [
                {
                    "name": definition.name,
                    "description": definition.description,
                    "aliases": list(definition.aliases),
                }
                for definition in application.commands.definitions
            ]
        )

    @web_app.get("/api/v1/modules")
    async def modules() -> dict[str, object]:
        return _success(
            [
                {
                    "id": record.manifest.id,
                    "name": record.manifest.name,
                    "version": record.manifest.version,
                    "capabilities": list(record.manifest.capabilities),
                    "status": record.status,
                    "error": record.error,
                    "config": record.config or {},
                    "config_schema": record.config_schema or {},
                }
                for record in application.modules.records
            ]
        )

    @web_app.put("/api/v1/modules/{module_id}/config")
    async def update_module_config(
        module_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        try:
            record = application.modules.update_config(module_id, payload)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _success(
            {
                "id": record.manifest.id,
                "config": record.config,
                "restart_required": True,
            }
        )

    @web_app.get("/api/v1/ai/models")
    async def ai_models() -> dict[str, object]:
        record = next(
            (item for item in application.modules.records if item.manifest.id == "local.opencode_ai"),
            None,
        )
        if record is None or record.instance is None or not callable(getattr(record.instance, "list_models", None)):
            raise HTTPException(
                status_code=409,
                detail="Activa local.opencode_ai y reinicia ModulAI antes de cargar modelos.",
            )
        try:
            models = await record.instance.list_models()
        except (OSError, ValueError, TimeoutError) as error:
            raise HTTPException(status_code=502, detail=f"No se pudieron cargar los modelos: {error}") from error
        return _success(models)

    @web_app.get("/api/v1/memories")
    async def memories(principal_id: str = "local-user") -> dict[str, object]:
        records = application.audit.list_memories(principal_id)
        return _success(
            [
                {"id": record.id, "text": record.text, "created_at": record.created_at}
                for record in records
            ]
        )

    @web_app.get("/api/v1/tasks")
    async def tasks(
        principal_id: str = "local-user",
        include_completed: bool = True,
    ) -> dict[str, object]:
        return _success(
            [_task_payload(item) for item in application.audit.list_tasks(principal_id, include_completed)]
        )

    @web_app.post("/api/v1/tasks")
    async def create_task(payload: TaskCreateRequest) -> dict[str, object]:
        _validate_task_values(payload.color, payload.importance)
        category = _normalize_category(payload.category)
        task = application.audit.add_task(
            payload.principal_id,
            payload.text.strip(),
            payload.color,
            payload.importance,
            category,
        )
        return _success(_task_payload(task))

    @web_app.put("/api/v1/tasks/{task_id}")
    async def update_task(task_id: int, payload: TaskUpdateRequest) -> dict[str, object]:
        if task_id < 1:
            raise HTTPException(status_code=400, detail="El identificador debe ser positivo")
        _validate_task_values(payload.color, payload.importance)
        category = _normalize_category(payload.category) if payload.category is not None else None
        task = application.audit.update_task(
            payload.principal_id,
            task_id,
            text=payload.text.strip() if payload.text is not None else None,
            color=payload.color,
            importance=payload.importance,
            category=category,
            completed=payload.completed,
        )
        if task is None:
            raise HTTPException(status_code=404, detail="Tarea no encontrada")
        return _success(_task_payload(task))

    @web_app.delete("/api/v1/tasks/{task_id}")
    async def delete_task(task_id: int, principal_id: str = "local-user") -> dict[str, object]:
        if task_id < 1:
            raise HTTPException(status_code=400, detail="El identificador debe ser positivo")
        if not application.audit.delete_task(principal_id, task_id):
            raise HTTPException(status_code=404, detail="Tarea no encontrada")
        return _success({"deleted": task_id})

    @web_app.get("/api/v1/finance")
    async def finance_entries(
        principal_id: str = "local-user",
        month: str | None = None,
    ) -> dict[str, object]:
        normalized_month = _normalize_month(month) if month is not None else None
        entries = application.audit.list_finance_entries(principal_id, normalized_month)
        return _success(
            {
                "entries": [_finance_payload(entry) for entry in entries],
                "summary": _finance_summary(entries),
                "month": normalized_month,
            }
        )

    @web_app.post("/api/v1/finance")
    async def create_finance_entry(payload: FinanceCreateRequest) -> dict[str, object]:
        kind, occurred_on, due_on, status = _validate_finance_values(
            payload.kind,
            payload.occurred_on,
            payload.due_on,
            payload.status,
            payload.recurring,
        )
        entry = application.audit.add_finance_entry(
            payload.principal_id,
            kind,
            payload.description.strip(),
            _parse_amount_cents(payload.amount),
            _normalize_category(payload.category),
            occurred_on,
            due_on,
            status,
            recurring=payload.recurring,
        )
        return _success(_finance_payload(entry))

    @web_app.put("/api/v1/finance/{entry_id}")
    async def update_finance_entry(
        entry_id: int,
        payload: FinanceUpdateRequest,
    ) -> dict[str, object]:
        if entry_id < 1:
            raise HTTPException(status_code=400, detail="El identificador debe ser positivo")
        current = application.audit.get_finance_entry(payload.principal_id, entry_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Movimiento no encontrado")
        kind, occurred_on, due_on, status = _validate_finance_values(
            payload.kind if payload.kind is not None else current.kind,
            payload.occurred_on if payload.occurred_on is not None else current.occurred_on,
            payload.due_on if payload.due_on is not None else current.due_on,
            payload.status if payload.status is not None else current.status,
            payload.recurring if payload.recurring is not None else current.recurring,
        )
        entry = application.audit.update_finance_entry(
            payload.principal_id,
            entry_id,
            kind=kind,
            description=payload.description.strip() if payload.description is not None else None,
            amount_cents=_parse_amount_cents(payload.amount) if payload.amount is not None else None,
            category=_normalize_category(payload.category) if payload.category is not None else None,
            occurred_on=occurred_on,
            due_on=due_on,
            status=status,
            recurring=payload.recurring,
        )
        assert entry is not None
        return _success(_finance_payload(entry))

    @web_app.delete("/api/v1/finance/{entry_id}")
    async def delete_finance_entry(entry_id: int, principal_id: str = "local-user") -> dict[str, object]:
        if entry_id < 1:
            raise HTTPException(status_code=400, detail="El identificador debe ser positivo")
        if not application.audit.delete_finance_entry(principal_id, entry_id):
            raise HTTPException(status_code=404, detail="Movimiento no encontrado")
        return _success({"deleted": entry_id})

    @web_app.get("/api/v1/jobs")
    async def jobs() -> dict[str, object]:
        return _success([_job_payload(job) for job in application.jobs.records])

    @web_app.get("/api/v1/jobs/{job_id}")
    async def job(job_id: str) -> dict[str, object]:
        record = application.jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Trabajo no encontrado")
        return _success(_job_payload(record))

    @web_app.post("/api/v1/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict[str, object]:
        record = application.jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Trabajo no encontrado")
        if not application.jobs.cancel(job_id):
            raise HTTPException(status_code=409, detail="El trabajo ya terminó")
        return _success(_job_payload(record))

    @web_app.get("/api/v1/jobs/{job_id}/artifact/{artifact_index}")
    async def job_artifact(job_id: str, artifact_index: int) -> FileResponse:
        record = application.jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Trabajo no encontrado")
        if artifact_index < 0 or artifact_index >= len(record.artifacts):
            raise HTTPException(status_code=404, detail="Artefacto no encontrado")
        path = Path(record.artifacts[artifact_index]).resolve()
        data_dir = application.config.data_dir.resolve()
        if data_dir not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="Artefacto no encontrado")
        media_type, _ = mimetypes.guess_type(path.name)
        return FileResponse(
            path,
            media_type=media_type or "application/octet-stream",
            filename=path.name,
        )

    @web_app.get("/api/v1/media")
    async def media_library() -> dict[str, object]:
        return _success(_list_media(application.config.data_dir))

    @web_app.get("/api/v1/media/{media_path:path}")
    async def stream_media(media_path: str) -> FileResponse:
        path = _resolve_media_path(application.config.data_dir, media_path)
        media_type, _ = mimetypes.guess_type(path.name)
        return FileResponse(path, media_type=media_type or "application/octet-stream")

    @web_app.delete("/api/v1/media/{media_path:path}")
    async def delete_media(media_path: str) -> dict[str, object]:
        path = _resolve_media_path(application.config.data_dir, media_path)
        try:
            path.unlink()
        except OSError as error:
            raise HTTPException(status_code=409, detail="No se pudo eliminar el archivo") from error
        return _success({"deleted": media_path})

    @web_app.delete("/api/v1/memories/{memory_id}")
    async def delete_memory(
        memory_id: int,
        principal_id: str = "local-user",
    ) -> dict[str, object]:
        if memory_id <= 0:
            raise HTTPException(status_code=400, detail="El identificador debe ser positivo")
        if not application.audit.delete_memory(principal_id, memory_id):
            raise HTTPException(status_code=404, detail="Recuerdo no encontrado")
        return _success({"deleted": memory_id})

    @web_app.post("/api/v1/messages")
    async def send_message(payload: MessageRequest) -> dict[str, object]:
        message = InboundMessage(
            id=str(uuid4()),
            channel="local-ui",
            conversation_id=payload.conversation_id,
            principal_id=payload.principal_id,
            text=payload.text,
            attachments=(),
            received_at=datetime.now(timezone.utc),
        )
        result = await application.execute_message(message)
        return _success({"ok": result.ok, "text": result.text})

    @web_app.get("/api/v1/message-log")
    async def message_log(principal_id: str = "local-user", limit: int = 100, conversation_id: str | None = None) -> dict[str, object]:
        return _success(application.audit.list_messages(principal_id, limit, conversation_id))

    @web_app.post("/api/v1/transcriptions")
    async def transcribe_audio(
        request: Request,
        conversation_id: str = "local-ui",
        principal_id: str = "local-user",
    ) -> dict[str, object]:
        transcription = next(
            (record for record in application.modules.records if record.manifest.id == "local.transcription"),
            None,
        )
        configured_mb = int((transcription.config if transcription else {}).get("max_file_mb", 100))
        max_bytes = min(max(configured_mb, 1) * 1024 * 1024, HARD_MAX_AUDIO_UPLOAD_BYTES)
        declared_size = request.headers.get("content-length")
        if declared_size and declared_size.isdigit() and int(declared_size) > max_bytes:
            raise HTTPException(status_code=413, detail=f"El archivo supera el límite configurado de {configured_mb} MB")
        original_name = request.headers.get("x-modulai-filename", "audio")
        filename = SAFE_UPLOAD_NAME.sub("_", Path(original_name).name)[:120] or "audio"
        destination = application.config.data_dir / "inbox" / "local-ui" / f"{uuid4().hex}_{filename}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with destination.open("wb") as stream:
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_bytes:
                    stream.close()
                    destination.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"El archivo supera el límite configurado de {configured_mb} MB",
                    )
                stream.write(chunk)
        if size == 0:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="No se recibió ningún archivo de audio")
        message = InboundMessage(
            id=str(uuid4()),
            channel="local-ui",
            conversation_id=conversation_id,
            principal_id=principal_id,
            text="/transcribir",
            attachments=(
                Attachment(
                    id=f"local-upload-{uuid4().hex[:12]}",
                    name=filename,
                    path=str(destination),
                    media_type=request.headers.get("content-type"),
                    size_bytes=size,
                ),
            ),
            received_at=datetime.now(timezone.utc),
            metadata={"audio_duration_seconds": _header_duration(request.headers.get("x-modulai-audio-duration"))},
        )
        result = await application.execute_message(message)
        if not result.ok:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        return _success({"ok": result.ok, "text": result.text})

    return web_app


async def serve(application: Application) -> None:
    config = application.config.web
    url = f"http://{config.host}:{config.port}"
    if config.open_browser:
        asyncio.create_task(_open_browser(url))
    server = uvicorn.Server(
        uvicorn.Config(
            create_web_app(application),
            host=config.host,
            port=config.port,
            log_config=None,
            access_log=False,
        )
    )
    application.logger.info("Interfaz local disponible en %s", url)
    await server.serve()


async def _open_browser(url: str) -> None:
    await asyncio.sleep(0.4)
    await asyncio.to_thread(webbrowser.open, url)


def _success(data: object) -> dict[str, object]:
    return {"data": data, "error": None, "meta": {}}


def _header_duration(value: str | None) -> float | None:
    try:
        duration = float(value or "")
    except ValueError:
        return None
    return duration if duration >= 0 else None


def _list_media(data_dir: Path) -> list[dict[str, object]]:
    """Lista únicamente multimedia dentro del almacenamiento administrado por ModulAI."""
    root = data_dir.resolve()
    if not root.is_dir():
        return []
    items: list[dict[str, object]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        kind = _gallery_kind(path, root)
        if kind is None:
            continue
        try:
            relative_path = path.resolve().relative_to(root).as_posix()
            stat = path.stat()
        except (OSError, ValueError):
            continue
        item: dict[str, object] = {
            "id": relative_path,
            "name": path.name,
            "kind": kind,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(
                stat.st_mtime, timezone.utc
            ).isoformat(),
            "url": f"/api/v1/media/{quote(relative_path, safe='/')}",
        }
        if kind == "text":
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            item["text"] = text[:MAX_TRANSCRIPT_PREVIEW_CHARS]
            item["text_truncated"] = len(text) > MAX_TRANSCRIPT_PREVIEW_CHARS
        items.append(item)
    return sorted(items, key=lambda item: str(item["modified_at"]), reverse=True)


def _resolve_media_path(data_dir: Path, media_path: str) -> Path:
    root = data_dir.resolve()
    path = (root / media_path).resolve()
    if (
        root not in path.parents
        or not path.is_file()
        or _gallery_kind(path, root) is None
    ):
        raise HTTPException(status_code=404, detail="Archivo multimedia no encontrado")
    return path


def _media_kind(path: Path) -> str | None:
    extension = path.suffix.lower()
    return next((kind for kind, extensions in MEDIA_EXTENSIONS.items() if extension in extensions), None)


def _gallery_kind(path: Path, root: Path) -> str | None:
    media_kind = _media_kind(path)
    if media_kind is not None:
        return media_kind
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        return None
    if (
        len(relative.parts) > 1
        and relative.parts[0] == TRANSCRIPT_DIRECTORY
        and path.suffix.lower() == ".txt"
    ):
        return "text"
    return None


def _job_payload(record) -> dict[str, object]:
    return {
        "id": record.id,
        "label": record.label,
        "status": record.status,
        "progress": record.progress,
        "detail": record.detail,
        "result": record.result,
        "error": record.error,
        "artifacts": [
            f"/api/v1/jobs/{record.id}/artifact/{index}"
            for index, _ in enumerate(record.artifacts)
        ],
        "created_at": record.created_at,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
    }


def _validate_task_values(color: str | None, importance: str | None) -> None:
    if color is not None and color not in TASK_COLORS:
        raise HTTPException(status_code=400, detail="Color de nota inválido")
    if importance is not None and importance not in TASK_IMPORTANCE:
        raise HTTPException(status_code=400, detail="Importancia de nota inválida")


def _normalize_category(category: str) -> str:
    value = " ".join(category.split())
    if not value:
        raise HTTPException(status_code=400, detail="La categoría no puede estar vacía")
    return value


def _task_payload(task) -> dict[str, object]:
    return {
        "id": task.id,
        "text": task.text,
        "color": task.color,
        "importance": task.importance,
        "category": task.category,
        "completed": task.completed,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
    }


def _normalize_month(value: str) -> str:
    try:
        date.fromisoformat(f"{value}-01")
    except ValueError as error:
        raise HTTPException(status_code=400, detail="El mes debe usar AAAA-MM") from error
    return value


def _parse_amount_cents(value: str) -> int:
    try:
        amount = Decimal(value.replace(",", "."))
    except InvalidOperation as error:
        raise HTTPException(status_code=400, detail="El monto debe ser un número válido") from error
    if amount <= 0 or amount.as_tuple().exponent < -2 or amount > Decimal("999999999.99"):
        raise HTTPException(
            status_code=400,
            detail="El monto debe ser positivo y tener como máximo dos decimales",
        )
    return int(amount * 100)


def _validate_finance_values(
    kind: str,
    occurred_on: str,
    due_on: str | None,
    status: str | None,
    recurring: bool = False,
) -> tuple[str, str, str | None, str]:
    if kind not in FINANCE_KINDS:
        raise HTTPException(status_code=400, detail="Tipo de movimiento inválido")
    try:
        normalized_occurred_on = date.fromisoformat(occurred_on).isoformat()
        normalized_due_on = date.fromisoformat(due_on).isoformat() if due_on else None
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Las fechas deben usar AAAA-MM-DD") from error
    default_status = (
        "pending" if kind == "invoice" or (kind == "expense" and recurring) else
        "paid" if kind == "expense" else "registered"
    )
    normalized_status = status or default_status
    if normalized_status not in FINANCE_STATUSES:
        raise HTTPException(status_code=400, detail="Estado de movimiento inválido")
    if kind == "invoice" and normalized_status not in {"pending", "paid"}:
        raise HTTPException(status_code=400, detail="Una factura debe estar pendiente o pagada")
    if kind == "expense" and normalized_status not in {"pending", "paid"}:
        raise HTTPException(status_code=400, detail="Un gasto debe estar pendiente o pagado")
    if kind == "income" and normalized_status != "registered":
        raise HTTPException(status_code=400, detail="Un ingreso usa estado registrado")
    if recurring and kind != "expense":
        raise HTTPException(status_code=400, detail="Solo los gastos pueden ser mensuales")
    return kind, normalized_occurred_on, normalized_due_on, normalized_status


def _finance_payload(entry) -> dict[str, object]:
    return {
        "id": entry.id,
        "kind": entry.kind,
        "description": entry.description,
        "amount": f"{entry.amount_cents // 100}.{entry.amount_cents % 100:02d}",
        "category": entry.category,
        "occurred_on": entry.occurred_on,
        "due_on": entry.due_on,
        "status": entry.status,
        "paid": entry.status == "paid",
        "recurring": entry.recurring,
        "recurring_parent_id": entry.recurring_parent_id,
        "created_at": entry.created_at,
    }


def _finance_summary(entries) -> dict[str, str]:
    income = sum(entry.amount_cents for entry in entries if entry.kind == "income")
    expenses = sum(
        entry.amount_cents
        for entry in entries
        if entry.kind == "expense" and entry.status == "paid"
    )
    pending_expenses = sum(
        entry.amount_cents
        for entry in entries
        if entry.kind == "expense" and entry.status == "pending"
    )
    paid_invoices = sum(
        entry.amount_cents
        for entry in entries
        if entry.kind == "invoice" and entry.status == "paid"
    )
    pending_invoices = sum(
        entry.amount_cents
        for entry in entries
        if entry.kind == "invoice" and entry.status == "pending"
    )
    return {
        "income": _cents_text(income),
        "expenses": _cents_text(expenses),
        "pending_expenses": _cents_text(pending_expenses),
        "paid_invoices": _cents_text(paid_invoices),
        "pending_invoices": _cents_text(pending_invoices),
        "balance": _cents_text(income - expenses - paid_invoices),
    }


def _cents_text(value: int) -> str:
    sign = "-" if value < 0 else ""
    cents = abs(value)
    return f"{sign}{cents // 100}.{cents % 100:02d}"
