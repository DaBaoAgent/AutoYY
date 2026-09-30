from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .paths import safe_child_path

FIELDS = [
    "id", "folder_name", "topic_cn", "video_title_cn", "original_title", "url",
    "channel", "duration_seconds", "max_height", "view_count", "verified_at",
    "source_language", "subtitle_language", "match_grade", "rights_note", "notes",
]
REQUIRED_FIELDS = {"folder_name", "url"}
MATCH_GRADES = {"exact", "high", "adjacent", "reject", ""}


@dataclass(slots=True)
class ManifestRow:
    data: dict[str, str]

    @property
    def folder_name(self) -> str:
        return self.data.get("folder_name", "").strip()

    @property
    def url(self) -> str:
        return self.data.get("url", "").strip()


def is_supported_youtube_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return False
    if host in {"youtu.be", "www.youtu.be"}:
        return bool(parsed.path.strip("/"))
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        return parsed.path == "/watch" and bool(parsed.query)
    return False


def load_manifest(path: Path, *, output_root: Path | None = None, validate_urls: bool = True) -> list[ManifestRow]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = set(reader.fieldnames or [])
        missing = REQUIRED_FIELDS - headers
        if missing:
            raise ValueError("manifest missing required headers: " + ", ".join(sorted(missing)))
        rows = [ManifestRow({k: (v or "") for k, v in row.items()}) for row in reader]
    if not rows:
        raise ValueError("manifest contains no data rows")

    seen: set[str] = set()
    for index, row in enumerate(rows, start=2):
        if not row.folder_name:
            raise ValueError(f"manifest row {index}: empty folder_name")
        if row.folder_name in seen:
            raise ValueError(f"duplicate folder_name: {row.folder_name}")
        seen.add(row.folder_name)
        if output_root is not None:
            safe_child_path(output_root, row.folder_name)
        if not row.url:
            raise ValueError(f"manifest row {index}: empty url")
        if validate_urls and not is_supported_youtube_url(row.url):
            raise ValueError(f"manifest row {index}: unsupported url: {row.url}")
        grade = row.data.get("match_grade", "").strip()
        if grade not in MATCH_GRADES:
            raise ValueError(f"manifest row {index}: invalid match_grade: {grade}")
    return rows


def validate_template_headers(path: Path) -> None:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        headers = next(csv.reader(handle), [])
    if headers != FIELDS:
        raise ValueError(f"manifest template headers differ: {headers}")
