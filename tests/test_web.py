from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

from modulai.bootstrap import Application
from modulai.web import MessageRequest, create_web_app
from tests.test_application import make_config


def endpoint_for(web_app: object, path: str, method: str):
    for route in web_app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"No existe la ruta {method} {path}")


class WebApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_endpoint_reads_existing_toml(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            application = Application.create(
                project_root,
                make_config(project_root, temporary_directory),
            )
            await application.start()
            web_app = create_web_app(application)
            get_config = endpoint_for(web_app, "/api/v1/config", "GET")

            response = await get_config()
            await application.stop()

            self.assertIn("app", response["data"]["values"])
            self.assertFalse(response["data"]["created_from_example"])

    async def test_config_endpoint_rejects_invalid_port(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            application = Application.create(
                project_root,
                make_config(project_root, temporary_directory),
            )
            await application.start()
            web_app = create_web_app(application)
            update_config = endpoint_for(web_app, "/api/v1/config", "PUT")

            with self.assertRaises(HTTPException) as raised:
                await update_config({"web": {"host": "127.0.0.1", "port": 70000}})
            await application.stop()

            self.assertEqual(raised.exception.status_code, 400)

    async def test_message_endpoint_executes_a_command(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            application = Application.create(
                project_root,
                make_config(project_root, temporary_directory),
            )
            await application.start()
            web_app = create_web_app(application)
            send_message = endpoint_for(web_app, "/api/v1/messages", "POST")

            response = await send_message(MessageRequest(text="/hola"))
            await application.stop()

            self.assertTrue(response["data"]["ok"])
            self.assertIn("funcionan correctamente", response["data"]["text"])

    async def test_message_model_rejects_empty_text(self) -> None:
        with self.assertRaises(ValidationError):
            MessageRequest(text="")

    async def test_delete_memory_endpoint_reports_not_found(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            application = Application.create(
                project_root,
                make_config(project_root, temporary_directory),
            )
            await application.start()
            web_app = create_web_app(application)
            delete_memory = endpoint_for(web_app, "/api/v1/memories/{memory_id}", "DELETE")

            with self.assertRaises(HTTPException) as raised:
                await delete_memory(memory_id=999, principal_id="local-user")
            await application.stop()

            self.assertEqual(raised.exception.status_code, 404)

    async def test_media_endpoints_list_stream_and_delete_only_data_files(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            application = Application.create(
                project_root,
                make_config(project_root, temporary_directory),
            )
            application.config.data_dir.mkdir(parents=True, exist_ok=True)
            music = application.config.data_dir / "downloads" / "canción.mp3"
            music.parent.mkdir(parents=True, exist_ok=True)
            music.write_bytes(b"fake audio")
            transcript = application.config.data_dir / "transcriptions" / "job123_audio.txt"
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text("Transcripción de audio.mp3:\n\nHola mundo.", encoding="utf-8")
            (application.config.data_dir / "notes.txt").write_text("not media", encoding="utf-8")
            await application.start()
            web_app = create_web_app(application)
            list_media = endpoint_for(web_app, "/api/v1/media", "GET")
            stream_media = endpoint_for(web_app, "/api/v1/media/{media_path:path}", "GET")
            delete_media = endpoint_for(web_app, "/api/v1/media/{media_path:path}", "DELETE")

            response = await list_media()
            media_by_id = {item["id"]: item for item in response["data"]}
            self.assertEqual(media_by_id["downloads/canción.mp3"]["kind"], "audio")
            self.assertEqual(media_by_id["transcriptions/job123_audio.txt"]["kind"], "text")
            self.assertIn("Hola mundo", media_by_id["transcriptions/job123_audio.txt"]["text"])
            self.assertNotIn("notes.txt", media_by_id)
            streamed = await stream_media("downloads/canción.mp3")
            self.assertEqual(Path(streamed.path), music)
            deleted = await delete_media("downloads/canción.mp3")
            await application.stop()

            self.assertEqual(deleted["data"]["deleted"], "downloads/canción.mp3")
            self.assertFalse(music.exists())

    async def test_media_endpoint_rejects_paths_outside_data_directory(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            application = Application.create(
                project_root,
                make_config(project_root, temporary_directory),
            )
            await application.start()
            web_app = create_web_app(application)
            stream_media = endpoint_for(web_app, "/api/v1/media/{media_path:path}", "GET")

            with self.assertRaises(HTTPException) as raised:
                await stream_media("../outside.mp3")
            await application.stop()

            self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
