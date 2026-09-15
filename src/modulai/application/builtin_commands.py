from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from modulai.core.commands import (
    CommandContext,
    CommandDefinition,
    CommandRegistry,
    CommandResult,
    ParsedCommand,
)
from modulai.core.jobs import JobManager
from modulai.infrastructure.audit import AuditStore


class ModuleStatus(Protocol):
    manifest: object
    status: str
    error: str | None


class CoreCommandService:
    def __init__(
        self,
        commands: CommandRegistry,
        store: AuditStore,
        module_provider: Callable[[], Iterable[ModuleStatus]],
        jobs: JobManager,
    ) -> None:
        self.commands = commands
        self.store = store
        self.module_provider = module_provider
        self.jobs = jobs

    def register(self) -> None:
        definitions = (
            CommandDefinition("ayuda", "Lista los comandos disponibles.", self.help),
            CommandDefinition("modulos", "Muestra los módulos detectados.", self.modules),
            CommandDefinition(
                "conversacion",
                "Abre una conversación en el canal actual.",
                self.start_conversation,
            ),
            CommandDefinition(
                "fin_conversacion",
                "Cierra la conversación actual.",
                self.end_conversation,
                aliases=("finconversacion",),
            ),
            CommandDefinition(
                "no_olvidar",
                "Guarda una nota persistente.",
                self.remember,
            ),
            CommandDefinition("recuerdos", "Lista las notas persistentes.", self.memories),
            CommandDefinition("tareas", "Muestra los trabajos en segundo plano.", self.tasks),
            CommandDefinition("cancelar", "Solicita cancelar un trabajo.", self.cancel),
            CommandDefinition(
                "olvidar",
                "Elimina una nota mediante /olvidar <id> confirmar.",
                self.forget,
            ),
        )
        for definition in definitions:
            self.commands.register(definition)

    def help(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        del context, command
        lines = ["Comandos disponibles:"]
        lines.extend(
            f"/{definition.name} — {definition.description}"
            for definition in self.commands.definitions
        )
        return CommandResult.success("\n".join(lines))

    def modules(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        del context, command
        modules = tuple(self.module_provider())
        if not modules:
            return CommandResult.success("No se detectaron módulos externos.")
        lines = ["Módulos detectados:"]
        for module in modules:
            manifest = module.manifest
            module_id = getattr(manifest, "id", "desconocido")
            detail = f" — {module.error}" if module.error else ""
            lines.append(f"{module_id}: {module.status}{detail}")
        return CommandResult.success("\n".join(lines))

    def start_conversation(
        self,
        context: CommandContext,
        command: ParsedCommand,
    ) -> CommandResult:
        del command
        if not self.store.start_conversation(context.message):
            return CommandResult.failure("Ya existe una conversación activa en este canal.")
        return CommandResult.success("Conversación iniciada.")

    def end_conversation(
        self,
        context: CommandContext,
        command: ParsedCommand,
    ) -> CommandResult:
        del command
        if not self.store.end_conversation(context.message):
            return CommandResult.failure("No hay una conversación activa en este canal.")
        return CommandResult.success("Conversación finalizada.")

    def remember(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        text = command.raw_args.strip()
        if not text:
            return CommandResult.failure("Uso: /no_olvidar <texto>")
        memory = self.store.add_memory(context.message.principal_id, text)
        return CommandResult.success(f"Recuerdo #{memory.id} guardado.")

    def memories(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        del command
        memories = self.store.list_memories(context.message.principal_id)
        if not memories:
            return CommandResult.success("No hay recuerdos guardados.")
        lines = ["Recuerdos:"]
        lines.extend(f"#{memory.id}: {memory.text}" for memory in memories)
        return CommandResult.success("\n".join(lines))

    def forget(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        if len(command.args) != 2 or command.args[1].lower() != "confirmar":
            return CommandResult.failure("Uso: /olvidar <id> confirmar")
        try:
            memory_id = int(command.args[0])
        except ValueError:
            return CommandResult.failure("El identificador del recuerdo debe ser un número.")
        if memory_id <= 0:
            return CommandResult.failure("El identificador del recuerdo debe ser positivo.")
        if not self.store.delete_memory(context.message.principal_id, memory_id):
            return CommandResult.failure(f"No existe el recuerdo #{memory_id}.")
        return CommandResult.success(f"Recuerdo #{memory_id} eliminado.")

    def tasks(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        del context, command
        if not self.jobs.records:
            return CommandResult.success("No hay trabajos registrados.")
        lines = ["Trabajos:"]
        for job in self.jobs.records:
            progress = f"{job.progress:.0f}%" if job.status in {"queued", "running"} else job.status
            lines.append(f"{job.id} — {job.label} — {progress} — {job.detail}")
        return CommandResult.success("\n".join(lines))

    def cancel(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        del context
        if len(command.args) != 1:
            return CommandResult.failure("Uso: /cancelar <id>")
        if not self.jobs.cancel(command.args[0]):
            return CommandResult.failure(f"No se puede cancelar el trabajo {command.args[0]}.")
        return CommandResult.success(f"Cancelación solicitada para {command.args[0]}.")
