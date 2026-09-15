from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from modulai.core.commands import CommandContext, CommandDefinition, CommandResult, ParsedCommand
from modulai.core.jobs import JobContext, JobOutcome
from modulai.core.modules import ModuleContext

IMAGE_EXTENSIONS = {".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf"}
SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")


class OcrModule:
    def __init__(self) -> None:
        self._context: ModuleContext | None = None

    async def setup(self, context: ModuleContext) -> None:
        self._context = context
        context.commands.register(
            CommandDefinition("ocr", "Extrae texto de una imagen o PDF adjunto.", self.extract_text)
        )
        context.commands.register(
            CommandDefinition("ultimo_ocr", "Devuelve el último OCR procesado, su texto y rutas.", self.latest)
        )

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def extract_text(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        source, known_size, error = self._resolve_source(context, command.raw_args)
        if error:
            return CommandResult.failure(error)
        assert source is not None
        validation = self._validate_input(source, known_size)
        if validation:
            return CommandResult.failure(validation)
        config = self._context.config if self._context else {}
        command_path = self._find_tesseract(str(config.get("tesseract_command", "tesseract")))
        if command_path is None and source.suffix.lower() != ".pdf":
            return CommandResult.failure(
                "No se encontró Tesseract. Instálalo y configura tesseract_command en local.ocr."
            )
        if source.suffix.lower() == ".pdf" and importlib.util.find_spec("fitz") is None:
            return CommandResult.failure("Para OCR de PDF instala PyMuPDF: python -m pip install PyMuPDF")
        assert self._context is not None
        record = self._context.jobs.submit(
            label=f"OCR: {source.name}",
            runner=lambda job: self._run_ocr(job, source, command_path),
            metadata={
                "channel": context.message.channel,
                "conversation_id": context.message.conversation_id,
                "principal_id": context.message.principal_id,
                "artifact_type": "ocr",
                "forward_to_ai": True,
            },
        )
        return CommandResult.success(f"Trabajo {record.id} creado. El texto extraído aparecerá al completarse.")

    def latest(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        del context, command
        if self._context is None:
            return CommandResult.failure("El módulo OCR no está inicializado.")
        directory = self._context.data_dir / "ocr"
        metadata_files = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not metadata_files:
            return CommandResult.failure("Todavía no hay ningún OCR procesado.")
        try:
            metadata = json.loads(metadata_files[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return CommandResult.failure("No se pudo leer el registro del último OCR.")
        result_path = Path(str(metadata.get("result_path", "")))
        text = str(metadata.get("text", "")).strip()
        if result_path.is_file() and not text:
            text = result_path.read_text(encoding="utf-8").strip()
        if len(text) > 12_000:
            text = text[:12_000] + "\n[Texto truncado]"
        return CommandResult.success(
            "Último OCR procesado:\n"
            f"Fecha: {metadata.get('processed_at', 'desconocida')}\n"
            f"Origen: {metadata.get('source_path', 'desconocido')}\n"
            f"Texto: {result_path}\n\n"
            f"Contenido:\n{text}"
        )

    def _resolve_source(self, context: CommandContext, raw_args: str) -> tuple[Path | None, int | None, str | None]:
        attachments = [item for item in context.message.attachments if item.path]
        source = next((Path(item.path or "").resolve() for item in attachments), None)
        known_size = next((item.size_bytes for item in attachments), None)
        if source is None and raw_args.strip():
            candidate = Path(raw_args.strip().strip('"')).expanduser().resolve()
            source, known_size = candidate, None
        if source is None:
            return None, None, "Adjunta una imagen o PDF, o usa: /ocr <ruta del archivo>."
        return source, known_size, None

    def _validate_input(self, path: Path, known_size: int | None) -> str | None:
        if not path.is_file():
            return "No se encontró el archivo. Vuelve a cargarlo o verifica la ruta."
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return "Formato no compatible. Usa PNG, JPG, WEBP, TIFF, BMP o PDF."
        config = self._context.config if self._context else {}
        maximum = max(1, int(config.get("max_file_mb", 20))) * 1024 * 1024
        size = known_size if known_size is not None else path.stat().st_size
        if size > maximum:
            return f"El archivo supera el límite configurado de {maximum // 1024 // 1024} MB."
        return None

    def _find_tesseract(self, configured: str) -> str | None:
        """Busca primero el motor incluido en el módulo y luego instalaciones locales."""
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute() and self._context is not None:
            bundled = (self._context.root / "modules" / "ocr" / candidate).resolve()
            if bundled.is_file():
                return str(bundled)
            module_bundled = (Path(__file__).resolve().parents[2] / "runtime" / "tesseract" / "tesseract.exe")
            if module_bundled.is_file():
                return str(module_bundled)
        if candidate.is_file():
            return str(candidate.resolve())
        found = shutil.which(configured)
        if found:
            return found
        for standard in (
            Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
            Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
        ):
            if standard.is_file():
                return str(standard)
        return None

    async def _run_ocr(self, job: JobContext, path: Path, command_path: str | None) -> JobOutcome:
        return await asyncio.to_thread(self._ocr_blocking, job, path, command_path)

    def _ocr_blocking(self, job: JobContext, path: Path, command_path: str | None) -> JobOutcome:
        job.report_progress(5, "Preparando OCR local")
        if path.suffix.lower() == ".pdf":
            text = self._ocr_pdf(job, path)
        else:
            text = self._run_tesseract(path, command_path)
        if not text.strip():
            raise RuntimeError("No se detectó texto en el archivo.")
        output = self._save_result(job.id, path, text.strip())
        return JobOutcome(text=f"Texto extraído de {path.name}:\n\n{text.strip()}", artifacts=(str(output),))

    def _ocr_pdf(self, job: JobContext, path: Path) -> str:
        import fitz

        config = self._context.config if self._context else {}
        dpi = max(72, min(600, int(config.get("pdf_dpi", 200))))
        parts: list[str] = []
        with fitz.open(path) as document, tempfile.TemporaryDirectory(prefix="modulai_ocr_") as directory:
            if not document.page_count:
                raise RuntimeError("El PDF no contiene páginas.")
            for index, page in enumerate(document, start=1):
                job.check_cancelled()
                pixmap = page.get_pixmap(dpi=dpi, alpha=False)
                image_path = Path(directory) / f"page_{index}.png"
                pixmap.save(str(image_path))
                parts.append(f"--- Página {index} ---\n{self._run_tesseract(image_path, self._find_tesseract(str(config.get('tesseract_command', 'tesseract'))))}")
                job.report_progress(10 + (80 * index / document.page_count), f"Procesando página {index}/{document.page_count}")
        return "\n\n".join(parts)

    def _run_tesseract(self, path: Path, command_path: str | None) -> str:
        if command_path is None:
            raise RuntimeError("No se encontró Tesseract en el sistema.")
        config = self._context.config if self._context else {}
        language = str(config.get("language", "spa+eng")).strip() or "eng"
        result = subprocess.run(
            [command_path, str(path), "stdout", "-l", language],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or "").strip()
            raise RuntimeError(f"Tesseract no pudo procesar el archivo: {detail[:500]}")
        return result.stdout

    def _save_result(self, job_id: str, source: Path, text: str) -> Path:
        assert self._context is not None
        directory = self._context.data_dir / "ocr"
        directory.mkdir(parents=True, exist_ok=True)
        stem = SAFE_NAME.sub("_", source.stem)[:100] or "imagen"
        destination = directory / f"{job_id}_{stem}.txt"
        destination.write_text(text + "\n", encoding="utf-8")
        metadata = {
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "source_path": str(source),
            "result_path": str(destination),
            "text": text,
        }
        destination.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return destination


def create_module() -> OcrModule:
    return OcrModule()
