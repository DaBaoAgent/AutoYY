from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
from pathlib import Path

from .config import work_root


def _is_writable_target(path: Path) -> bool:
    """Check whether path can be used without creating anything."""
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current.is_dir() and os.access(current, os.W_OK)


def run_doctor(repo_root: Path | None = None) -> dict:
    repo = repo_root or Path.cwd()
    required_commands = {
        "yt-dlp": shutil.which("yt-dlp"),
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
    }
    optional_commands = {
        "node": shutil.which("node"),
        "deno": shutil.which("deno"),
        "aria2c": shutil.which("aria2c"),
        "pwsh": shutil.which("pwsh"),
    }
    assets = [
        repo / "assets" / "topic-manifest-template.csv",
        repo / "assets" / "topic-cover-3x4-approved.png",
        repo / "assets" / "topic-cover-4x3-approved.png",
    ]
    root = work_root()
    writable = _is_writable_target(root)
    checks = {
        "python": {"ok": sys.version_info >= (3, 11), "value": platform.python_version()},
        "required_commands": {
            name: {"ok": bool(path), "path": path}
            for name, path in required_commands.items()
        },
        "optional_commands": {
            name: {"ok": bool(path), "path": path}
            for name, path in optional_commands.items()
        },
        "asr": {
            "funasr": importlib.util.find_spec("funasr") is not None,
            "faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
        },
        "work_root": {"ok": writable, "path": str(root), "exists": root.exists()},
        "assets": {str(path.name): path.is_file() for path in assets},
    }
    required_ok = (
        checks["python"]["ok"]
        and writable
        and all(item["ok"] for item in checks["required_commands"].values())
        and all(checks["assets"].values())
    )
    return {"ok": required_ok, "checks": checks}
