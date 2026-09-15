from __future__ import annotations

import asyncio
import importlib.util
import re
from pathlib import Path
from urllib.parse import urlparse

from modulai.core.commands import CommandContext, CommandDefinition, CommandResult, ParsedCommand
from modulai.core.jobs import JobContext, JobOutcome
from modulai.core.modules import ModuleContext

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
INVALID_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


class YouTubeAudioModule:
    def __init__(self) -> None:
        self._context: ModuleContext | None = None

    async def setup(self, context: ModuleContext) -> None:
        self._context = context
        context.commands.register(
            CommandDefinition(
                name="audio",
                description="Busca o descarga un video de YouTube y lo convierte a MP3.",
                handler=self.download_audio,
            )
        )

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def download_audio(
        self,
        context: CommandContext,
        command: ParsedCommand,
    ) -> CommandResult:
        search_or_url = " ".join(command.args).strip()
        if not search_or_url:
            return CommandResult.failure("Uso: /audio <url o nombre de canción>")
        source, error = resolve_youtube_source(search_or_url)
        if error:
            return CommandResult.failure(error)
        if importlib.util.find_spec("yt_dlp") is None:
            return CommandResult.failure(
                "Falta yt-dlp. Instálalo en el entorno activo con: python -m pip install yt-dlp"
            )
        if self._context is None:
            return CommandResult.failure("El módulo de YouTube no está inicializado.")

        record = self._context.jobs.submit(
            label="Audio de YouTube",
            runner=lambda job_context: self._run_download(job_context, source),
            metadata={
                "channel": context.message.channel,
                "conversation_id": context.message.conversation_id,
                "principal_id": context.message.principal_id,
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
        output_value = str(configuration.get("output_dir", "downloads/youtube"))
        output_dir = Path(output_value)
        if not output_dir.is_absolute() and self._context:
            output_dir = self._context.data_dir / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        quality = str(configuration.get("audio_quality", "192K"))
        ffmpeg_location = str(configuration.get("ffmpeg_location", "")).strip()
        options: dict[str, object] = {
            "format": "bestaudio/best",
            "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": quality.removesuffix("K"),
                }
            ],
            "progress_hooks": [self._progress_hook(job)],
        }
        if ffmpeg_location:
            options["ffmpeg_location"] = ffmpeg_location

        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
            entries = info.get("entries")
            if isinstance(entries, (list, tuple)):
                info = next((entry for entry in entries if isinstance(entry, dict)), None)
                if info is None:
                    raise LookupError("No se encontraron resultados en YouTube")
            downloaded_path = Path(downloader.prepare_filename(info))
        job.check_cancelled()
        video_id = str(info.get("id", "audio"))
        temporary_mp3_path = downloaded_path.with_suffix(".mp3")
        if not temporary_mp3_path.is_file():
            raise FileNotFoundError("yt-dlp no produjo el archivo MP3 esperado")
        title = str(info.get("title", video_id))
        mp3_path = unique_output_path(output_dir, sanitize_filename(title), ".mp3")
        if temporary_mp3_path != mp3_path:
            temporary_mp3_path.replace(mp3_path)
        return JobOutcome(text=f"Audio listo: {title}", artifacts=(str(mp3_path),))

    def _progress_hook(self, job: JobContext):
        def hook(status: dict[str, object]) -> None:
            job.check_cancelled()
            state = status.get("status")
            if state == "downloading":
                total = status.get("total_bytes") or status.get("total_bytes_estimate")
                downloaded = status.get("downloaded_bytes", 0)
                if isinstance(total, (int, float)) and total:
                    percent = float(downloaded) / float(total) * 92.0
                    job.report_progress(percent, "Descargando audio")
            elif state == "finished":
                job.report_progress(94.0, "Convirtiendo a MP3")

        return hook


def validate_youtube_url(value: str) -> str | None:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or host not in YOUTUBE_HOSTS:
        return "La URL debe pertenecer a YouTube (youtube.com o youtu.be)."
    if not parsed.path or parsed.path == "/":
        return "La URL de YouTube no contiene un video identificable."
    return None


def resolve_youtube_source(value: str) -> tuple[str, str | None]:
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return value, validate_youtube_url(value)
    return f"ytsearch1:{value}", None


def sanitize_filename(value: str) -> str:
    sanitized = INVALID_FILENAME_CHARACTERS.sub(" - ", value)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" .-")
    if not sanitized:
        return "audio"
    if sanitized.upper() in WINDOWS_RESERVED_NAMES:
        return f"{sanitized}_"
    return sanitized[:180].rstrip(" .-") or "audio"


def unique_output_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    sequence = 2
    while candidate.exists():
        candidate = directory / f"{stem} ({sequence}){suffix}"
        sequence += 1
    return candidate


def create_module() -> YouTubeAudioModule:
    return YouTubeAudioModule()
