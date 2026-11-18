"""Local durable artifact storage — paths never exposed to clients."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ALLOWED_MIME = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/svg+xml",
    "video/mp4",
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp3",
    "audio/webm",
    "audio/ogg",
    "audio/mp4",
    "application/octet-stream",
}

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# Separate ceiling for worker-fetched generation outputs (images/video).
MAX_GENERATED_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class StoredArtifact:
    artifact_id: str
    storage_key: str
    filename: str
    mime_type: str
    byte_size: int
    sha256: str


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        *,
        data: bytes,
        filename: str,
        mime_type: str,
        kind: str = "reference",
    ) -> StoredArtifact:
        if mime_type not in ALLOWED_MIME:
            raise ValueError(f"unsupported_mime_type:{mime_type}")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("upload_too_large")
        if len(data) == 0:
            raise ValueError("upload_empty")
        artifact_id = f"art-{uuid.uuid4().hex[:16]}"
        digest = hashlib.sha256(data).hexdigest()
        safe_name = Path(filename).name or "upload.bin"
        storage_key = f"{kind}/{artifact_id}/{safe_name}"
        path = self.root / storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredArtifact(
            artifact_id=artifact_id,
            storage_key=storage_key,
            filename=safe_name,
            mime_type=mime_type,
            byte_size=len(data),
            sha256=digest,
        )

    def put_stream(
        self,
        *,
        chunks: Iterable[bytes],
        filename: str,
        mime_type: str,
        kind: str = "generated",
        max_bytes: int | None = None,
    ) -> StoredArtifact:
        """Accept bounded worker-fetched bytes without buffering an unbounded body."""
        limit = max_bytes
        if limit is None:
            limit = (
                MAX_GENERATED_BYTES if kind == "generated" else MAX_UPLOAD_BYTES
            )
        if mime_type not in ALLOWED_MIME:
            raise ValueError(f"unsupported_mime_type:{mime_type}")
        data = bytearray()
        for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise ValueError("artifact_chunk_must_be_bytes")
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError("generated_artifact_too_large")
        if len(data) == 0:
            raise ValueError("upload_empty")
        artifact_id = f"art-{uuid.uuid4().hex[:16]}"
        digest = hashlib.sha256(data).hexdigest()
        safe_name = Path(filename).name or "upload.bin"
        storage_key = f"{kind}/{artifact_id}/{safe_name}"
        path = self.root / storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredArtifact(
            artifact_id=artifact_id,
            storage_key=storage_key,
            filename=safe_name,
            mime_type=mime_type,
            byte_size=len(data),
            sha256=digest,
        )

    def resolve_path(self, storage_key: str) -> Path:
        path = (self.root / storage_key).resolve()
        root = self.root.resolve()
        if not str(path).startswith(str(root)):
            raise ValueError("invalid_storage_key")
        if not path.exists():
            raise FileNotFoundError(storage_key)
        return path

    def read(self, storage_key: str) -> bytes:
        return self.resolve_path(storage_key).read_bytes()

    def delete(self, storage_key: str) -> None:
        try:
            path = (self.root / storage_key).resolve()
            root = self.root.resolve()
            if not str(path).startswith(str(root)):
                return
            if path.exists() and path.is_file():
                path.unlink()
        except OSError:
            return
