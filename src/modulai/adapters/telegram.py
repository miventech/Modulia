from __future__ import annotations

import asyncio
import importlib.util
import hmac
import json
import logging
import os
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from modulai.bootstrap import Application
from modulai.core.jobs import JobRecord
from modulai.core.messages import Attachment, InboundMessage
from modulai.infrastructure.config import TelegramConfig

SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")
TELEGRAM_COMMAND_NAME = re.compile(r"^[a-z0-9_]{1,32}$")
TELEGRAM_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


class TelegramAttachmentError(RuntimeError):
    """El adjunto no puede descargarse mediante la API de Telegram."""


@dataclass(slots=True)
class _ActivationAttempt:
    started_at: float
    attempts: int = 0


class TelegramAuthorization:
    """Gestiona allowlist estática y altas mediante contraseña sin guardar el secreto."""

    def __init__(self, config: TelegramConfig, data_dir: Path) -> None:
        self.config = config
        self.path = data_dir / "telegram" / "authorized_users.json"
        self._authorized = set(config.allowed_user_ids)
        self._authorized.update(self._load_persisted())
        self._password = os.getenv(config.activation_password_env, "").strip()
        self._pending: dict[int, _ActivationAttempt] = {}

    @property
    def has_password(self) -> bool:
        return bool(self._password)

    @property
    def authorized_count(self) -> int:
        return len(self._authorized)

    def is_authorized(self, user_id: int) -> bool:
        return user_id in self._authorized

    def check(self, user_id: int, text: str) -> str:
        if self.is_authorized(user_id):
            return "authorized"
        if not self.has_password:
            return "unavailable"
        now = time.monotonic()
        pending = self._pending.get(user_id)
        if pending is None or now - pending.started_at > self.config.activation_timeout_seconds:
            self._pending[user_id] = _ActivationAttempt(started_at=now)
            return "prompt"
        pending.attempts += 1
        if hmac.compare_digest(text.strip(), self._password):
            self._authorized.add(user_id)
            self._pending.pop(user_id, None)
            self._persist()
            return "authorized_now"
        if pending.attempts >= self.config.max_activation_attempts:
            self._pending.pop(user_id, None)
            return "locked"
        return "invalid"

    def _load_persisted(self) -> set[int]:
        if not self.path.is_file():
            return set()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return set()
            return {
                int(value)
                for value in raw
                if isinstance(value, int) and not isinstance(value, bool)
            }
        except (OSError, ValueError, TypeError):
            return set()

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(sorted(self._authorized), indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


class TelegramAdapter:
    def __init__(
        self,
        application: Application,
        config: TelegramConfig,
        logger: logging.Logger,
    ) -> None:
        self.application = application
        self.config = config
        self.logger = logger
        self._telegram_application = None
        self._authorization: TelegramAuthorization | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        if importlib.util.find_spec("telegram") is None:
            raise RuntimeError(
                "Falta python-telegram-bot. Instálalo con: "
                "python -m pip install python-telegram-bot"
            )
        token = os.getenv(self.config.token_env, "").strip()
        if not token:
            raise RuntimeError(
                f"Telegram está habilitado, pero falta la variable {self.config.token_env}"
            )
        authorization = TelegramAuthorization(self.config, self.application.config.data_dir)
        if not authorization.has_password and authorization.authorized_count == 0:
            raise RuntimeError(
                "Telegram requiere activation_password_env o al menos un allowed_user_ids"
            )

        from telegram import BotCommand
        from telegram.ext import ApplicationBuilder, MessageHandler, filters

        timeout = float(self.config.network_timeout_seconds)
        self._telegram_application = (
            ApplicationBuilder()
            .token(token)
            .connect_timeout(timeout)
            .read_timeout(timeout)
            .write_timeout(timeout)
            .pool_timeout(timeout)
            .build()
        )
        self._authorization = authorization
        self._telegram_application.add_handler(MessageHandler(filters.ALL, self._handle_update))
        self._telegram_application.add_error_handler(self._handle_error)
        self.application.jobs.add_listener(self._on_job_finished)

        try:
            await self._telegram_application.initialize()
            await self._publish_command_menu(BotCommand)
            await self._telegram_application.start()
            if self._telegram_application.updater is None:
                raise RuntimeError("python-telegram-bot no tiene Updater disponible")
            await self._telegram_application.updater.start_polling(
                drop_pending_updates=self.config.drop_pending_updates,
            )
        except Exception:
            with suppress(Exception):
                await self._telegram_application.stop()
            with suppress(Exception):
                await self._telegram_application.shutdown()
            self._telegram_application = None
            raise
        self._started = True
        self.logger.info("Adaptador Telegram iniciado")

    async def _publish_command_menu(self, bot_command_type) -> None:
        """Publica el menú nativo de comandos junto al campo de texto de Telegram."""
        if self._telegram_application is None:
            return
        commands = tuple(
            bot_command_type(name, description)
            for name, description in _telegram_command_menu(self.application.commands.definitions)
        )
        try:
            await self._telegram_application.bot.set_my_commands(commands)
        except Exception as error:
            # El menú es una comodidad: no debe dejar al bot sin servicio si Telegram
            # rechaza temporalmente esta llamada.
            self.logger.warning("No se pudo publicar el menú de comandos de Telegram: %s", error)

    async def stop(self) -> None:
        if not self._started or self._telegram_application is None:
            return
        updater = self._telegram_application.updater
        if updater is not None:
            await updater.stop()
        await self._telegram_application.stop()
        await self._telegram_application.shutdown()
        self._telegram_application = None
        self._authorization = None
        self._started = False
        self.logger.info("Adaptador Telegram detenido")

    async def _handle_update(self, update, telegram_context) -> None:
        del telegram_context
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if message is None or user is None or chat is None:
            return
        if self._authorization is None:
            return
        authorization = self._authorization.check(user.id, message.text or message.caption or "")
        if authorization != "authorized":
            await message.reply_text(_authorization_message(authorization))
            return

        try:
            attachments = await self._download_attachment(message, update.update_id)
        except TelegramAttachmentError as error:
            await message.reply_text(str(error))
            return
        text = message.text or message.caption or ""
        if attachments and not text.strip().startswith("/"):
            attachment = attachments[0]
            is_image = (attachment.media_type or "").startswith("image/") or Path(attachment.name).suffix.lower() in {
                ".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"
            }
            text = f"{'/ocr' if is_image else '/transcribir'} {text.strip()}".rstrip()
        inbound = InboundMessage(
            id=f"telegram-{update.update_id}",
            channel="telegram",
            conversation_id=str(chat.id),
            principal_id=str(user.id),
            text=text,
            attachments=attachments,
            received_at=message.date,
            metadata={
                "telegram_update_id": update.update_id,
                "username": user.username or "",
                "audio_duration_seconds": getattr(message.voice or message.audio, "duration", None),
            },
        )
        typing_task = asyncio.create_task(self._typing_loop(chat.id))
        try:
            result = await self.application.execute_message(inbound)
        finally:
            typing_task.cancel()
            with suppress(asyncio.CancelledError):
                await typing_task
        await message.reply_text(result.text)

    async def _typing_loop(self, chat_id: int) -> None:
        """Mantiene visible el indicador de escritura durante respuestas lentas."""
        if self._telegram_application is None:
            return
        try:
            while True:
                await self._telegram_application.bot.send_chat_action(chat_id=chat_id, action="typing")
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.logger.debug("No se pudo mostrar estado de escritura en Telegram: %s", error)

    async def _download_attachment(self, message, update_id: int) -> tuple[Attachment, ...]:
        media = message.voice or message.audio or message.document
        if media is None and getattr(message, "photo", None):
            media = message.photo[-1]
        if media is None or self._telegram_application is None:
            return ()
        size = getattr(media, "file_size", None)
        if isinstance(size, int) and size > TELEGRAM_MAX_DOWNLOAD_BYTES:
            raise TelegramAttachmentError(
                "Telegram no puede descargar archivos mayores de 20 MB. "
                "Envía un audio más corto o comprimido."
            )
        try:
            file = await self._telegram_application.bot.get_file(media.file_id)
        except Exception as error:
            if "file is too big" in str(error).lower():
                raise TelegramAttachmentError(
                    "Telegram no puede descargar archivos mayores de 20 MB. "
                    "Envía un audio más corto o comprimido."
                ) from error
            raise
        raw_name = getattr(media, "file_name", None)
        if not raw_name:
            if getattr(message, "photo", None):
                raw_name = f"telegram_{update_id}.jpg"
            elif message.voice is not None:
                raw_name = f"telegram_{update_id}.oga"
            elif message.audio is not None:
                raw_name = f"telegram_{update_id}.mp3"
            else:
                raw_name = f"telegram_{update_id}"
        safe_name = SAFE_NAME.sub("_", Path(raw_name).name)[:120]
        inbox = self.application.config.data_dir / "inbox" / "telegram"
        inbox.mkdir(parents=True, exist_ok=True)
        destination = inbox / f"{update_id}_{safe_name}"
        await file.download_to_drive(custom_path=destination)
        return (
            Attachment(
                id=f"telegram-{update_id}",
                name=safe_name,
                path=str(destination),
                media_type=getattr(media, "mime_type", None),
                size_bytes=destination.stat().st_size,
            ),
        )

    async def _on_job_finished(self, record: JobRecord) -> None:
        if self._telegram_application is None or record.metadata.get("channel") != "telegram":
            return
        chat_id = record.metadata.get("conversation_id")
        if chat_id is None:
            return
        if record.status == "succeeded" and record.artifacts:
            for artifact in record.artifacts:
                artifact_path = Path(artifact)
                if artifact_path.suffix.lower() == ".m3u":
                    await self._telegram_application.bot.send_document(
                        chat_id=int(str(chat_id)),
                        document=artifact,
                        caption=(record.result or "Playlist lista")[:1024],
                    )
                elif record.metadata.get("artifact_type") in {"transcription", "ocr"}:
                    await self._telegram_application.bot.send_document(
                        chat_id=int(str(chat_id)),
                        document=artifact,
                        caption=(record.result or "Texto procesado")[:1024],
                    )
                elif record.metadata.get("artifact_type") in {"video", "playlist_video"}:
                    await self._telegram_application.bot.send_video(
                        chat_id=int(str(chat_id)),
                        video=artifact,
                        caption=(record.result or "Video listo")[:1024],
                    )
                else:
                    await self._telegram_application.bot.send_audio(
                        chat_id=int(str(chat_id)),
                        audio=artifact,
                        caption=(record.result or "Audio listo")[:1024],
                    )
        if record.status == "succeeded" and record.metadata.get("forward_to_ai") and record.result:
            ai_message = InboundMessage(
                id=f"telegram-ai-{record.id}",
                channel="telegram",
                conversation_id=str(chat_id),
                principal_id=str(record.metadata.get("principal_id", "")),
                text=f"Transcripción de audio:\n{record.result}",
                attachments=(),
                received_at=datetime.now(timezone.utc),
                metadata={"source": "audio_transcription", "job_id": record.id},
            )
            ai_result = await self.application.execute_message(ai_message)
            await self._telegram_application.bot.send_message(
                chat_id=int(str(chat_id)), text=ai_result.text[:4000]
            )
        elif record.status == "succeeded":
            await self._telegram_application.bot.send_message(
                chat_id=int(str(chat_id)),
                text=(record.result or "Trabajo completado")[:4000],
            )
        elif record.status in {"failed", "cancelled"}:
            detail = record.error or record.detail
            await self._telegram_application.bot.send_message(
                chat_id=int(str(chat_id)),
                text=f"Trabajo {record.id}: {detail}",
            )

    async def _handle_error(self, update, telegram_context) -> None:
        error = getattr(telegram_context, "error", telegram_context)
        self.logger.error(
            "Error de Telegram (update=%s): %s: %s",
            getattr(update, "update_id", None),
            type(error).__name__,
            error,
        )


def _authorization_message(status: str) -> str:
    return {
        "prompt": "Este bot es privado. Responde a este mensaje con la contraseña de activación.",
        "invalid": "Contraseña incorrecta. Inténtalo de nuevo.",
        "locked": "Demasiados intentos. Espera unos minutos antes de volver a solicitar acceso.",
        "authorized_now": "Autorización correcta. Ya puedes usar ModulAI en este chat.",
        "unavailable": "La activación no está disponible. Contacta al propietario del bot.",
    }.get(status, "No estás autorizado para usar este asistente.")


def _telegram_command_menu(definitions) -> tuple[tuple[str, str], ...]:
    """Convierte los comandos internos al formato permitido por Bot API.

    Los alias se omiten para no llenar el menú; siguen funcionando si se escriben.
    """
    return tuple(
        (definition.name, definition.description[:256])
        for definition in definitions
        if TELEGRAM_COMMAND_NAME.fullmatch(definition.name)
        and len(definition.description) >= 3
    )
