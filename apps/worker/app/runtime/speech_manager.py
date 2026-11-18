"""Worker-owned STT/TTS runtime.

Real loads require optional whisper/piper packages and model bundles.
When disabled or bundles are missing, endpoints report not_supported —
never invent transcripts or audio.
"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime
from os import environ
from pathlib import Path
from typing import Any

from yonder_configuration.models import SpeechWorkerRuntimeConfig


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _silence_wav(duration_seconds: float = 0.4, sample_rate: int = 16000) -> bytes:
    """Minimal valid WAV used only for dry_run synthesize tests."""
    frames = int(duration_seconds * sample_rate)
    buf = tempfile.SpooledTemporaryFile()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * frames)
    buf.seek(0)
    return buf.read()


@dataclass
class ManagedSpeechRuntime:
    deployment_id: str
    kind: str  # stt | tts
    model_path: str | None
    status: str = "loading"
    message: str = ""
    updated_at: str = field(default_factory=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "kind": self.kind,
            "model_path": self.model_path,
            "status": self.status,
            "message": self.message,
            "updated_at": self.updated_at,
        }


class SpeechRuntimeManager:
    def __init__(self, config: SpeechWorkerRuntimeConfig) -> None:
        self._config = config
        self._deployments: dict[str, ManagedSpeechRuntime] = {}
        self._stt_model: Any = None
        self._stt_path: Path | None = None
        self._tts_voice: Any = None
        self._tts_path: Path | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    @property
    def dry_run(self) -> bool:
        return bool(self._config.dry_run)

    def list_deployments(self) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self._deployments.values()]

    def status(self) -> dict[str, Any]:
        stt_ready = any(
            d.status == "ready" and d.kind == "stt" for d in self._deployments.values()
        )
        tts_ready = any(
            d.status == "ready" and d.kind == "tts" for d in self._deployments.values()
        )
        if self.dry_run and self.enabled:
            availability = "mock"
        elif not self.enabled:
            availability = "not_supported"
        elif stt_ready or tts_ready:
            availability = "ready"
        else:
            availability = "not_configured"
        return {
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "availability": availability,
            "stt_ready": stt_ready or (self.dry_run and self.enabled),
            "tts_ready": tts_ready or (self.dry_run and self.enabled),
            "deployments": self.list_deployments(),
        }

    def _path_allowed(self, path: Path) -> bool:
        resolved = path.expanduser().resolve()
        for root in self._config.allowed_model_roots:
            try:
                resolved.relative_to(root.expanduser().resolve())
                return True
            except ValueError:
                continue
        return False

    def load(
        self,
        *,
        deployment_id: str,
        model_definition_id: str | None = None,
        runtime_configuration: dict[str, Any] | None = None,
        gpu_assignments: list[int] | None = None,
        maintenance: bool = False,
        already_occupied: set[int] | None = None,
        kind: str | None = None,
        model_path: str | None = None,
    ) -> dict[str, Any]:
        del model_definition_id, gpu_assignments, maintenance, already_occupied
        if not self.enabled and not self.dry_run:
            raise RuntimeError("Speech worker runtime is disabled")
        cfg = dict(runtime_configuration or {})
        kind_norm = (
            kind or str(cfg.get("speech_kind") or cfg.get("kind") or "stt")
        ).lower().strip()
        path_raw = model_path or cfg.get("model_path") or cfg.get("source_path")
        if kind_norm not in {"stt", "tts"}:
            raise RuntimeError(f"Unsupported speech kind: {kind_norm}")
        record = ManagedSpeechRuntime(
            deployment_id=deployment_id,
            kind=kind_norm,
            model_path=str(path_raw) if path_raw else None,
            message="Loading speech runtime",
        )
        self._deployments[deployment_id] = record
        if self.dry_run:
            record.status = "ready"
            record.message = "Dry-run speech runtime acknowledged"
            record.updated_at = _now()
            return record.as_dict()
        if not path_raw:
            # Fall back to configured defaults.
            if kind_norm == "stt" and self._config.whisper_model_path:
                path_raw = str(self._config.whisper_model_path)
            elif kind_norm == "tts" and self._config.piper_model_path:
                path_raw = str(self._config.piper_model_path)
        if not path_raw:
            self._deployments.pop(deployment_id, None)
            raise RuntimeError("model_path is required for speech load")
        path = Path(str(path_raw)).expanduser()
        record.model_path = str(path)
        if not self._path_allowed(path):
            self._deployments.pop(deployment_id, None)
            raise RuntimeError(f"model_path outside allowed roots: {path}")
        if not path.exists():
            self._deployments.pop(deployment_id, None)
            raise RuntimeError(f"Speech model path not found: {path}")
        if kind_norm == "stt":
            self._load_stt(path)
        else:
            self._load_tts(path)
        record.status = "ready"
        record.message = f"Speech {kind_norm} ready"
        record.updated_at = _now()
        return record.as_dict()

    def drain(self, deployment_id: str) -> dict[str, Any]:
        record = self._deployments.get(deployment_id)
        if record is None:
            raise RuntimeError(f"Unknown speech deployment: {deployment_id}")
        record.status = "draining"
        record.message = "Speech runtime draining"
        record.updated_at = _now()
        return record.as_dict()

    def _load_stt(self, path: Path) -> None:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed on this worker"
            ) from exc
        device = "cuda" if self._config.device == "cuda" else "cpu"
        self._stt_model = WhisperModel(
            str(path),
            device=device,
            compute_type="float16" if device == "cuda" else "int8",
        )
        self._stt_path = path

    def _load_tts(self, path: Path) -> None:
        """Prefer python piper-tts (RHEL8-safe); fall back to piper binary."""
        try:
            from piper import PiperVoice  # type: ignore

            self._tts_voice = PiperVoice.load(str(path))
            self._tts_path = path
            return
        except ImportError:
            self._tts_voice = None
        binary = self._config.piper_binary
        if binary is None or not Path(binary).expanduser().exists():
            raise RuntimeError(
                "piper-tts is not installed and piper binary is missing"
            )
        # Binary path validated; synthesize will invoke it.
        self._tts_path = path

    def unload(self, deployment_id: str) -> dict[str, Any]:
        record = self._deployments.pop(deployment_id, None)
        if record is None:
            return {
                "deployment_id": deployment_id,
                "status": "stopped",
                "message": "Deployment already stopped",
                "updated_at": _now(),
            }
        if record.kind == "stt":
            self._stt_model = None
            self._stt_path = None
        if record.kind == "tts":
            self._tts_voice = None
            self._tts_path = None
        record.status = "stopped"
        record.message = "Speech runtime unloaded"
        record.updated_at = _now()
        return record.as_dict()

    def transcribe(
        self,
        *,
        audio: bytes,
        filename: str,
        language: str | None = None,
    ) -> dict[str, Any]:
        if self.dry_run and self.enabled:
            return {
                "text": "yonder dry run transcript",
                "availability": "mock",
                "language": language or "en",
                "message": "Dry-run STT; no model was invoked",
            }
        if not self.enabled:
            return {
                "text": "",
                "availability": "not_supported",
                "message": "Speech runtime disabled",
                "code": "not_supported",
            }
        ready_stt = [
            d for d in self._deployments.values() if d.kind == "stt" and d.status == "ready"
        ]
        if not ready_stt or self._stt_model is None:
            return {
                "text": "",
                "availability": "not_configured",
                "message": "No READY STT deployment on this worker",
                "code": "not_configured",
            }
        suffix = Path(filename).suffix or ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(audio)
            tmp.flush()
            segments, info = self._stt_model.transcribe(
                tmp.name,
                language=language,
                vad_filter=True,
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
        return {
            "text": text,
            "availability": "ready",
            "language": getattr(info, "language", language),
            "duration_seconds": getattr(info, "duration", None),
            "deployment_id": ready_stt[0].deployment_id,
        }

    def _synthesize_python(self, *, text: str, voice: Any) -> bytes:
        """Render WAV via piper-tts Python API."""
        import io

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            # piper-tts 1.x: synthesize_wav writes frames into a wave writer.
            if hasattr(voice, "synthesize_wav"):
                voice.synthesize_wav(text, wf)
            else:
                # Fallback: collect AudioChunk stream.
                sample_rate = int(getattr(voice.config, "sample_rate", 22050))
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                for chunk in voice.synthesize(text):
                    audio_bytes = getattr(chunk, "audio_int16_bytes", None)
                    if audio_bytes is None and hasattr(chunk, "audio_int16"):
                        audio_bytes = bytes(chunk.audio_int16)
                    if audio_bytes:
                        wf.writeframes(audio_bytes)
        return buf.getvalue()

    def synthesize(
        self, *, text: str, voice: str | None = None
    ) -> tuple[bytes, str, dict[str, Any]]:
        if self.dry_run and self.enabled:
            return (
                _silence_wav(),
                "audio/wav",
                {
                    "availability": "mock",
                    "message": "Dry-run TTS; silence WAV generated",
                },
            )
        if not self.enabled:
            return (
                b"",
                "audio/wav",
                {
                    "availability": "not_supported",
                    "message": "Speech runtime disabled",
                    "code": "not_supported",
                },
            )
        ready_tts = [
            d for d in self._deployments.values() if d.kind == "tts" and d.status == "ready"
        ]
        if not ready_tts:
            return (
                b"",
                "audio/wav",
                {
                    "availability": "not_configured",
                    "message": "No READY TTS deployment on this worker",
                    "code": "not_configured",
                },
            )
        model_path = ready_tts[0].model_path or (
            str(self._config.piper_model_path)
            if self._config.piper_model_path
            else None
        )
        if not model_path:
            return (
                b"",
                "audio/wav",
                {
                    "availability": "not_configured",
                    "message": "Piper model_path not configured",
                    "code": "not_configured",
                },
            )
        if self._tts_voice is not None:
            try:
                audio = self._synthesize_python(text=text, voice=self._tts_voice)
            except Exception as exc:  # noqa: BLE001
                return (
                    b"",
                    "audio/wav",
                    {
                        "availability": "not_reported",
                        "message": f"piper-tts failed: {exc}"[:400],
                        "code": "provider_failure",
                    },
                )
            return (
                audio,
                "audio/wav",
                {
                    "availability": "ready",
                    "deployment_id": ready_tts[0].deployment_id,
                },
            )
        binary = self._config.piper_binary
        if binary is None:
            return (
                b"",
                "audio/wav",
                {
                    "availability": "not_configured",
                    "message": "piper-tts not loaded and piper binary missing",
                    "code": "not_configured",
                },
            )
        binary_path = Path(binary).expanduser()
        env = {
            **dict(environ),
            "LD_LIBRARY_PATH": (
                f"{binary_path.parent}:{environ.get('LD_LIBRARY_PATH', '')}"
            ).rstrip(":"),
        }
        espeak_data = binary_path.parent / "espeak-ng-data"
        if espeak_data.is_dir():
            env["ESPEAK_DATA_PATH"] = str(espeak_data)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.wav"
            proc = subprocess.run(
                [
                    str(binary_path),
                    "--model",
                    str(model_path),
                    "--output_file",
                    str(out),
                ],
                input=text.encode("utf-8"),
                capture_output=True,
                check=False,
                env=env,
                cwd=str(binary_path.parent),
            )
            if proc.returncode != 0 or not out.exists():
                return (
                    b"",
                    "audio/wav",
                    {
                        "availability": "not_reported",
                        "message": (
                            proc.stderr.decode("utf-8", errors="replace")[:400]
                            or "piper synthesize failed"
                        ),
                        "code": "provider_failure",
                    },
                )
            return (
                out.read_bytes(),
                "audio/wav",
                {
                    "availability": "ready",
                    "deployment_id": ready_tts[0].deployment_id,
                },
            )
