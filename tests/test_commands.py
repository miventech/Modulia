from __future__ import annotations

import unittest

from modulai.core.commands import (
    CommandContext,
    CommandDefinition,
    CommandRegistry,
    CommandResult,
    parse_command,
)
from modulai.core.messages import InboundMessage


class ParseCommandTests(unittest.TestCase):
    def test_returns_none_for_plain_text(self) -> None:
        self.assertIsNone(parse_command("hola"))

    def test_normalizes_name_and_preserves_windows_path(self) -> None:
        parsed = parse_command('/TRANSCRIBIR "C:\\Mis audios\\voz.wav"')

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.name, "transcribir")
        self.assertEqual(parsed.args, ("C:\\Mis audios\\voz.wav",))
        self.assertEqual(parsed.raw_args, '"C:\\Mis audios\\voz.wav"')

    def test_represents_empty_command(self) -> None:
        parsed = parse_command("/")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.name, "")
        self.assertEqual(parsed.args, ())


class CommandRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_executes_registered_async_handler(self) -> None:
        registry = CommandRegistry()

        async def handler(context: CommandContext, command: object) -> CommandResult:
            del context, command
            return CommandResult.success("ok")

        registry.register(CommandDefinition("hola", "Saluda", handler))
        parsed = parse_command("/hola")
        assert parsed is not None

        result = await registry.execute(parsed, CommandContext(InboundMessage.local("/hola")))

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "ok")

    async def test_rejects_duplicate_alias(self) -> None:
        registry = CommandRegistry()

        def handler(context: CommandContext, command: object) -> CommandResult:
            del context, command
            return CommandResult.success("ok")

        registry.register(CommandDefinition("primero", "Uno", handler, aliases=("p",)))

        with self.assertRaisesRegex(ValueError, "ya existe"):
            registry.register(CommandDefinition("p", "Duplicado", handler))

    async def test_contains_handler_errors(self) -> None:
        registry = CommandRegistry()

        def handler(context: CommandContext, command: object) -> CommandResult:
            del context, command
            raise RuntimeError("problema controlado")

        registry.register(CommandDefinition("fallar", "Falla", handler))
        parsed = parse_command("/fallar")
        assert parsed is not None

        result = await registry.execute(parsed, CommandContext(InboundMessage.local("/fallar")))

        self.assertFalse(result.ok)
        self.assertIn("problema controlado", result.text)


if __name__ == "__main__":
    unittest.main()
