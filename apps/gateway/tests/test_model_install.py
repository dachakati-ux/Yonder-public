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
