from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from modulai.infrastructure.config import (
    ensure_config_file,
    load_config,
    read_config_values,
    write_config_values,
)


EXAMPLE = """[app]
name = "ModulAI"
data_dir = "data"

[modules]
paths = ["modules"]

[logging]
level = "INFO"
file_enabled = true
file_name = "modulai.log"

[web]
host = "127.0.0.1"
port = 8765
open_browser = false

[telegram]
enabled = false
token_env = "MODULAI_TELEGRAM_TOKEN"
activation_password_env = "MODULAI_TELEGRAM_ACTIVATION_PASSWORD"
activation_timeout_seconds = 300
max_activation_attempts = 3
network_timeout_seconds = 30
allowed_user_ids = []
drop_pending_updates = false
"""


class ConfigEditorTests(unittest.TestCase):
    def test_creates_private_config_from_example_and_saves_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "app.example.toml").write_text(EXAMPLE, encoding="utf-8")

            path, created = ensure_config_file(root)
            values, created_again = read_config_values(root)
            values["app"]["name"] = "ModulAI editado"
            values["web"]["port"] = 9876
            validated = write_config_values(root, values)

            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertEqual(path.name, "app.toml")
            self.assertEqual(validated.name, "ModulAI editado")
            self.assertEqual(validated.web.port, 9876)
            self.assertIn('name = "ModulAI editado"', path.read_text(encoding="utf-8"))

    def test_invalid_config_is_rejected_without_replacing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "app.example.toml").write_text(EXAMPLE, encoding="utf-8")
            path, _ = ensure_config_file(root)
            original = path.read_text(encoding="utf-8")
            values, _ = read_config_values(root)
            values["web"]["port"] = 70000

            with self.assertRaises(ValueError):
                write_config_values(root, values)

            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_rejects_telegram_network_timeout_outside_range(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "app.example.toml").write_text(EXAMPLE, encoding="utf-8")
            values, _ = read_config_values(root)
            values["telegram"]["network_timeout_seconds"] = 121

            with self.assertRaisesRegex(ValueError, "network_timeout_seconds"):
                write_config_values(root, values)


if __name__ == "__main__":
    unittest.main()
