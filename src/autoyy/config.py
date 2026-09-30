from __future__ import annotations

import os
from pathlib import Path

DEFAULT_WINDOWS_WORK_ROOT = Path(r"D:\自动剪辑")


def work_root() -> Path:
    configured = os.environ.get("AUTOYY_WORK_ROOT")
    return Path(configured).expanduser() if configured else DEFAULT_WINDOWS_WORK_ROOT


def peer_library(root: Path | None = None) -> Path:
    base = root or Path.cwd()
    return base / ".autoyy" / "peer-hit-library.csv"
