from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules" / "playlist_downloader" / "src"))
from modulai_playlist_downloader.plugin import (
    PlaylistDownloaderModule,
    PlaylistRequest,
    parse_playlist_request,
    validate_public_url,
)


class PlaylistRequestTests(unittest.TestCase):
    def test_parses_audio_request_with_options(self) -> None:
        request, error = parse_playlist_request(
            ("audio", "https://youtube.com/playlist?list=abc", "--limite", "12", "--formato", "m4a", "--calidad", "256K")
        )

        self.assertIsNone(error)
        self.assertEqual(request, PlaylistRequest("audio", "https://youtube.com/playlist?list=abc", 12, "m4a", "256K"))

    def test_rejects_invalid_type_format_and_limit(self) -> None:
        self.assertIsNotNone(parse_playlist_request(("podcast", "https://example.com"))[1])
        self.assertIsNotNone(parse_playlist_request(("audio", "https://example.com", "--formato", "mp4"))[1])
        self.assertIsNotNone(parse_playlist_request(("video", "https://example.com", "--limite", "0"))[1])

    def test_rejects_local_urls(self) -> None:
        self.assertIsNotNone(validate_public_url("http://127.0.0.1/playlist"))
        self.assertIsNotNone(validate_public_url("https://user:secret@example.com/playlist"))
        self.assertIsNone(validate_public_url("https://youtube.com/playlist?list=abc"))


class PlaylistDownloadTests(unittest.TestCase):
    def test_downloads_audio_playlist_and_generates_m3u(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            captured_options: list[dict[str, object]] = []

            class FakeYoutubeDL:
                def __init__(self, options) -> None:
                    self.options = options
                    captured_options.append(options)

                def __enter__(self):
                    return self

                def __exit__(self, *_args) -> None:
                    return None

                def extract_info(self, _url, download):
                    playlist = {
                        "id": "PL123",
                        "title": "Mis canciones",
                        "entries": [{"id": "a"}, {"id": "b"}],
                    }
                    if download:
                        output_dir = output_root / "PL123_-_Mis_canciones"
                        (output_dir / "001 - Uno.mp3").touch()
                        (output_dir / "002 - Dos.mp3").touch()
                    return playlist

            module = PlaylistDownloaderModule()
            module._context = SimpleNamespace(
                config={"output_dir": str(output_root), "max_items": 25, "audio_format": "mp3", "audio_quality": "192K"},
                data_dir=output_root,
            )
            progress: list[tuple[float, str]] = []
            job = SimpleNamespace(
                check_cancelled=lambda: None,
                report_progress=lambda value, detail: progress.append((value, detail)),
            )
            with patch.dict(sys.modules, {"yt_dlp": SimpleNamespace(YoutubeDL=FakeYoutubeDL)}):
                outcome = module._download_blocking(
                    job,
                    PlaylistRequest("audio", "https://youtube.com/playlist?list=PL123", limit=2),
                )

            self.assertEqual(captured_options[1]["playlistend"], 2)
            self.assertEqual(captured_options[1]["format"], "bestaudio/best")
            self.assertEqual(len(outcome.artifacts), 3)
            self.assertTrue(any(detail.startswith("Preparando 2") for _, detail in progress))
            self.assertTrue(any(detail == "Organizando archivos descargados" for _, detail in progress))
            m3u = Path(outcome.artifacts[-1])
            self.assertEqual(m3u.read_text(encoding="utf-8"), "#EXTM3U\n001 - Uno.mp3\n002 - Dos.mp3\n")


if __name__ == "__main__":
    unittest.main()
