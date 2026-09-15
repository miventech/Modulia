from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules" / "transcription" / "src"))
from modulai_transcription.plugin import TranscriptionModule


class TranscriptionModuleTests(unittest.TestCase):
    def test_rejects_unsupported_extension_and_oversized_input(self) -> None:
        module = TranscriptionModule()
        module._context = SimpleNamespace(config={"max_file_mb": 1})
        with tempfile.TemporaryDirectory() as directory:
            unsupported = Path(directory) / "nota.txt"
            unsupported.write_text("texto", encoding="utf-8")
            self.assertIn("Formato no compatible", module._validate_input(unsupported, None) or "")
            audio = Path(directory) / "audio.mp3"
            audio.touch()
            self.assertIn("supera el límite", module._validate_input(audio, 2 * 1024 * 1024) or "")

    def test_accepts_telegram_voice_container(self) -> None:
        module = TranscriptionModule()
        module._context = SimpleNamespace(config={"max_file_mb": 1})
        with tempfile.TemporaryDirectory() as directory:
            voice = Path(directory) / "nota.oga"
            voice.touch()
            self.assertIsNone(module._validate_input(voice, 1))

    def test_returns_transcript_from_local_engine(self) -> None:
        class FakeModel:
            def __init__(self, *_args, **_kwargs) -> None:
                return None

            def transcribe(self, _path, language=None, initial_prompt=None):
                del language
                self.initial_prompt = initial_prompt
                segments = [
                    SimpleNamespace(text=" Hola", end=1.0),
                    SimpleNamespace(text=" mundo.", end=2.0),
                ]
                return segments, SimpleNamespace(duration=2.0)

        module = TranscriptionModule()
        module._context = SimpleNamespace(
            config={
                "model": "small",
                "device": "cpu",
                "compute_type": "int8",
                "retain_input": True,
            },
            data_dir=None,
        )
        progress: list[tuple[float, str]] = []
        job = SimpleNamespace(
            check_cancelled=lambda: None,
            report_progress=lambda value, detail: progress.append((value, detail)),
        )
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.mp3"
            audio.touch()
            module._context.data_dir = Path(directory) / "data"
            expected_transcript = module._context.data_dir / "transcriptions" / "job123_audio.txt"
            engine = SimpleNamespace(WhisperModel=FakeModel)
            job.id = "job123"
            with patch.dict(sys.modules, {"faster_whisper": engine}):
                outcome = module._transcribe_blocking(job, audio, "reunión de ventas")
            self.assertEqual(expected_transcript.read_text(encoding="utf-8"), outcome.text)

        self.assertEqual(
            outcome.text,
            "Transcripción de audio.mp3:\nContexto recibido: reunión de ventas\n\nHola mundo.",
        )
        self.assertEqual(outcome.artifacts, (str(expected_transcript),))
        self.assertTrue(any(detail == "Transcribiendo audio" for _, detail in progress))


if __name__ == "__main__":
    unittest.main()
