from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from modulai.bootstrap import Application
from modulai.core.messages import InboundMessage
from tests.test_application import make_config


class TasksIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_lists_completes_and_deletes_tasks(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            application = Application.create(project_root, make_config(project_root, directory))
            await application.start()
            created = await application.execute_text(
                "/pendiente Comprar pan --categoria Compras --color pink --importancia alta"
            )
            agenda = await application.execute_text("/agenda")
            completed = await application.execute_text("/hecho 1")
            empty_agenda = await application.execute_text("/agenda")
            deleted = await application.execute_text("/quitar_pendiente 1 confirmar")
            await application.stop()

        self.assertTrue(created.ok)
        self.assertIn("#1", agenda.text)
        self.assertIn("Compras:", agenda.text)
        self.assertIn("Comprar pan", agenda.text)
        self.assertTrue(completed.ok)
        self.assertIn("No tienes tareas", empty_agenda.text)
        self.assertTrue(deleted.ok)

    async def test_tasks_are_private_to_each_principal(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            application = Application.create(project_root, make_config(project_root, directory))
            await application.start()
            first = InboundMessage.local("/pendiente Nota privada")
            second = InboundMessage(
                id="other-user",
                channel="telegram",
                conversation_id="chat-2",
                principal_id="other-user",
                text="/agenda",
                attachments=(),
                received_at=first.received_at,
            )
            await application.execute_message(first)
            agenda = await application.execute_message(second)
            await application.stop()

        self.assertTrue(agenda.ok)
        self.assertIn("No tienes tareas", agenda.text)


if __name__ == "__main__":
    unittest.main()
