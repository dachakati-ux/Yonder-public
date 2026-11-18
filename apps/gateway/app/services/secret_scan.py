"""Content-level secret scanning for the coder jail boundary.

Filename heuristics catch secret *files*; this module catches secret
*content* inside otherwise ordinary files, diffs, and command output before
any of it leaves the jail toward the model, the run store, or the console.

Design rules:
- High-signal patterns only — a redaction must mean something.
- Redact and count; never silently drop the surrounding text.
- Fail closed: a scanner error refuses the read instead of leaking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Order matters: the first matching pattern names the redaction.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
            r"(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
            re.DOTALL,
        ),
    ),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "aws_secret_access_key",
        re.compile(
            r"(?i)\baws_secret_access_key\b\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}['\"]?"
        ),
    ),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    ),
    (
        "bearer_header",
        re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._\-]{16,}"),
    ),
    (
        "url_basic_auth",
        re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s/:@]{1,64}:[^\s/@]{4,}@"),
    ),
    (
        "npm_auth_token",
        re.compile(r"(?i)\b_authToken\s*=\s*\S{16,}"),
    ),
    (
        "netrc_password",
        re.compile(r"(?i)\bpassword\s+\S{6,}"),
    ),
    (
        "assignment_secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret[_-]?key|client[_-]?secret|"
            r"auth[_-]?token|access[_-]?token|private[_-]?key)\b"
            r"\s*[=:]\s*['\"][^'\"\s]{12,}['\"]"
        ),
    ),
)


@dataclass(frozen=True)
class ScanResult:
    text: str
    redactions: int
    kinds: tuple[str, ...]


def redact_secrets(text: str) -> ScanResult:
    """Redact high-signal secret content. Returns redacted text + counts."""
    if not text:
        return ScanResult(text=text, redactions=0, kinds=())
    redacted = text
    total = 0
    kinds: list[str] = []
    for kind, pattern in _PATTERNS:
        redacted, count = pattern.subn(f"[REDACTED:{kind}]", redacted)
        if count:
            total += count
            kinds.append(kind)
    return ScanResult(text=redacted, redactions=total, kinds=tuple(kinds))
