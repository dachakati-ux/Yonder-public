"""Safe, bounded, revision-pinned GGUF download jobs for local Compose installs."""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx

from app.api.model_catalog import _get_json
from app.services.model_discovery import prepare_model_bundle

REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+() -]*\.gguf$", re.IGNORECASE)
REVISION = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_FILE_SIZE = 40 * 1024**3
CHUNK_SIZE = 1024**2
MAX_REDIRECTS = 5
ALLOWED_DOMAINS = ("huggingface.co", "hf.co")


def trusted_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 443)
        and any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_DOMAINS)
    )


def validate_request(repo: str, filename: str) -> None:
    if not REPO_ID.fullmatch(repo) or not FILE_NAME.fullmatch(filename):
        raise ValueError("Only a valid repository ID and top-level GGUF filename are allowed.")


async def verified_metadata(repo: str, filename: str) -> dict[str, object]:
    validate_request(repo, filename)
    owner, name = repo.split("/", 1)
    model = await _get_json(f"/models/{owner}/{name}", {"blobs": "true"})
    if not isinstance(model, dict) or model.get("gated") or model.get("private"):
        raise ValueError("Only public, non-gated model files may be installed.")
    revision = model.get("sha")
    if not isinstance(revision, str) or not REVISION.fullmatch(revision):
        raise ValueError("Hugging Face did not return an immutable model revision.")
    for entry in model.get("siblings", []):
        if not isinstance(entry, dict) or entry.get("rfilename") != filename:
            continue
        lfs = entry.get("lfs") or {}
        digest = lfs.get("sha256")
        size = lfs.get("size") or entry.get("size")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ValueError("Model file has no verified LFS SHA-256; refusing download.")
        if type(size) is not int or not 0 < size <= MAX_FILE_SIZE:
            raise ValueError("Unknown or oversized GGUF. Maximum download is 40 GiB.")
        return {"revision": revision, "sha256": digest, "size_bytes": size}
    raise ValueError("GGUF file not present in the pinned repository revision.")


