from __future__ import annotations

import inspect
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from modulai.core.messages import InboundMessage

COMMAND_NAME = re.compile(r"^[a-z0-9_][a-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    name: str
    args: tuple[str, ...]
    raw_args: str


@dataclass(frozen=True, slots=True)
class CommandContext:
    message: InboundMessage


@dataclass(frozen=True, slots=True)
class CommandResult:
    ok: bool
    text: str

    @classmethod
    def success(cls, text: str) -> CommandResult:
        return cls(ok=True, text=text)

    @classmethod
    def failure(cls, text: str) -> CommandResult:
        return cls(ok=False, text=text)


CommandHandler = Callable[[CommandContext, ParsedCommand], Awaitable[CommandResult] | CommandResult]


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    name: str
    description: str
    handler: CommandHandler
    aliases: tuple[str, ...] = ()


def parse_command(text: str | None) -> ParsedCommand | None:
    if text is None:
        return None
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None

    body = stripped[1:]
    if not body:
        return ParsedCommand(name="", args=(), raw_args="")

    name, separator, raw_args = body.partition(" ")
    name = name.lower()
    raw_args = raw_args.strip() if separator else ""
    if not raw_args:
        return ParsedCommand(name=name, args=(), raw_args="")

    lexer = shlex.shlex(raw_args, posix=False)
    lexer.whitespace_split = True
    lexer.commenters = ""
    args = tuple(_strip_matching_quotes(token) for token in lexer)
    return ParsedCommand(name=name, args=args, raw_args=raw_args)


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


class CommandRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, CommandDefinition] = {}
        self._aliases: dict[str, str] = {}

    @property
    def definitions(self) -> tuple[CommandDefinition, ...]:
        return tuple(sorted(self._definitions.values(), key=lambda item: item.name))

    def register(self, definition: CommandDefinition) -> None:
        names = (definition.name, *definition.aliases)
        for name in names:
            if not COMMAND_NAME.fullmatch(name):
                raise ValueError(f"Nombre de comando inválido: {name!r}")
            if name in self._definitions or name in self._aliases:
                raise ValueError(f"El comando o alias ya existe: {name}")

        self._definitions[definition.name] = definition
        for alias in definition.aliases:
            self._aliases[alias] = definition.name

    async def execute(self, parsed: ParsedCommand, context: CommandContext) -> CommandResult:
        canonical_name = self._aliases.get(parsed.name, parsed.name)
        definition = self._definitions.get(canonical_name)
        if definition is None:
            if parsed.name:
                return CommandResult.failure(f"Comando desconocido: /{parsed.name}")
            return CommandResult.failure("El comando está vacío.")

        try:
            value = definition.handler(context, parsed)
            if inspect.isawaitable(value):
                value = await value
            if not isinstance(value, CommandResult):
                raise TypeError("El manejador no devolvió CommandResult")
            return value
        except Exception as error:
            return CommandResult.failure(
                f"El comando /{canonical_name} falló: {type(error).__name__}: {error}"
            )

