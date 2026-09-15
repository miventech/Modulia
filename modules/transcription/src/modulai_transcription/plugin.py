from __future__ import annotations

import asyncio
import importlib.util
import re
from pathlib import Path

from modulai.core.commands import CommandContext, CommandDefinition, CommandResult, ParsedCommand
from modulai.core.jobs import JobContext, JobOutcome
from modulai.core.modules import ModuleContext

SUPPORTED_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".ogg", ".oga", ".flac", ".aac", ".webm", ".mp4"
}
SAFE_TRANSCRIPT_NAME = re.compile(r"[^a-zA-Z0-9._-]+")


class TranscriptionModule:
    def __init__(self) -> None:
        self._context: ModuleContext | None = None

    async def setup(self, context: ModuleContext) -> None:
        self._context = context
        context.commands.register(
            CommandDefinition(
                name="transcribir",
                description="Transcribe un archivo de audio adjunto con un modelo local.",
                handler=self.transcribe,
            )
        )

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def transcribe(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        if self._context is None:
            return CommandResult.failure("El módulo de transcripción no está inicializado.")
        if importlib.util.find_spec("faster_whisper") is None:
            return CommandResult.failure(
                "Falta faster-whisper. Instálalo con: python -m pip install faster-whisper"
            )
        attachment = next((item for item in context.message.attachments if item.path), None)
        if attachment is None:
            return CommandResult.failure("Adjunta un audio desde la UI y pulsa «Transcribir audio».")
        path = Path(attachment.path or "").resolve()
        error = self._validate_input(path, attachment.size_bytes)
        if error:
            return CommandResult.failure(error)
        context_note = command.raw_args.strip()
        config = self._context.config
        duration = context.message.metadata.get("audio_duration_seconds")
        max_seconds = max(1, int(config.get("ai_forward_max_seconds", 60)))
        record = self._context.jobs.submit(
            label=f"Transcripción: {attachment.name}",
            runner=lambda job: self._run_transcription(job, path, context_note),
            metadata={
                "channel": context.message.channel,
                "conversation_id": context.message.conversation_id,
                "principal_id": context.message.principal_id,
                "context": context_note,
                "artifact_type": "transcription",
                "audio_duration_seconds": duration,
                "forward_to_ai": bool(config.get("forward_to_ai", True)) and isinstance(duration, (int, float)) and duration <= max_seconds,
            },
        )
        return CommandResult.success(
            f"Trabajo {record.id} creado. La transcripción se mostrará al completarse."
        )

    def _validate_input(self, path: Path, known_size: int | None) -> str | None:
        if not path.is_file():
            return "No se encontró el archivo adjunto. Vuelve a cargarlo."
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return "Formato no compatible. Usa WAV, MP3, M4A, OGG/OGA, FLAC, AAC, WEBM o MP4."
        config = self._context.config if self._context else {}
        max_bytes = int(config.get("max_file_mb", 500)) * 1024 * 1024
        size = known_size if known_size is not None else path.stat().st_size
        if size > max_bytes:
            return f"El audio supera el límite configurado de {max_bytes // 1024 // 1024} MB."
        return None

    async def _run_transcription(
        self,
        job: JobContext,
        path: Path,
        context_note: str = "",
    ) -> JobOutcome:
        return await asyncio.to_thread(self._transcribe_blocking, job, path, context_note)

    def _transcribe_blocking(
        self,
        job: JobContext,
        path: Path,
        context_note: str = "",
    ) -> JobOutcome:
        from faster_whisper import WhisperModel

        config = self._context.config if self._context else {}
        configured_device = str(config.get("device", "cpu")).lower()
        configured_compute = str(config.get("compute_type", "auto"))
        job.report_progress(5, "Cargando modelo local")
        try:
            model = WhisperModel(
                str(config.get("model", "small")),
                device=configured_device,
                compute_type=("int8" if configured_device == "cpu" and configured_compute == "auto" else configured_compute),
            )
        except RuntimeError as error:
            detail = str(error).lower()
            if configured_device != "auto" or not any(
                marker in detail for marker in ("cublas", "cuda", "cudnn")
            ):
                raise
            job.report_progress(7, "CUDA no disponible; usando CPU")
            model = WhisperModel(
                str(config.get("model", "small")),
                device="cpu",
                compute_type="int8" if configured_compute == "auto" else configured_compute,
            )
        job.check_cancelled()
        language = str(config.get("language", "")).strip() or None
        segments, info = model.transcribe(
            str(path),
            language=language,
            initial_prompt=context_note or None,
        )
        duration = float(getattr(info, "duration", 0) or 0)
        parts: list[str] = []
        for segment in segments:
            job.check_cancelled()
            text = str(getattr(segment, "text", "")).strip()
            if text:
                parts.append(text)
            end = float(getattr(segment, "end", 0) or 0)
            progress = 10 + (85 * end / duration) if duration else 50
            job.report_progress(progress, "Transcribiendo audio")
        transcript = " ".join(parts).strip()
        if not transcript:
            raise RuntimeError("No se detectó voz en el archivo.")
        context = f"\nContexto recibido: {context_note}" if context_note else ""
        result = f"Transcripción de {path.name}:{context}\n\n{transcript}"
        transcript_path = self._save_transcript(job.id, path, result)
        if not bool(config.get("retain_input", False)):
            try:
                path.unlink()
            except OSError:
                pass
        return JobOutcome(text=result, artifacts=(str(transcript_path),))

    def _save_transcript(self, job_id: str, source: Path, content: str) -> Path:
        if self._context is None:
            raise RuntimeError("El módulo de transcripción no está inicializado.")
        directory = self._context.data_dir / "transcriptions"
        directory.mkdir(parents=True, exist_ok=True)
        safe_stem = SAFE_TRANSCRIPT_NAME.sub("_", source.stem)[:100] or "transcripcion"
        destination = directory / f"{job_id}_{safe_stem}.txt"
        destination.write_text(content, encoding="utf-8")
        return destination


def create_module() -> TranscriptionModule:
    return TranscriptionModule()
