from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modulai.bootstrap import Application
from modulai.core.messages import InboundMessage
from tests.test_application import make_config


class AutomationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_lists_and_cancels_a_scheduled_command(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            application = Application.create(project_root, make_config(project_root, directory))
            await application.start()
            created = await application.execute_text('/programar "23:59" /hola')
            listed = await application.execute_text('/programaciones')
            removed = await application.execute_text('/desprogramar 1')
            await application.stop()

        self.assertTrue(created.ok)
        self.assertIn("#1", listed.text)
        self.assertTrue(removed.ok)

    async def test_due_reminder_runs_as_a_job_and_completes_schedule(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            application = Application.create(project_root, make_config(project_root, directory))
            await application.start()
            message = InboundMessage.local("/recordar_en 1m llamar a Juan")
            record = application.audit.add_schedule(
                message,
                "reminder",
                "llamar a Juan",
                (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            )
            automation = next(
                item.instance
                for item in application.modules.records
                if item.manifest.id == "local.automation"
            )
            assert automation is not None
            await automation._run_due()
            await asyncio.sleep(0.03)
            completed = application.audit.get_schedule(record.id)
            jobs = application.jobs.records
            await application.stop()

        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertEqual(completed.status, "completed")
        self.assertTrue(any(job.result == "Recordatorio: llamar a Juan" for job in jobs))


if __name__ == "__main__":
    unittest.main()
