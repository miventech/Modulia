from __future__ import annotations

import asyncio
import importlib.util
import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from modulai.core.commands import CommandContext, CommandDefinition, CommandResult, ParsedCommand
from modulai.core.jobs import JobContext, JobOutcome
from modulai.core.modules import ModuleContext

AUDIO_FORMATS = {"mp3", "m4a", "opus"}
VIDEO_FORMATS = {"mp4", "webm", "mkv"}
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}
SAFE_FOLDER = re.compile(r"[^a-zA-Z0-9._-]+")


@dataclass(frozen=True, slots=True)
class PlaylistRequest:
    media_type: str
    url: str
    limit: int | None = None
    output_format: str | None = None
    quality: str | None = None


class PlaylistDownloaderModule:
    def __init__(self) -> None:
        self._context: ModuleContext | None = None

    async def setup(self, context: ModuleContext) -> None:
        self._context = context
        context.commands.register(
            CommandDefinition(
                name="playlist",
                description="Descarga una playlist como audio o video y crea un archivo M3U.",
                handler=self.download_playlist,
            )
        )

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def download_playlist(
        self,
        context: CommandContext,
        command: ParsedCommand,
    ) -> CommandResult:
        request, error = parse_playlist_request(command.args)
        if error:
            return CommandResult.failure(error)
        assert request is not None
        url_error = validate_public_url(request.url)
        if url_error:
            return CommandResult.failure(url_error)
        if importlib.util.find_spec("yt_dlp") is None:
            return CommandResult.failure(
                "Falta yt-dlp. Instálalo en el entorno activo con: python -m pip install yt-dlp"
            )
        if self._context is None:
            return CommandResult.failure("El descargador de playlists no está inicializado.")
        max_items = int(self._context.config.get("max_items", 25))
        if max_items < 1:
            return CommandResult.failure("max_items debe ser mayor que cero en la configuración del módulo.")
        if request.limit is not None and request.limit > max_items:
            return CommandResult.failure(
                f"El límite solicitado ({request.limit}) supera el máximo configurado ({max_items})."
            )

        record = self._context.jobs.submit(
            label=f"Playlist de {request.media_type}",
            runner=lambda job_context: self._run_download(job_context, request),
            metadata={
                "channel": context.message.channel,
                "conversation_id": context.message.conversation_id,
                "principal_id": context.message.principal_id,
                "artifact_type": f"playlist_{request.media_type}",
            },
        )
        return CommandResult.success(
            f"Trabajo {record.id} creado. Usa /tareas para consultar el progreso. "
            f"Se descargarán como máximo {request.limit or max_items} elementos."
        )

    async def _run_download(self, job: JobContext, request: PlaylistRequest) -> JobOutcome:
        job.report_progress(1.0, "Analizando playlist")
        return await asyncio.to_thread(self._download_blocking, job, request)

    def _download_blocking(self, job: JobContext, request: PlaylistRequest) -> JobOutcome:
        import yt_dlp

        if self._context is None:
            raise RuntimeError("El módulo no tiene contexto")
        configuration = self._context.config
        max_items = int(configuration.get("max_items", 25))
        limit = request.limit or max_items
        info_options: dict[str, object] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "discard_in_playlist",
            "playlistend": limit,
        }
        job.report_progress(3.0, "Consultando elementos de la playlist")
        with yt_dlp.YoutubeDL(info_options) as inspector:
            playlist = inspector.extract_info(request.url, download=False)
        entries = playlist.get("entries") if isinstance(playlist, dict) else None
        if not isinstance(entries, (list, tuple)):
            raise LookupError("La URL no corresponde a una playlist compatible.")
        available_entries = [entry for entry in entries if isinstance(entry, dict)]
        if not available_entries:
            raise LookupError("La playlist no contiene elementos descargables.")
        item_count = len(available_entries)
        job.report_progress(5.0, f"Preparando {item_count} elemento(s) de la playlist")
        playlist_title = str(playlist.get("title", "playlist"))
        playlist_id = str(playlist.get("id", "playlist"))
        folder_name = safe_folder_name(f"{playlist_id} - {playlist_title}")
        output_root = self._output_root(configuration)
        output_dir = output_root / folder_name
        output_dir.mkdir(parents=True, exist_ok=True)
        output_format, quality = self._resolve_format_and_quality(request, configuration)
        options = self._download_options(
            job,
            request.media_type,
            output_dir,
            output_format,
            quality,
            item_count,
        )
        ffmpeg_location = str(configuration.get("ffmpeg_location", "")).strip()
        if ffmpeg_location:
            options["ffmpeg_location"] = ffmpeg_location

        with yt_dlp.YoutubeDL(options) as downloader:
            downloaded = downloader.extract_info(request.url, download=True)
        job.check_cancelled()
        artifacts = find_playlist_artifacts(output_dir, request.media_type, output_format)
        if not artifacts:
            raise FileNotFoundError("yt-dlp no produjo archivos para la playlist.")
        job.report_progress(97.0, "Organizando archivos descargados")
        m3u_path = output_dir / "playlist.m3u"
        write_m3u(m3u_path, artifacts)
        count = len(artifacts)
        job.report_progress(99.0, "Generando playlist M3U")
        title = str(downloaded.get("title", playlist_title)) if isinstance(downloaded, dict) else playlist_title
        return JobOutcome(
            text=f"Playlist lista: {title} ({count} archivo(s)).",
            artifacts=(*map(str, artifacts), str(m3u_path)),
        )

    def _output_root(self, configuration: dict[str, object]) -> Path:
        output_dir = Path(str(configuration.get("output_dir", "downloads/playlists")))
        if not output_dir.is_absolute():
            assert self._context is not None
            output_dir = self._context.data_dir / output_dir
        return output_dir

    def _resolve_format_and_quality(
        self,
        request: PlaylistRequest,
        configuration: dict[str, object],
    ) -> tuple[str, str]:
        if request.media_type == "audio":
            return (
                request.output_format or str(configuration.get("audio_format", "mp3")),
                request.quality or str(configuration.get("audio_quality", "192K")),
            )
        return (
            request.output_format or str(configuration.get("video_format", "mp4")),
            request.quality or str(configuration.get("video_quality", "best")),
        )

    def _download_options(
        self,
        job: JobContext,
        media_type: str,
        output_dir: Path,
        output_format: str,
        quality: str,
        item_count: int,
    ) -> dict[str, object]:
        options: dict[str, object] = {
            "outtmpl": str(output_dir / "%(playlist_index)03d - %(title)s.%(ext)s"),
            "windowsfilenames": True,
            "restrictfilenames": True,
            "noplaylist": False,
            "playlistend": item_count,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [self._progress_hook(job, media_type, item_count)],
        }
        if media_type == "audio":
            options.update(
                {
                    "format": "bestaudio/best",
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": output_format,
                            "preferredquality": quality.removesuffix("K"),
                        }
                    ],
                }
            )
        else:
            format_selector = "bestvideo*+bestaudio/best"
            if quality != "best":
                format_selector = f"bestvideo*[height<={quality}]+bestaudio/best[height<={quality}]"
            options.update(
                {
                    "format": format_selector,
                    "merge_output_format": output_format,
                }
            )
        return options

    def _progress_hook(self, job: JobContext, media_type: str, item_count: int):
        completed = 0

        def hook(status: dict[str, object]) -> None:
            nonlocal completed
            job.check_cancelled()
            state = status.get("status")
            if state == "downloading":
                total = status.get("total_bytes") or status.get("total_bytes_estimate")
                current = status.get("downloaded_bytes", 0)
                track_progress = float(current) / float(total) if isinstance(total, (int, float)) and total else 0.0
                progress = min(95.0, ((completed + track_progress) / item_count) * 95.0)
                job.report_progress(
                    progress,
                    f"Descargando {media_type} {min(completed + 1, item_count)}/{item_count}",
                )
            elif state == "finished":
                completed += 1
                job.report_progress(
                    min(95.0, (completed / item_count) * 95.0),
                    f"Procesando {media_type} {completed}/{item_count}",
                )

        return hook


