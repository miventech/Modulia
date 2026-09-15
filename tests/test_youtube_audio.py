from __future__ import annotations

import unittest
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules" / "youtube_audio" / "src"))
from modulai_youtube_audio.plugin import (
    YouTubeAudioModule,
    resolve_youtube_source,
    sanitize_filename,
    validate_youtube_url,
)


class YouTubeUrlTests(unittest.TestCase):
    def test_accepts_standard_and_short_urls(self) -> None:
        self.assertIsNone(validate_youtube_url("https://www.youtube.com/watch?v=abc123"))
        self.assertIsNone(validate_youtube_url("https://youtu.be/abc123"))

    def test_rejects_other_domains_and_empty_paths(self) -> None:
        self.assertIsNotNone(validate_youtube_url("https://example.com/video"))
        self.assertIsNotNone(validate_youtube_url("https://youtube.com/"))
        self.assertIsNotNone(validate_youtube_url("javascript:alert(1)"))

    def test_uses_first_youtube_result_for_a_song_name(self) -> None:
        source, error = resolve_youtube_source("Bohemian Rhapsody Queen")

        self.assertIsNone(error)
        self.assertEqual(source, "ytsearch1:Bohemian Rhapsody Queen")

    def test_preserves_and_validates_urls(self) -> None:
        source, error = resolve_youtube_source("https://youtu.be/abc123")
        self.assertIsNone(error)
        self.assertEqual(source, "https://youtu.be/abc123")

        _, error = resolve_youtube_source("https://example.com/video")
        self.assertIsNotNone(error)

    def test_sanitizes_titles_for_windows_filenames(self) -> None:
        self.assertEqual(sanitize_filename("Artista: canción / especial?"), "Artista - canción - especial")
        self.assertEqual(sanitize_filename("CON"), "CON_")
        self.assertEqual(sanitize_filename("..."), "audio")


class YouTubeDownloadTests(unittest.TestCase):
    def test_names_downloaded_mp3_after_video_title(self) -> None:
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
                    self.prepared_path = output_dir / "abc123.webm"
                    self.prepared_path.with_suffix(".mp3").touch()
                    video = {"id": "abc123", "title": "Mi: canción / bonita?"}
                    return {"entries": [video]}

                def prepare_filename(self, _info):
                    return str(self.prepared_path)

            fake_yt_dlp = SimpleNamespace(YoutubeDL=FakeYoutubeDL)
            module = YouTubeAudioModule()
            module._context = SimpleNamespace(
                config={"output_dir": str(output_dir)},
                data_dir=output_dir,
            )
            job = SimpleNamespace(check_cancelled=lambda: None)

            with patch.dict(sys.modules, {"yt_dlp": fake_yt_dlp}):
                outcome = module._download_blocking(job, "ytsearch1:Mi canción bonita")

            self.assertEqual(captured_options["outtmpl"], str(output_dir / "%(id)s.%(ext)s"))
            self.assertEqual(captured_options["source"], "ytsearch1:Mi canción bonita")
            self.assertEqual(outcome.artifacts, (str(output_dir / "Mi - canción - bonita.mp3"),))


if __name__ == "__main__":
    unittest.main()
