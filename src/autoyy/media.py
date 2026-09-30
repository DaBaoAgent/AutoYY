from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .paths import is_nonempty_file

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v"}
SUBTITLE_EXTENSIONS = {".srt"}


def find_primary_video(folder: Path) -> Path | None:
    preferred = [folder / f"高清源视频{ext}" for ext in sorted(VIDEO_EXTENSIONS)]
    for path in preferred:
        if is_nonempty_file(path):
            return path
    videos = [p for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS and is_nonempty_file(p)]
    return max(videos, key=lambda p: p.stat().st_size) if videos else None


def find_primary_subtitle(folder: Path) -> Path | None:
    preferred = folder / "字幕.srt"
    if is_nonempty_file(preferred):
        return preferred
    items = sorted(p for p in folder.glob("*.srt") if is_nonempty_file(p))
    return items[0] if items else None


def probe_media(path: Path, ffprobe: str = "ffprobe", *, timeout: int = 120) -> dict:
    cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"ffprobe unavailable/timeout: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()[:300]}")
    try:
        payload = json.loads(proc.stdout)
        duration = float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("ffprobe returned invalid duration") from exc
    if duration <= 0:
        raise RuntimeError("media duration must be positive")
    return {"duration": duration}