def parse_playlist_request(args: tuple[str, ...]) -> tuple[PlaylistRequest | None, str | None]:
    usage = "Uso: /playlist <audio|video> <url> [--limite N] [--formato formato] [--calidad valor]"
    if len(args) < 2:
        return None, usage
    media_type, url = args[0].lower(), args[1].strip()
    if media_type not in {"audio", "video"}:
        return None, "El primer argumento debe ser audio o video. " + usage
    values: dict[str, str] = {}
    index = 2
    flags = {"--limite": "limit", "--formato": "output_format", "--calidad": "quality"}
    while index < len(args):
        flag = args[index].lower()
        field = flags.get(flag)
        if field is None or index + 1 >= len(args):
            return None, usage
        if field in values:
            return None, f"La opción {flag} solo puede indicarse una vez."
        values[field] = args[index + 1]
        index += 2
    limit: int | None = None
    if "limit" in values:
        try:
            limit = int(values["limit"])
        except ValueError:
            return None, "--limite debe ser un número entero positivo."
        if limit < 1:
            return None, "--limite debe ser mayor que cero."
    output_format = values.get("output_format", "").lower() or None
    allowed_formats = AUDIO_FORMATS if media_type == "audio" else VIDEO_FORMATS
    if output_format is not None and output_format not in allowed_formats:
        return None, f"El formato para {media_type} debe ser uno de: {', '.join(sorted(allowed_formats))}."
    quality = values.get("quality")
    if media_type == "audio" and quality is not None and quality.upper() not in {"128K", "192K", "256K", "320K"}:
        return None, "La calidad de audio debe ser 128K, 192K, 256K o 320K."
    if media_type == "video" and quality is not None and quality.lower() not in {"best", "1080", "720", "480"}:
        return None, "La calidad de video debe ser best, 1080, 720 o 480."
    return PlaylistRequest(media_type, url, limit, output_format, quality.upper() if media_type == "audio" and quality else quality), None


def validate_public_url(value: str) -> str | None:
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


def safe_folder_name(value: str) -> str:
    cleaned = SAFE_FOLDER.sub("_", value).strip("._")
    return cleaned[:120] or "playlist"


def find_playlist_artifacts(output_dir: Path, media_type: str, output_format: str) -> tuple[Path, ...]:
    if media_type == "audio":
        extensions = {f".{output_format}"}
    else:
        extensions = VIDEO_EXTENSIONS if output_format == "mp4" else {f".{output_format}"}
    return tuple(sorted(path for path in output_dir.iterdir() if path.is_file() and path.suffix.lower() in extensions))


def write_m3u(path: Path, tracks: tuple[Path, ...]) -> None:
    content = "#EXTM3U\n" + "\n".join(track.name for track in tracks) + "\n"
    path.write_text(content, encoding="utf-8", newline="\n")


def create_module() -> PlaylistDownloaderModule:
    return PlaylistDownloaderModule()
