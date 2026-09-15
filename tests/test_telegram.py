from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from modulai.adapters.telegram import TelegramAuthorization, _telegram_command_menu
from modulai.bootstrap import Application
from modulai.core.commands import CommandDefinition
from modulai.infrastructure.config import TelegramConfig
from tests.test_application import make_config


class TelegramConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_enabled_without_token_is_reported_without_crashing_core(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        old_token = os.environ.pop("MODULAI_TELEGRAM_TOKEN", None)
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                config_path = make_config(project_root, temporary_directory)
                with config_path.open("a", encoding="utf-8") as stream:
                    stream.write(
                        "\n[telegram]\n"
                        "enabled = true\n"
                        'token_env = "MODULAI_TELEGRAM_TOKEN_TEST"\n'
                        "allowed_user_ids = [123456789]\n"
                    )
                application = Application.create(project_root, config_path)

                await application.start()
                result = await application.execute_text("/hola")
                telegram_error = application.telegram_error
                await application.stop()

                self.assertTrue(result.ok)
                self.assertIsNotNone(telegram_error)
                self.assertIn("MODULAI_TELEGRAM_TOKEN_TEST", telegram_error or "")
        finally:
            if old_token is not None:
                os.environ["MODULAI_TELEGRAM_TOKEN"] = old_token

    def test_password_activation_persists_user_without_persisting_password(self) -> None:
        old_password = os.environ.get("MODULAI_TEST_ACTIVATION")
        os.environ["MODULAI_TEST_ACTIVATION"] = "secreto-local"
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                config = TelegramConfig(
                    enabled=True,
                    token_env="TOKEN",
                    activation_password_env="MODULAI_TEST_ACTIVATION",
                    activation_timeout_seconds=300,
                    max_activation_attempts=3,
                    network_timeout_seconds=30,
                    allowed_user_ids=(),
                    drop_pending_updates=False,
                )
                authorization = TelegramAuthorization(config, Path(temporary_directory))

                self.assertEqual(authorization.check(42, "hola"), "prompt")
                self.assertEqual(authorization.check(42, "incorrecta"), "invalid")
                self.assertEqual(authorization.check(42, "secreto-local"), "authorized_now")
                self.assertEqual(authorization.check(42, "cualquier mensaje"), "authorized")
                persisted = Path(temporary_directory) / "telegram" / "authorized_users.json"
                self.assertEqual(persisted.read_text(encoding="utf-8").strip(), "[\n  42\n]")
                self.assertNotIn("secreto-local", persisted.read_text(encoding="utf-8"))
        finally:
            if old_password is None:
                os.environ.pop("MODULAI_TEST_ACTIVATION", None)
            else:
                os.environ["MODULAI_TEST_ACTIVATION"] = old_password

    def test_command_menu_uses_canonical_telegram_compatible_commands(self) -> None:
        def handler(context, command):
            del context, command
            raise AssertionError("No se debe ejecutar")

        definitions = (
            CommandDefinition("recuerdos", "Lista las notas persistentes.", handler),
            CommandDefinition("alias-invalido", "No aparece en Telegram.", handler),
            CommandDefinition("corto", "ok", handler),
            CommandDefinition("descripcion", "x" * 300, handler),
        )

        self.assertEqual(
            _telegram_command_menu(definitions),
            (
                ("recuerdos", "Lista las notas persistentes."),
                ("descripcion", "x" * 256),
            ),
        )


if __name__ == "__main__":
    unittest.main()
