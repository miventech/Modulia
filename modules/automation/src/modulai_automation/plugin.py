from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from modulai.core.commands import CommandContext, CommandDefinition, CommandResult, parse_command
from modulai.core.jobs import JobContext, JobOutcome
from modulai.core.messages import InboundMessage
from modulai.core.modules import ModuleContext
from modulai.infrastructure.audit import ScheduleRecord

INTERVAL = re.compile(r"^(\d+)(m|h|d)$", re.IGNORECASE)
TIME_ONLY = re.compile(r"^\d{2}:\d{2}$")


class AutomationModule:
    def __init__(self) -> None:
        self._context: ModuleContext | None = None
        self._worker: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def setup(self, context: ModuleContext) -> None:
        self._context = context
        for definition in (
            CommandDefinition("programar", "Programa un comando para una fecha, hora o repetición.", self.schedule_command),
            CommandDefinition("programaciones", "Lista tus comandos y recordatorios programados.", self.list_schedules),
            CommandDefinition("desprogramar", "Cancela una programación activa.", self.remove_schedule),
            CommandDefinition("recordar_en", "Crea un recordatorio dentro de un intervalo.", self.remind_in),
            CommandDefinition("recordar_el", "Crea un recordatorio en una fecha y hora.", self.remind_at),
        ):
            context.commands.register(definition)

    async def start(self) -> None:
        self._stopping.clear()
        self._worker = asyncio.create_task(self._run_loop(), name="modulai-automation")

    async def stop(self) -> None:
        self._stopping.set()
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    def schedule_command(self, context: CommandContext, command) -> CommandResult:
        if len(command.args) < 2:
            return CommandResult.failure(self._schedule_usage())
        when, recurrence, target_index, error = self._parse_schedule_time(command.args)
        if error:
            return CommandResult.failure(error)
        target = " ".join(command.args[target_index:]).strip()
        if not target.startswith("/"):
            return CommandResult.failure("El comando programado debe comenzar con /. " + self._schedule_usage())
        target_name = (parse_command(target).name if parse_command(target) else "")
        if target_name in {"programar", "recordar_en", "recordar_el"}:
            return CommandResult.failure("No se pueden programar comandos de automatización desde /programar.")
        record = self._add(context.message, "command", target, when, recurrence)
        return CommandResult.success(self._created_message(record))

    def remind_in(self, context: CommandContext, command) -> CommandResult:
        if len(command.args) < 2:
            return CommandResult.failure("Uso: /recordar_en <30m|2h|1d> <texto>")
        seconds, error = parse_interval(command.args[0])
        if error:
            return CommandResult.failure(error)
        text = " ".join(command.args[1:]).strip()
        if not text:
            return CommandResult.failure("Uso: /recordar_en <30m|2h|1d> <texto>")
        when = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        record = self._add(context.message, "reminder", text, when, None)
        return CommandResult.success(self._created_message(record))

    def remind_at(self, context: CommandContext, command) -> CommandResult:
        if len(command.args) < 3:
            return CommandResult.failure("Uso: /recordar_el <YYYY-MM-DD> <HH:MM> <texto>")
        when, error = self._parse_datetime(f"{command.args[0]} {command.args[1]}")
        if error:
            return CommandResult.failure(error)
        text = " ".join(command.args[2:]).strip()
        if not text:
            return CommandResult.failure("Uso: /recordar_el <YYYY-MM-DD> <HH:MM> <texto>")
        record = self._add(context.message, "reminder", text, when, None)
        return CommandResult.success(self._created_message(record))

    def list_schedules(self, context: CommandContext, command) -> CommandResult:
        del command
        assert self._context is not None
        records = self._context.store.list_schedules(context.message.principal_id)
        if not records:
            return CommandResult.success("No tienes programaciones activas.")
        lines = ["Programaciones activas:"]
        for record in records:
            kind = "Recordatorio" if record.kind == "reminder" else record.payload
            repeat = describe_recurrence(record.recurrence)
            lines.append(f"#{record.id} — {self._display_time(record.next_run_at)} — {repeat} — {kind}")
        return CommandResult.success("\n".join(lines))

    def remove_schedule(self, context: CommandContext, command) -> CommandResult:
        if len(command.args) != 1:
            return CommandResult.failure("Uso: /desprogramar <id>")
        try:
            schedule_id = int(command.args[0])
        except ValueError:
            return CommandResult.failure("El identificador debe ser un número positivo.")
        if schedule_id < 1:
            return CommandResult.failure("El identificador debe ser un número positivo.")
        assert self._context is not None
        if not self._context.store.delete_schedule(context.message.principal_id, schedule_id):
            return CommandResult.failure(f"No existe una programación activa #{schedule_id}.")
        return CommandResult.success(f"Programación #{schedule_id} cancelada.")

    async def _run_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._run_due()
            except Exception:
                if self._context is not None:
                    self._context.logger.exception("Falló la revisión de programaciones")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_seconds())
            except asyncio.TimeoutError:
                continue

    async def _run_due(self) -> None:
        if self._context is None:
            return
        records = self._context.store.claim_due_schedules(datetime.now(timezone.utc))
        for record in records:
            self._context.jobs.submit(
                label=f"Programación #{record.id}",
                runner=lambda job, item=record: self._execute_schedule(job, item),
                metadata={
                    "channel": record.channel,
                    "conversation_id": record.conversation_id,
                    "principal_id": record.principal_id,
                    "artifact_type": "notification",
                },
            )

    async def _execute_schedule(self, job: JobContext, record: ScheduleRecord) -> JobOutcome:
        assert self._context is not None
        job.report_progress(5, "Ejecutando programación")
        if record.kind == "reminder":
            return JobOutcome(f"Recordatorio: {record.payload}")
        message = InboundMessage(
            id=f"schedule-{record.id}-{int(datetime.now(timezone.utc).timestamp())}",
            channel=record.channel,
            conversation_id=record.conversation_id,
            principal_id=record.principal_id,
            text=record.payload,
            attachments=(),
            received_at=datetime.now(timezone.utc),
            metadata={"schedule_id": record.id},
        )
        parsed = parse_command(record.payload)
        if parsed is None:
            return JobOutcome("La programación contiene un comando inválido.")
        result = await self._context.commands.execute(parsed, CommandContext(message=message))
        self._context.store.record_command(message, parsed, result)
        job.report_progress(95, "Resultado listo")
        return JobOutcome(result.text)

    def _parse_schedule_time(self, args: tuple[str, ...]) -> tuple[datetime | None, str | None, int, str | None]:
        first = args[0].lower()
        if first == "cada":
            if len(args) < 3:
                return None, None, 0, self._schedule_usage()
            seconds, error = parse_interval(args[1])
            if error:
                return None, None, 0, error
            return datetime.now(timezone.utc) + timedelta(seconds=seconds), f"interval:{seconds}", 2, None
        if TIME_ONLY.fullmatch(args[0]):
            try:
                local_now = datetime.now(self._timezone())
                requested = datetime.strptime(args[0], "%H:%M").time()
                local_when = local_now.replace(hour=requested.hour, minute=requested.minute, second=0, microsecond=0)
                if local_when <= local_now:
                    local_when += timedelta(days=1)
                return local_when.astimezone(timezone.utc), "daily", 1, None
            except ValueError:
                return None, None, 0, "La hora debe tener el formato HH:MM."
        when, error = self._parse_datetime(args[0])
        return when, None, 1, error

    def _parse_datetime(self, value: str) -> tuple[datetime | None, str | None]:
        try:
            local_value = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=self._timezone())
        except ValueError:
            return None, "La fecha debe tener el formato YYYY-MM-DD HH:MM."
        if local_value <= datetime.now(self._timezone()):
            return None, "La fecha programada debe estar en el futuro."
        return local_value.astimezone(timezone.utc), None

    def _add(self, message: InboundMessage, kind: str, payload: str, when: datetime, recurrence: str | None) -> ScheduleRecord:
        assert self._context is not None
        return self._context.store.add_schedule(message, kind, payload, when.astimezone(timezone.utc).isoformat(), recurrence)

    def _timezone(self) -> ZoneInfo:
        config = self._context.config if self._context else {}
        try:
            return ZoneInfo(str(config.get("time_zone", "America/Lima")))
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    def _poll_seconds(self) -> int:
        config = self._context.config if self._context else {}
        return max(1, min(int(config.get("poll_seconds", 2)), 60))

    def _display_time(self, value: str) -> str:
        return datetime.fromisoformat(value).astimezone(self._timezone()).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _schedule_usage() -> str:
        return "Uso: /programar <HH:MM|YYYY-MM-DD HH:MM|cada 30m> /comando"

    def _created_message(self, record: ScheduleRecord) -> str:
        return f"Programación #{record.id} creada para {self._display_time(record.next_run_at)} ({describe_recurrence(record.recurrence)})."


def parse_interval(value: str) -> tuple[int, str | None]:
    match = INTERVAL.fullmatch(value.strip())
    if not match:
        return 0, "El intervalo debe usar minutos, horas o días: 30m, 2h o 1d."
    amount = int(match.group(1))
    factor = {"m": 60, "h": 3600, "d": 86400}[match.group(2).lower()]
    seconds = amount * factor
    if amount < 1 or seconds > 365 * 86400:
        return 0, "El intervalo debe estar entre 1 minuto y 365 días."
    return seconds, None


def describe_recurrence(value: str | None) -> str:
    if value is None:
        return "una vez"
    if value == "daily":
        return "cada día"
    if value.startswith("interval:"):
        seconds = int(value.partition(":")[2])
        if seconds % 86400 == 0:
            return f"cada {seconds // 86400} día(s)"
        if seconds % 3600 == 0:
            return f"cada {seconds // 3600} hora(s)"
        return f"cada {seconds // 60} minuto(s)"
    return value


def create_module() -> AutomationModule:
    return AutomationModule()
