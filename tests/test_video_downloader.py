from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules" / "video_downloader" / "src"))
from modulai_video_downloader.plugin import VideoDownloaderModule, validate_video_url


class VideoUrlTests(unittest.TestCase):
    def test_accepts_public_http_urls(self) -> None:
        self.assertIsNone(validate_video_url("https://vimeo.com/123456"))
        self.assertIsNone(validate_video_url("https://example.com/video/123"))

    def test_rejects_non_http_and_local_urls(self) -> None:
        self.assertIsNotNone(validate_video_url("file:///C:/video.mp4"))
        self.assertIsNotNone(validate_video_url("http://localhost/video"))
        self.assertIsNotNone(validate_video_url("http://127.0.0.1/video"))
        self.assertIsNotNone(validate_video_url("http://192.168.1.10/video"))
        self.assertIsNotNone(validate_video_url("https://user:secret@example.com/video"))


class VideoDownloadTests(unittest.TestCase):
    def test_downloads_best_video_with_title_as_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            captured_options = {}

            class FakeYoutubeDL:
                def __init__(self, options) -> None:
                    captured_options.update(options)

                def __enter__(self):
                    return self

                def __exit__(self, *_args) -> None:
                    return None

                def extract_info(self, url, download):
                    captured_options["source"] = url
                    self.prepared_path = output_dir / "Video de prueba.webm"
                    self.prepared_path.with_suffix(".mp4").touch()
                    return {"id": "123", "title": "Video de prueba"}

                def prepare_filename(self, _info):
                    return str(self.prepared_path)

            fake_yt_dlp = SimpleNamespace(YoutubeDL=FakeYoutubeDL)
            module = VideoDownloaderModule()
            module._context = SimpleNamespace(
                config={"output_dir": str(output_dir)},
                data_dir=output_dir,
            )
            job = SimpleNamespace(check_cancelled=lambda: None)
            url = "https://vimeo.com/123456"

            with patch.dict(sys.modules, {"yt_dlp": fake_yt_dlp}):
                outcome = module._download_blocking(job, url)

            self.assertEqual(captured_options["source"], url)
            self.assertEqual(captured_options["format"], "bestvideo*+bestaudio/best")
            self.assertEqual(outcome.artifacts, (str(output_dir / "Video de prueba.mp4"),))


if __name__ == "__main__":
    unittest.main()
