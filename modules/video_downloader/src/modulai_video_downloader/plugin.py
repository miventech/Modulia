from __future__ import annotations

import asyncio
import importlib.util
import ipaddress
from pathlib import Path
from urllib.parse import urlparse

from modulai.core.commands import CommandContext, CommandDefinition, CommandResult, ParsedCommand
from modulai.core.jobs import JobContext, JobOutcome
from modulai.core.modules import ModuleContext

VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}


class VideoDownloaderModule:
    def __init__(self) -> None:
        self._context: ModuleContext | None = None

    async def setup(self, context: ModuleContext) -> None:
        self._context = context
        context.commands.register(
            CommandDefinition(
                name="video",
                description="Descarga un video desde cualquier URL compatible con yt-dlp.",
                handler=self.download_video,
            )
        )

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def download_video(
        self,
        context: CommandContext,
        command: ParsedCommand,
    ) -> CommandResult:
        if len(command.args) != 1:
            return CommandResult.failure("Uso: /video <url>")
        url = command.args[0].strip()
        error = validate_video_url(url)
        if error:
            return CommandResult.failure(error)
        if importlib.util.find_spec("yt_dlp") is None:
            return CommandResult.failure(
                "Falta yt-dlp. Instálalo en el entorno activo con: python -m pip install yt-dlp"
            )
        if self._context is None:
            return CommandResult.failure("El descargador de videos no está inicializado.")

        record = self._context.jobs.submit(
            label="Descarga de video",
            runner=lambda job_context: self._run_download(job_context, url),
            metadata={
                "channel": context.message.channel,
                "conversation_id": context.message.conversation_id,
                "principal_id": context.message.principal_id,
                "artifact_type": "video",
            },
        )
        return CommandResult.success(
            f"Trabajo {record.id} creado. Usa /tareas para consultar el progreso."
        )

    async def _run_download(self, job: JobContext, url: str) -> JobOutcome:
        if self._context is None:
            raise RuntimeError("El módulo no tiene contexto")
        return await asyncio.to_thread(self._download_blocking, job, url)

    def _download_blocking(self, job: JobContext, url: str) -> JobOutcome:
        import yt_dlp

        configuration = self._context.config if self._context else {}
        output_value = str(configuration.get("output_dir", "downloads/videos"))
        output_dir = Path(output_value)
        if not output_dir.is_absolute() and self._context:
            output_dir = self._context.data_dir / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg_location = str(configuration.get("ffmpeg_location", "")).strip()
        options: dict[str, object] = {
            "format": "bestvideo*+bestaudio/best",
            "merge_output_format": "mp4",
            "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
            "windowsfilenames": True,
            "restrictfilenames": True,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [self._progress_hook(job)],
        }
        if ffmpeg_location:
            options["ffmpeg_location"] = ffmpeg_location

        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
            downloaded_path = Path(downloader.prepare_filename(info))
        job.check_cancelled()
        video_path = find_downloaded_video(downloaded_path)
        title = str(info.get("title", info.get("id", "video")))
        return JobOutcome(text=f"Video listo: {title}", artifacts=(str(video_path),))

    def _progress_hook(self, job: JobContext):
        def hook(status: dict[str, object]) -> None:
            job.check_cancelled()
            state = status.get("status")
            if state == "downloading":
                total = status.get("total_bytes") or status.get("total_bytes_estimate")
                downloaded = status.get("downloaded_bytes", 0)
                if isinstance(total, (int, float)) and total:
                    percent = float(downloaded) / float(total) * 92.0
                    job.report_progress(percent, "Descargando video")
            elif state == "finished":
                job.report_progress(94.0, "Preparando archivo de video")

        return hook


def validate_video_url(value: str) -> str | None:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        return "Ingresa una URL completa que comience con http:// o https://."
    if parsed.username or parsed.password:
        return "La URL no debe incluir credenciales."
    if host == "localhost" or host.endswith(".localhost"):
        return "No se permiten direcciones locales."
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if not address.is_global:
        return "No se permiten direcciones IP privadas, locales o reservadas."
    return None


def find_downloaded_video(prepared_path: Path) -> Path:
    candidates = (prepared_path.with_suffix(".mp4"), prepared_path)
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix.lower() in VIDEO_EXTENSIONS:
            return candidate
    for candidate in prepared_path.parent.glob(f"{prepared_path.stem}.*"):
        if candidate.is_file() and candidate.suffix.lower() in VIDEO_EXTENSIONS:
            return candidate
    raise FileNotFoundError("yt-dlp no produjo el archivo de video esperado")


def create_module() -> VideoDownloaderModule:
    return VideoDownloaderModule()
