from __future__ import annotations

import re
from pathlib import Path

TOPIC_RE = re.compile(r"^\d{2}-")


def is_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def safe_child_path(root: Path, child: str) -> Path:
    if not child or child.strip() != child:
        raise ValueError("folder_name must be non-empty and trimmed")
    raw = Path(child)
    if raw.is_absolute():
        raise ValueError("folder_name must be relative")
    root_resolved = root.resolve()
    candidate = (root_resolved / raw).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"folder_name escapes output root: {child}") from exc
    if candidate == root_resolved:
        raise ValueError("folder_name must identify a child directory")
    return candidate


def discover_topics(root: Path, *, include_archives: bool = False) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(root)
    if include_archives:
        return sorted(
            p for p in root.rglob("*")
            if p.is_dir() and TOPIC_RE.match(p.name)
        )
    return sorted(
        p for p in root.iterdir()
        if p.is_dir() and TOPIC_RE.match(p.name)
    )


def require_topics(root: Path, *, allow_empty: bool = False) -> list[Path]:
    topics = discover_topics(root)
    if not topics and not allow_empty:
        raise ValueError(f"no topic directories found under {root}")
    return topics
