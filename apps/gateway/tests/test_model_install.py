"""Offline contract tests for local model-install preflight."""
from __future__ import annotations

import pytest
from app.services.model_install import trusted_url, validate_request


@pytest.mark.parametrize(
    "url",
    [
        "https://huggingface.co/example/model",
        "https://cdn-lfs-us-1.hf.co/object",
        "https://cas-server.xethub.hf.co/transfer",
    ],
)
def test_trusted_hf_download_hosts(url):
    assert trusted_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://huggingface.co/example/model",
        "https://huggingface.co.evil.example/file",
        "https://localhost/file",
        "https://127.0.0.1/file",
        "file:///etc/passwd",
        "https://huggingface.co:1234/file",
        "https://user:pass@huggingface.co/file",
        "https://cloudfront.net/unknown",
    ],
)
def test_untrusted_download_urls_blocked(url):
    assert not trusted_url(url)


@pytest.mark.parametrize(
    ("repo", "filename"),
    [
        ("../../../etc", "a.gguf"),
        ("owner/model", "../secret.gguf"),
        ("owner/model", "folder/file.gguf"),
        ("owner/model", "weights.safetensors"),
        ("owner/model", ".hidden.gguf"),
    ],
)
def test_invalid_download_names_rejected(repo, filename):
    with pytest.raises(ValueError):
        validate_request(repo, filename)


def test_valid_download_name():
    validate_request("example/Model-GGUF", "model.Q4_K_M.gguf")


@pytest.mark.asyncio
async def test_download_manager_duplicate_guard_quota_retry_and_remove(tmp_path, monkeypatch):
    import asyncio

    from app.services.model_install import DownloadManager

    async def fake_meta(_repo: str, _filename: str) -> dict[str, object]:
        await asyncio.sleep(0.05)
        return {
            "revision": "a" * 40,
            "sha256": "b" * 64,
            "size_bytes": 1024,
        }

    monkeypatch.setattr("app.services.model_install.verified_metadata", fake_meta)
    mgr = DownloadManager(tmp_path, max_root_bytes=512)

    job = await mgr.start("example/Model-GGUF", "model.Q4_K_M.gguf")
    # Duplicate active download must be rejected.
    with pytest.raises(ValueError, match="already active"):
        await mgr.start("example/Model-GGUF", "model.Q4_K_M.gguf")

    await mgr.tasks[job["id"]]
    # Exceeded max_root_bytes=512 (size_bytes=1024) -> failed with quota error.
    assert job["status"] == "failed"
    assert "quota" in str(job["error"]).lower()

    # Retry creates a new job for failed/cancelled jobs.
    mgr.max_root_bytes = 4096
    retried = await mgr.retry(str(job["id"]))
    assert retried["id"] != job["id"]
    await mgr.cancel(str(retried["id"]))
    await asyncio.sleep(0.02)
    assert await mgr.remove(str(job["id"])) is True
