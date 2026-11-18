"""Worker speech runtime — dry_run and fail-closed paths."""

from __future__ import annotations

from pathlib import Path

from app.runtime.speech_manager import SpeechRuntimeManager
from yonder_configuration.models import SpeechWorkerRuntimeConfig


def test_disabled_reports_not_supported() -> None:
    manager = SpeechRuntimeManager(SpeechWorkerRuntimeConfig(enabled=False))
    status = manager.status()
    assert status["availability"] == "not_supported"
    result = manager.transcribe(audio=b"abc", filename="a.wav")
    assert result["availability"] == "not_supported"
    audio, _mime, meta = manager.synthesize(text="hi")
    assert audio == b""
    assert meta["availability"] == "not_supported"


def test_dry_run_transcribe_and_synthesize(tmp_path: Path) -> None:
    manager = SpeechRuntimeManager(
        SpeechWorkerRuntimeConfig(
            enabled=True,
            dry_run=True,
            allowed_model_roots=[tmp_path],
            output_root=tmp_path / "out",
            device="cpu",
        )
    )
    assert manager.status()["availability"] == "mock"
    manager.load(deployment_id="stt-dry", kind="stt", model_path=None)
    tx = manager.transcribe(audio=b"\x00\x01", filename="clip.webm")
    assert tx["availability"] == "mock"
    assert "transcript" in tx["text"]
    audio, mime, meta = manager.synthesize(text="hello from dry run")
    assert mime == "audio/wav"
    assert meta["availability"] == "mock"
    assert audio[:4] == b"RIFF"