class DownloadManager:
    """Process-local job state; completed files survive in the Docker volume."""

    def __init__(self, root: Path, *, max_root_bytes: int | None = None):
        self.root = root
        self.max_root_bytes = max_root_bytes
        self.jobs: dict[str, dict[str, object]] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    def _used_bytes(self) -> int:
        if not self.root.exists():
            return 0
        total = 0
        for item in self.root.rglob("*"):
            if item.is_file() and not item.is_symlink():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
        return total

    def public_jobs(self) -> list[dict[str, object]]:
        return list(self.jobs.values())[-30:][::-1]

    async def start(self, repo: str, filename: str) -> dict[str, object]:
        validate_request(repo, filename)
        if not self.root.is_absolute():
            raise ValueError("Install root must be absolute.")
        # Reserve a slot before starting work, including metadata checks.
        async with self._lock:
            active_jobs = [
                job for job in self.jobs.values()
                if job["status"] in ("queued", "validating", "downloading", "verifying")
            ]
            if len(active_jobs) >= 2:
                raise ValueError("Two downloads are already active; try again later.")
            if any(j["repo"] == repo and j["filename"] == filename for j in active_jobs):
                raise ValueError("A download for this model file is already active.")
            job_id = uuid.uuid4().hex
            job: dict[str, object] = {
                "id": job_id, "repo": repo, "filename": filename,
                "status": "queued", "downloaded_bytes": 0,
                "total_bytes": None, "error": None, "installed_path": None,
            }
            self.jobs[job_id] = job
            task = asyncio.create_task(self._run(job))
            self.tasks[job_id] = task
            task.add_done_callback(lambda _, key=job_id: self.tasks.pop(key, None))
            return job

    async def retry(self, job_id: str) -> dict[str, object]:
        old = self.jobs.get(job_id)
        if old is None:
            raise ValueError("Download job not found.")
        if old["status"] not in ("failed", "cancelled"):
            raise ValueError("Only failed or cancelled jobs can be retried.")
        return await self.start(str(old["repo"]), str(old["filename"]))

    async def remove(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        if job["status"] in ("queued", "validating", "downloading", "verifying"):
            raise ValueError("Cancel active download before removing.")
        installed = job.get("installed_path")
        if isinstance(installed, str) and installed:
            target = Path(installed)
            resolved_root = self.root.resolve()
            if target.exists() and not target.is_symlink():
                resolved_target = target.resolve()
                if (
                    resolved_target.is_relative_to(resolved_root)
                    and resolved_target != resolved_root
                ):
                    await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
        self.jobs.pop(job_id, None)
        return True

    async def cancel(self, job_id: str) -> bool:
        task = self.tasks.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def _run(self, job: dict[str, object]) -> None:
        temporary: Path | None = None
        folder: Path | None = None
        created_folder = False
        try:
            job["status"] = "validating"
            repo = str(job["repo"])
            filename = str(job["filename"])
            metadata = await verified_metadata(repo, filename)
            revision = str(metadata["revision"])
            expected_digest = str(metadata["sha256"])
            expected_size = int(metadata["size_bytes"])
            job["total_bytes"] = expected_size

            folder = self.root / repo.replace("/", "--") / revision / filename.removesuffix(".gguf")
            # No attacker-controlled subpath; no preexisting symlink directories.
            if any(
                ancestor.is_symlink()
                for ancestor in (
                    self.root,
                    self.root / repo.replace("/", "--"),
                    folder.parent,
                    folder,
                )
            ):
                raise ValueError("Install directory must not use symlinks.")
            if folder.exists():
                raise ValueError("This model is already installed or a partial folder exists.")
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.max_root_bytes is not None:
                used = await asyncio.to_thread(self._used_bytes)
                if used + expected_size > self.max_root_bytes:
                    raise ValueError("Model storage quota exceeded.")
            free = shutil.disk_usage(self.root).free
            if free < expected_size + max(1024**3, expected_size // 10):
                raise ValueError("Insufficient disk space for model and safety margin.")
            folder.mkdir(mode=0o700, parents=True, exist_ok=False)
            created_folder = True
            temporary = folder / (filename + ".partial")
            target = folder / filename
            url = (
                f"https://huggingface.co/{repo}/resolve/{revision}/"
                + quote(filename, safe="")
            )
            hasher = hashlib.sha256()
            length = 0
            job["status"] = "downloading"
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(45.0, connect=10.0),
                follow_redirects=False,
                headers={"User-Agent": "Yonder-ModelInstaller/0.1"},
            ) as client:
                for _ in range(MAX_REDIRECTS + 1):
                    if not trusted_url(url):
                        raise ValueError("Untrusted Hugging Face redirect destination.")
                    request = client.build_request("GET", url)
                    response = await client.send(request, stream=True)
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location", "")
                        await response.aclose()
                        if not location:
                            raise ValueError("Missing redirect location.")
                        url = urljoin(url, location)
                        continue
                    try:
                        response.raise_for_status()
                        with temporary.open("xb") as output:
                            async for chunk in response.aiter_bytes(CHUNK_SIZE):
                                length += len(chunk)
                                if length > expected_size:
                                    raise ValueError("Download exceeded published size.")
                                output.write(chunk)
                                hasher.update(chunk)
                                job["downloaded_bytes"] = length
                            output.flush()
                            os.fsync(output.fileno())
                    finally:
                        await response.aclose()
                    break
                else:
                    raise ValueError("Too many download redirects.")
            job["status"] = "verifying"
            if length != expected_size or hasher.hexdigest() != expected_digest:
                raise ValueError("Downloaded file size or SHA-256 mismatch.")
            temporary.replace(target)
            temporary = None
            # Registers a checksum-verified incomplete bundle; does not enable a runtime.
            await asyncio.to_thread(prepare_model_bundle, folder)
            job["installed_path"] = str(folder)
            job["status"] = "installed_unconfigured"
        except asyncio.CancelledError:
            job["status"] = "cancelled"
        except Exception as exc:  # noqa: BLE001 — job failures must remain observable
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            # Only remove a directory created by this job when it failed/cancelled.
            if created_folder and folder is not None and job["status"] in ("failed", "cancelled"):
                await asyncio.to_thread(shutil.rmtree, folder, ignore_errors=True)
