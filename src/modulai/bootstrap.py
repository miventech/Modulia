from __future__ import annotations

import inspect
import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

from modulai.application.builtin_commands import CoreCommandService
from modulai.core.commands import CommandContext, CommandRegistry, CommandResult, parse_command
from modulai.core.jobs import JobManager
from modulai.core.messages import InboundMessage
from modulai.core.modules import ModuleContext
from modulai.infrastructure.audit import AuditStore
from modulai.infrastructure.certificates import configure_system_certificates
from modulai.infrastructure.config import AppConfig, load_config
from modulai.infrastructure.logging import configure_logging
from modulai.infrastructure.module_loader import ModuleManager


@dataclass(slots=True)
class Application:
    root: Path
    config: AppConfig
    commands: CommandRegistry
    modules: ModuleManager
    audit: AuditStore
    jobs: JobManager
    logger: logging.Logger
    _started: bool = False
    _telegram_adapter: object | None = None
    telegram_error: str | None = None

    @classmethod
    def create(cls, root: Path, config_path: Path | None = None) -> Application:
        root = root.resolve()
        configure_system_certificates()
        config = load_config(root, config_path)
        configure_logging(config.logging, config.data_dir)
        logger = logging.getLogger("modulai")
        commands = CommandRegistry()
        audit = AuditStore(config.data_dir / "modulai.db")
        jobs = JobManager()
        context = ModuleContext(
            commands=commands,
            root=root,
            data_dir=config.data_dir,
            logger=logger,
            jobs=jobs,
            store=audit,
        )
        modules = ModuleManager(
            config.module_paths,
            config.data_dir / "config" / "modules",
            context,
            logger,
        )
        core_commands = CoreCommandService(commands, audit, lambda: modules.records, jobs)
        core_commands.register()
        return cls(root, config, commands, modules, audit, jobs, logger)

    async def start(self) -> None:
        if self._started:
            return
        self.audit.open()
        self.jobs.add_listener(self._forward_short_media_to_ai)
        await self.modules.load_and_start()
        if self.config.telegram.enabled:
            from modulai.adapters.telegram import TelegramAdapter

            adapter = TelegramAdapter(self, self.config.telegram, self.logger)
            try:
                await adapter.start()
                self._telegram_adapter = adapter
            except Exception as error:
                self.telegram_error = f"{type(error).__name__}: {error}"
                self.logger.error("Telegram no se pudo iniciar: %s", self.telegram_error)
        self._started = True
        self.logger.info(
            "ModulAI iniciado: %s módulo(s) activo(s), %s error(es)",
            self.modules.active_count,
            self.modules.error_count,
        )

    async def _forward_short_media_to_ai(self, record) -> None:
        """Reenvía OCR y transcripciones cortas a la IA para la UI local."""
        if record.status != "succeeded" or not record.metadata.get("forward_to_ai"):
            return
        if record.metadata.get("channel") != "local-ui" or not record.result:
            return
        message = InboundMessage(
            id=f"ai-job-{record.id}",
            channel="local-ui",
            conversation_id=str(record.metadata.get("conversation_id", "local-ui")),
            principal_id=str(record.metadata.get("principal_id", "local-user")),
            text=record.result,
            attachments=(),
            received_at=datetime.now(timezone.utc),
            metadata={"source": "media_processing", "job_id": record.id},
        )
        result = await self.execute_message(message)
        record.result = f"{record.result}\n\nRespuesta de la IA:\n{result.text}"

    async def stop(self) -> None:
        if not self._started:
            return
        await self.jobs.shutdown()
        await self.modules.stop()
        if self._telegram_adapter is not None:
            await self._telegram_adapter.stop()
            self._telegram_adapter = None
        self.audit.close()
        self._started = False
        self.logger.info("ModulAI detenido")

    async def execute_text(self, text: str) -> CommandResult:
        return await self.execute_message(InboundMessage.local(text))

    async def execute_message(self, message: InboundMessage) -> CommandResult:
        if not self._started:
            raise RuntimeError("La aplicación debe iniciarse antes de ejecutar comandos")

        parsed = parse_command(message.text)
        if parsed is None:
            direct_result = await self._dispatch_message_to_modules(message)
            if direct_result is not None:
                self.audit.record_message(message, "inbound", message.text or "")
                self.audit.record_message(message, "outbound", direct_result.text)
                return direct_result
            if not self.audit.is_conversation_active(message):
                result = CommandResult.failure("Se esperaba un comando que comience con '/'.")
                self.audit.record_message(message, "inbound", message.text or "")
                self.audit.record_message(message, "outbound", result.text)
                return result
            inbound_text = message.text or ""
            result = CommandResult.success(
                "Mensaje guardado. No hay ningún módulo conversacional activo todavía."
            )
            self.audit.record_message(message, "inbound", inbound_text)
            self.audit.record_message(message, "outbound", result.text)
            return result

        self.audit.record_message(message, "inbound", message.text or "")
        context = CommandContext(message=message)
        result = await self.commands.execute(parsed, context)
        self.audit.record_command(message, parsed, result)
        self.audit.record_message(message, "outbound", result.text)
        return result

    async def _dispatch_message_to_modules(self, message: InboundMessage) -> CommandResult | None:
        """Da a los módulos activos la oportunidad de responder texto sin comando."""
        context = CommandContext(message=message)
        for record in self.modules.records:
            if record.status != "active" or record.instance is None:
                continue
            handler = getattr(record.instance, "handle_message", None)
            if not callable(handler):
                continue
            try:
                result = handler(context)
                if inspect.isawaitable(result):
                    result = await result
                if result is not None and not isinstance(result, CommandResult):
                    raise TypeError("handle_message() debe devolver CommandResult o None")
                if result is not None:
                    return result
            except Exception as error:
                self.logger.exception("Falló el manejo de texto en el módulo %s", record.manifest.id)
                return CommandResult.failure(
                    f"El módulo {record.manifest.id} no pudo responder: {type(error).__name__}: {error}"
                )
        return None
