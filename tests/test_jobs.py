from __future__ import annotations

import asyncio
import unittest

from modulai.core.jobs import JobManager, JobOutcome


class JobManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_completes_runner_and_keeps_artifact(self) -> None:
        manager = JobManager()

        async def runner(context):
            context.report_progress(45, "Procesando")
            await asyncio.sleep(0)
            return JobOutcome("Terminado", ("data/result.mp3",))

        record = manager.submit("Prueba", runner)
        await asyncio.sleep(0.02)

        self.assertEqual(record.status, "succeeded")
        self.assertEqual(record.result, "Terminado")
        self.assertEqual(record.artifacts, ("data/result.mp3",))
        await manager.shutdown()

    async def test_cancel_is_cooperative(self) -> None:
        manager = JobManager()
        started = asyncio.Event()

        async def runner(context):
            started.set()
            while True:
                context.check_cancelled()
                await asyncio.sleep(0.001)

        record = manager.submit("Cancelación", runner)
        await started.wait()
        self.assertTrue(manager.cancel(record.id))
        await asyncio.sleep(0.02)

        self.assertEqual(record.status, "cancelled")
        await manager.shutdown()


if __name__ == "__main__":
    unittest.main()
