from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from modulai.bootstrap import Application


def make_config(project_root: Path, temporary_directory: str) -> Path:
    temporary_path = Path(temporary_directory)
    config_path = temporary_path / "test.toml"
    config_path.write_text(
        "\n".join(
            (
                "[app]",
                'name = "ModulAI Test"',
                f'data_dir = "{(temporary_path / "data").as_posix()}"',
                "[modules]",
                f'paths = ["{(project_root / "modules").as_posix()}"]',
                "[logging]",
                'level = "ERROR"',
                "file_enabled = false",
                'file_name = "test.log"',
            )
        ),
        encoding="utf-8",
    )
    return config_path


class ApplicationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_loads_module_executes_command_and_audits_result(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = make_config(project_root, temporary_directory)
            application = Application.create(project_root, config_path)

            await application.start()
            result = await application.execute_text("/hola")
            await application.stop()

            self.assertTrue(result.ok)
            self.assertIn("funcionan correctamente", result.text)
            hello = next(
                record
                for record in application.modules.records
                if record.manifest.id == "local.hello"
            )
            self.assertEqual(hello.status, "stopped")
            database = Path(temporary_directory) / "data" / "modulai.db"
            connection = sqlite3.connect(database)
            try:
                row = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()
            finally:
                connection.close()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row[0], 1)

    async def test_plain_text_is_rejected_without_audit_event(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = make_config(project_root, temporary_directory)
            application = Application.create(project_root, config_path)

            await application.start()
            result = await application.execute_text("hola sin barra")
            count = application.audit.count_events()
            await application.stop()

            self.assertFalse(result.ok)
            self.assertEqual(count, 0)

    async def test_conversation_and_memory_commands_persist_state(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = make_config(project_root, temporary_directory)
            application = Application.create(project_root, config_path)

            await application.start()
            started = await application.execute_text("/conversacion")
            plain = await application.execute_text("este mensaje forma parte del historial")
            remembered = await application.execute_text("/no_olvidar respuesta breve")
            memories = await application.execute_text("/recuerdos")
            ended = await application.execute_text("/fin_conversacion")
            rejected = await application.execute_text("ya no hay conversación")
            await application.stop()

            self.assertTrue(started.ok)
            self.assertTrue(plain.ok)
            self.assertTrue(remembered.ok)
            self.assertIn("respuesta breve", memories.text)
            self.assertTrue(ended.ok)
            self.assertFalse(rejected.ok)

    async def test_module_configuration_is_validated_and_applies_after_restart(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = make_config(project_root, temporary_directory)
            application = Application.create(project_root, config_path)
            await application.start()

            with self.assertRaisesRegex(ValueError, "enabled debe ser"):
                application.modules.update_config("local.hello", {"enabled": "sí"})
            application.modules.update_config("local.hello", {"enabled": False})
            await application.stop()

            restarted = Application.create(project_root, config_path)
            await restarted.start()
            result = await restarted.execute_text("/hola")
            module_status = next(
                record.status
                for record in restarted.modules.records
                if record.manifest.id == "local.hello"
            )
            await restarted.stop()

            self.assertFalse(result.ok)
            self.assertEqual(module_status, "disabled")


if __name__ == "__main__":
    unittest.main()
