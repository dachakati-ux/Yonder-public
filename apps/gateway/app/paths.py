"""Resolve repository paths for source checkouts and offline installs."""

from __future__ import annotations

import os
from pathlib import Path

_INSTALLED_REPO_ROOT = Path("/opt/yonder/repo")


def resolve_repo_root(start: Path | None = None) -> Path:
    """Locate the Yonder repository root that contains ``config/``.

    Offline installs run the Gateway from a wheel under ``site-packages`` while
    the config tree lives at ``/opt/yonder/repo``. Prefer an explicit
    ``YONDER_REPO_ROOT``, then walk from the caller, then the process cwd, then
    the installed default.
    """

    env = (os.environ.get("YONDER_REPO_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()

    origin = (start or Path(__file__)).resolve()
    for parent in [origin, *origin.parents]:
        if (parent / "config" / "models").is_dir():
            return parent

    cwd = Path.cwd().resolve()
    if (cwd / "config" / "models").is_dir():
        return cwd

    if (_INSTALLED_REPO_ROOT / "config" / "models").is_dir():
        return _INSTALLED_REPO_ROOT.resolve()

    # Last resort: source-tree relative location of this module.
    return Path(__file__).resolve().parents[3]
