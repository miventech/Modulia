from __future__ import annotations

from modulai.core.commands import CommandContext, CommandDefinition, CommandResult, ParsedCommand
from modulai.core.modules import ModuleContext


class HelloModule:
    async def setup(self, context: ModuleContext) -> None:
        context.commands.register(
            CommandDefinition(
                name="hola",
                description="Comprueba que el sistema de módulos funciona.",
                handler=self.hello,
            )
        )

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def hello(
        self,
        context: CommandContext,
        command: ParsedCommand,
    ) -> CommandResult:
        del context, command
        return CommandResult.success(
            "¡Hola! ModulAI y su sistema de módulos funcionan correctamente."
        )


def create_module() -> HelloModule:
    return HelloModule()
