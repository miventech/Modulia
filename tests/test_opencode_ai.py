from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules" / "opencode_ai" / "src"))

from modulai.core.commands import CommandContext, CommandDefinition, CommandRegistry  # noqa: E402
from modulai.core.jobs import JobManager  # noqa: E402
from modulai.core.messages import InboundMessage  # noqa: E402
from modulai.core.modules import ModuleContext  # noqa: E402
from modulai.infrastructure.audit import AuditStore  # noqa: E402
from modulai_opencode_ai.plugin import create_module  # noqa: E402


class OpenCodeModuleTests(unittest.IsolatedAsyncioTestCase):
    async def test_ai_can_execute_a_modulai_command_as_a_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AuditStore(Path(directory) / "modulai.db")
            store.open()
            registry = CommandRegistry()

            def greet(context, command):
                del context, command
                from modulai.core.commands import CommandResult

                return CommandResult.success("Hola desde el módulo.")

            registry.register(CommandDefinition("saludar", "Saluda al usuario.", greet))
            module = create_module()
            await module.setup(
                ModuleContext(
                    registry,
                    Path(directory),
                    Path(directory),
                    logging.getLogger("test.opencode"),
                    JobManager(),
                    store,
                    {
                        "base_url": "http://127.0.0.1:1234/v1/chat/completions",
                        "model": "test-model",
                    },
                )
            )
            responses = iter(
                (
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "tool_calls": [
                                        {
                                            "id": "call-1",
                                            "function": {
                                                "name": "ejecutar_comando",
                                                "arguments": '{"command":"/saludar"}',
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                    {"choices": [{"message": {"role": "assistant", "content": "Acción realizada."}}]},
                )
            )
            message = InboundMessage.local("saluda por mí")
            with patch.object(module, "_request", side_effect=lambda *args: next(responses)):
                result = await module.handle_message(CommandContext(message))
            store.close()

            self.assertTrue(result.ok)
            self.assertEqual(result.text, "Acción realizada.")


if __name__ == "__main__":
    unittest.main()
