from __future__ import annotations

import struct
from pathlib import Path

from .media import find_primary_subtitle, find_primary_video, probe_media
from .publication import validate_publication_file
from .subtitles import validate_srt
from .voiceover import validate_voiceover

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


def image_size(path: Path) -> tuple[int, int] | None:
    with path.open("rb") as handle:
        head = handle.read(32)
        if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
            return struct.unpack(">II", head[16:24])
        if head[:2] != b"\xff\xd8":
            try:
                from PIL import Image
                with Image.open(path) as image:
                    return image.size
            except (ImportError, OSError):
                return None
        handle.seek(2)
        while True:
            marker_start = handle.read(1)
            if not marker_start:
                return None
            if marker_start != b"\xff":
                continue
            marker = handle.read(1)
            while marker == b"\xff":
                marker = handle.read(1)
            if marker in {b"\xd8", b"\xd9"}:
                continue
            raw_len = handle.read(2)
            if len(raw_len) != 2:
                return None
            length = struct.unpack(">H", raw_len)[0]
            if marker and marker[0] in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                payload = handle.read(5)
                if len(payload) != 5:
                    return None
                height, width = struct.unpack(">HH", payload[1:5])
                return width, height
            handle.seek(length - 2, 1)


def find_cover(folder: Path, stem: str) -> Path | None:
    for ext in IMAGE_EXTENSIONS:
        path = folder / f"{stem}{ext}"
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def validate_topic(folder: Path, *, require_quality: bool = True, ffprobe: str | None = None) -> dict:
    issues: list[str] = []
    video = find_primary_video(folder)
    subtitle = find_primary_subtitle(folder)
    if video is None:
        issues.append("primary video missing/empty")
    if subtitle is None:
        issues.append("subtitle missing/empty")
    duration = None
    if video is not None and ffprobe:
        try:
            duration = probe_media(video, ffprobe)["duration"]
        except RuntimeError as exc:
            issues.append(f"video unreadable: {exc}")
    subtitle_validation = None
    if subtitle is not None:
        subtitle_validation = validate_srt(subtitle, video_duration=duration)
        if not subtitle_validation["valid"]:
            issues.extend(f"subtitle: {item}" for item in subtitle_validation["issues"])
    publication = validate_publication_file(folder / "发布信息.txt")
    if not publication.get("valid"):
        issues.extend(f"publication: {item}" for item in publication.get("issues", []))
    voiceover = validate_voiceover(folder, require_quality=require_quality)
    if not voiceover.get("valid"):
        issues.extend(f"voiceover: {item}" for item in voiceover.get("issues", []))
    covers: dict[str, dict | None] = {}
    for stem, expected in (("封面-3比4", 3 / 4), ("封面-4比3", 4 / 3)):
        cover = find_cover(folder, stem)
        if cover is None:
            issues.append(f"{stem} missing/empty")
            covers[stem] = None
            continue
        size = image_size(cover)
        if size is None:
            issues.append(f"{cover.name} unreadable")
            covers[stem] = None
            continue
        width, height = size
        ratio = width / height
        ratio_ok = abs(ratio - expected) <= 0.01
        if not ratio_ok:
            issues.append(f"{cover.name} ratio {ratio:.4f}, expected {expected:.4f}")
        covers[stem] = {"file": cover.name, "width": width, "height": height, "ratio_ok": ratio_ok}
    return {
        "folder": folder.name,
        "complete": not issues,
        "issues": issues,
        "video": video.name if video else None,
        "subtitle": subtitle.name if subtitle else None,
        "subtitle_validation": subtitle_validation,
        "publication": publication,
        "voiceover": voiceover,
        "covers": covers,
    }


def validate_root(root: Path, *, allow_empty: bool = False, expected_count: int | None = None, require_quality: bool = True, ffprobe: str | None = None) -> dict:
    folders = sorted(p for p in root.iterdir() if p.is_dir() and p.name[:2].isdigit() and p.name[2:3] == "-")
    if not folders and not allow_empty:
        return {"valid": False, "topic_count": 0, "complete_count": 0, "incomplete_count": 0, "issues": ["no topic directories"], "results": []}
    if expected_count is not None and len(folders) != expected_count:
        return {"valid": False, "topic_count": len(folders), "complete_count": 0, "incomplete_count": len(folders), "issues": [f"topic count {len(folders)}, expected {expected_count}"], "results": []}
    results = [validate_topic(folder, require_quality=require_quality, ffprobe=ffprobe) for folder in folders]
    incomplete = sum(not item["complete"] for item in results)
    return {
        "valid": incomplete == 0,
        "topic_count": len(results),
        "complete_count": len(results) - incomplete,
        "incomplete_count": incomplete,
        "issues": [],
        "results": results,
    }
