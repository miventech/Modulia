from __future__ import annotations

from modulai.core.commands import CommandContext, CommandDefinition, CommandResult, ParsedCommand
from modulai.core.modules import ModuleContext

COLORS = {"yellow", "pink", "purple", "blue", "green", "orange"}
IMPORTANCE = {"baja", "media", "alta"}


class TasksModule:
    def __init__(self) -> None:
        self._context: ModuleContext | None = None

    async def setup(self, context: ModuleContext) -> None:
        self._context = context
        definitions = (
            CommandDefinition("pendiente", "Crea una tarea o nota personal.", self.add_task),
            CommandDefinition("agenda", "Lista tus tareas pendientes.", self.list_tasks, aliases=("tareas_personales",)),
            CommandDefinition("hecho", "Marca una tarea como completada.", self.complete_task),
            CommandDefinition("quitar_pendiente", "Elimina una tarea con confirmación.", self.delete_task),
        )
        for definition in definitions:
            context.commands.register(definition)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def add_task(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        text, color, importance, category, error = parse_new_task(command.args)
        if error:
            return CommandResult.failure(error)
        assert self._context is not None and text is not None and color is not None and importance is not None and category is not None
        task = self._context.store.add_task(context.message.principal_id, text, color, importance, category)
        return CommandResult.success(f"Pendiente #{task.id} creado en {category} ({importance}, {color}).")

    def list_tasks(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        del command
        assert self._context is not None
        tasks = self._context.store.list_tasks(context.message.principal_id)
        if not tasks:
            return CommandResult.success("No tienes tareas pendientes.")
        lines = ["Tus pendientes:"]
        current_category = ""
        for task in tasks:
            if task.category != current_category:
                current_category = task.category
                lines.append(f"\n{current_category}:")
            lines.append(f"#{task.id} [{task.importance}] {task.text}")
        return CommandResult.success("\n".join(lines))

    def complete_task(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        task_id, error = parse_task_id(command.args, "Uso: /hecho <id>")
        if error:
            return CommandResult.failure(error)
        assert self._context is not None and task_id is not None
        task = self._context.store.update_task(context.message.principal_id, task_id, completed=True)
        if task is None:
            return CommandResult.failure(f"No existe el pendiente #{task_id}.")
        return CommandResult.success(f"Pendiente #{task_id} marcado como hecho.")

    def delete_task(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        if len(command.args) != 2 or command.args[1].lower() != "confirmar":
            return CommandResult.failure("Uso: /quitar_pendiente <id> confirmar")
        task_id, error = parse_task_id(command.args[:1], "Uso: /quitar_pendiente <id> confirmar")
        if error:
            return CommandResult.failure(error)
        assert self._context is not None and task_id is not None
        if not self._context.store.delete_task(context.message.principal_id, task_id):
            return CommandResult.failure(f"No existe el pendiente #{task_id}.")
        return CommandResult.success(f"Pendiente #{task_id} eliminado.")


def parse_new_task(
    args: tuple[str, ...],
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    usage = "Uso: /pendiente <texto> [--categoria Nombre] [--color yellow] [--importancia baja|media|alta]"
    words: list[str] = []
    values = {"color": "yellow", "importance": "media", "category": "General"}
    index = 0
    flags = {"--color": "color", "--importancia": "importance", "--categoria": "category"}
    while index < len(args):
        field = flags.get(args[index].lower())
        if field is None:
            words.append(args[index])
            index += 1
            continue
        if index + 1 >= len(args):
            return None, None, None, None, usage
        values[field] = args[index + 1] if field == "category" else args[index + 1].lower()
        index += 2
    text = " ".join(words).strip()
    if not text:
        return None, None, None, None, usage
    if len(text) > 500:
        return None, None, None, None, "El texto del pendiente no puede superar 500 caracteres."
    if values["color"] not in COLORS:
        return None, None, None, None, "Color inválido. Usa: " + ", ".join(sorted(COLORS)) + "."
    if values["importance"] not in IMPORTANCE:
        return None, None, None, None, "Importancia inválida. Usa: baja, media o alta."
    category = " ".join(values["category"].split())
    if not category or len(category) > 60:
        return None, None, None, None, "La categoría debe tener entre 1 y 60 caracteres."
    return text, values["color"], values["importance"], category, None


def parse_task_id(args: tuple[str, ...], usage: str) -> tuple[int | None, str | None]:
    if len(args) != 1:
        return None, usage
    try:
        task_id = int(args[0])
    except ValueError:
        return None, "El identificador debe ser un número positivo."
    if task_id < 1:
        return None, "El identificador debe ser un número positivo."
    return task_id, None


def create_module() -> TasksModule:
    return TasksModule()
