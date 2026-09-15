from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules" / "facebook_video" / "src"))
from modulai_facebook_video.plugin import FacebookVideoModule, validate_facebook_url


class FacebookUrlTests(unittest.TestCase):
    def test_accepts_facebook_video_urls(self) -> None:
        self.assertIsNone(validate_facebook_url("https://www.facebook.com/watch/?v=123"))
        self.assertIsNone(validate_facebook_url("https://fb.watch/abc123/"))
        self.assertIsNone(validate_facebook_url("https://www.facebook.com/reel/123"))

    def test_rejects_other_domains_and_empty_paths(self) -> None:
        self.assertIsNotNone(validate_facebook_url("https://example.com/video"))
        self.assertIsNotNone(validate_facebook_url("https://facebook.com/"))
        self.assertIsNotNone(validate_facebook_url("javascript:alert(1)"))


class FacebookDownloadTests(unittest.TestCase):
    def test_downloads_mp4_named_after_video_title(self) -> None:
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
                    self.prepared_path = output_dir / "Mi video.webm"
                    self.prepared_path.with_suffix(".mp4").touch()
                    return {"id": "123", "title": "Mi video"}

                def prepare_filename(self, _info):
                    return str(self.prepared_path)

            fake_yt_dlp = SimpleNamespace(YoutubeDL=FakeYoutubeDL)
            module = FacebookVideoModule()
            module._context = SimpleNamespace(
                config={"output_dir": str(output_dir)},
                data_dir=output_dir,
            )
            job = SimpleNamespace(check_cancelled=lambda: None)
            url = "https://www.facebook.com/reel/123"

            with patch.dict(sys.modules, {"yt_dlp": fake_yt_dlp}):
                outcome = module._download_blocking(job, url)

            self.assertEqual(captured_options["source"], url)
            self.assertEqual(captured_options["merge_output_format"], "mp4")
            self.assertTrue(captured_options["windowsfilenames"])
            self.assertEqual(outcome.artifacts, (str(output_dir / "Mi video.mp4"),))


if __name__ == "__main__":
    unittest.main()
