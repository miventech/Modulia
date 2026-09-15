from __future__ import annotations

import logging
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules" / "ocr" / "src"))

from modulai.core.commands import CommandContext, ParsedCommand  # noqa: E402
from modulai.core.jobs import JobManager  # noqa: E402
from modulai.core.messages import Attachment, InboundMessage  # noqa: E402
from modulai.core.modules import ModuleContext  # noqa: E402
from modulai.infrastructure.audit import AuditStore  # noqa: E402
from modulai_ocr.plugin import create_module  # noqa: E402


class OcrModuleTests(unittest.IsolatedAsyncioTestCase):
    async def test_extracts_attached_image_and_saves_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "recibo.png"
            image.write_bytes(b"fake image")
            store = AuditStore(root / "modulai.db")
            store.open()
            jobs = JobManager()
            module = create_module()
            await module.setup(ModuleContext(SimpleNamespace(register=lambda *_: None), root, root, logging.getLogger(), jobs, store, {"tesseract_command": "tesseract"}))
            message = InboundMessage(
                id="1", channel="local", conversation_id="local", principal_id="local-user", text="/ocr",
                attachments=(Attachment("a", "recibo.png", str(image), "image/png", image.stat().st_size),),
                received_at=InboundMessage.local("x").received_at,
            )
            with patch("modulai_ocr.plugin.shutil.which", return_value="tesseract"), patch(
                "modulai_ocr.plugin.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout="TOTAL 10.00", stderr=""),
            ):
                result = await module.extract_text(CommandContext(message), ParsedCommand("ocr", (), ""))
                await asyncio.sleep(0.1)
            self.assertTrue(result.ok)
            self.assertIn("Trabajo", result.text)
            self.assertEqual(jobs.records[0].status, "succeeded", jobs.records[0].error)
            self.assertTrue(list((root / "ocr").glob("*.txt")))
            store.close()


if __name__ == "__main__":
    unittest.main()
