from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2}),(\d{3})$")


@dataclass(slots=True)
class Cue:
    index: int
    start: float
    end: float
    text: str


def _seconds(parts: tuple[str, ...]) -> float:
    h, m, s, ms = map(int, parts)
    return h * 3600 + m * 60 + s + ms / 1000.0


def parse_srt(path: Path) -> tuple[list[Cue], list[str]]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    if not raw:
        return [], ["empty subtitle file"]
    blocks = re.split(r"\r?\n\s*\r?\n", raw)
    cues: list[Cue] = []
    issues: list[str] = []
    expected = 1
    for block_no, block in enumerate(blocks, 1):
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            issues.append(f"block {block_no}: incomplete")
            continue
        try:
            index = int(lines[0].strip())
        except ValueError:
            issues.append(f"block {block_no}: invalid index")
            continue
        if index != expected:
            issues.append(f"block {block_no}: index {index}, expected {expected}")
        expected = index + 1
        match = TIME_RE.match(lines[1].strip())
        if not match:
            issues.append(f"block {block_no}: invalid timestamp")
            continue
        groups = match.groups()
        start, end = _seconds(groups[:4]), _seconds(groups[4:])
        if end <= start:
            issues.append(f"block {block_no}: end must be after start")
        text = " ".join(lines[2:]).strip()
        if not text:
            issues.append(f"block {block_no}: empty text")
        cues.append(Cue(index, start, end, text))
    return cues, issues


def validate_cues(cues: list[Cue], *, overlap_tolerance: float = 0.15) -> list[str]:
    issues: list[str] = []
    for idx, cue in enumerate(cues):
        if idx and cue.start < cues[idx - 1].start:
            issues.append(f"cue {cue.index}: start time decreases")
        if idx and cue.start < cues[idx - 1].end - overlap_tolerance:
            overlap = cues[idx - 1].end - cue.start
            issues.append(f"cue {cue.index}: overlap {overlap:.2f}s")
    return issues


def coverage_metrics(cues: list[Cue], video_duration: float | None) -> dict[str, float | None]:
    if not cues:
        return {"first_start": None, "last_end": None, "coverage_pct": None, "max_gap": None}
    max_gap = 0.0
    for prev, current in zip(cues, cues[1:], strict=False):
        max_gap = max(max_gap, max(0.0, current.start - prev.end))
    coverage = None
    if video_duration and video_duration > 0:
        coverage = cues[-1].end / video_duration * 100.0
    return {
        "first_start": round(cues[0].start, 3),
        "last_end": round(cues[-1].end, 3),
        "coverage_pct": round(coverage, 2) if coverage is not None else None,
        "max_gap": round(max_gap, 3),
    }


def validate_srt(
    path: Path,
    *,
    video_duration: float | None = None,
    max_drift_pct: float = 5.0,
    overlap_tolerance: float = 0.15,
) -> dict:
    cues, issues = parse_srt(path)
    issues.extend(validate_cues(cues, overlap_tolerance=overlap_tolerance))
    metrics = coverage_metrics(cues, video_duration)
    if video_duration and cues:
        drift = abs(cues[-1].end - video_duration) / video_duration * 100.0
        if drift > max_drift_pct:
            issues.append(f"subtitle/video drift {drift:.2f}% exceeds {max_drift_pct:.2f}%")
    return {"valid": not issues, "issues": issues, "cue_count": len(cues), **metrics}


def srt_to_text(path: Path) -> str:
    cues, _ = parse_srt(path)
    return "\n".join(cue.text for cue in cues)
