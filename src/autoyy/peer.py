from __future__ import annotations

import csv
import math
import os
from datetime import date
from pathlib import Path
from statistics import mean, median

FIELDS = ["platform", "title", "hashtags", "views", "likes", "comments", "topic_category", "hook_pattern", "published_at", "source"]
PATTERNS = {"数字冲击", "对比反差", "信息缺口设问", "后果威胁", "身份反转", "反常识", "悬念事件", "情感共鸣"}


def number(value: object) -> float:
    try:
        out = float(str(value or 0).replace(",", ""))
    except (TypeError, ValueError):
        raise ValueError(f"invalid numeric value: {value}") from None
    if out < 0:
        raise ValueError("metrics must be non-negative")
    return out


def load_library(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or any(field not in reader.fieldnames for field in FIELDS):
            raise ValueError("peer library has invalid headers")
        return [dict(row) for row in reader]


def validate_row(row: dict[str, str]) -> list[str]:
    issues: list[str] = []
    if not (row.get("title") or "").strip():
        issues.append("title missing")
    for field in ("views", "likes", "comments"):
        try:
            number(row.get(field, "0"))
        except ValueError as exc:
            issues.append(f"{field}: {exc}")
    published = (row.get("published_at") or "").strip()
    if published:
        try:
            date.fromisoformat(published)
        except ValueError:
            issues.append("published_at must be YYYY-MM-DD")
    pattern = (row.get("hook_pattern") or "").strip()
    if pattern and pattern not in PATTERNS:
        issues.append(f"unknown hook_pattern: {pattern}")
    return issues


def save_library(path: Path, rows: list[dict[str, str]], *, backup: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        backup_path = path.with_suffix(path.suffix + ".bak")
        backup_path.write_bytes(path.read_bytes())
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lower, upper = math.floor(pos), math.ceil(pos)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - pos) + ordered[upper] * (pos - lower)


def pattern_stats(
    rows: list[dict[str, str]],
    *,
    platform: str | None = None,
    category: str | None = None,
    since: date | None = None,
) -> list[dict]:
    filtered: list[dict[str, str]] = []
    for row in rows:
        if platform and (row.get("platform") or "").strip() != platform:
            continue
        if category and (row.get("topic_category") or "").strip() != category:
            continue
        if since:
            raw_date = (row.get("published_at") or "").strip()
            try:
                if not raw_date or date.fromisoformat(raw_date) < since:
                    continue
            except ValueError:
                continue
        filtered.append(row)
    groups: dict[tuple[str, str], list[float]] = {}
    for row in filtered:
        row_platform = (row.get("platform") or "unknown").strip() or "unknown"
        pattern = (row.get("hook_pattern") or "未分类").strip() or "未分类"
        groups.setdefault((row_platform, pattern), []).append(number(row.get("views")))
    out: list[dict] = []
    for (row_platform, pattern), values in groups.items():
        out.append({
            "platform": row_platform,
            "pattern": pattern,
            "count": len(values),
            "median_views": median(values),
            "mean_views": mean(values),
            "p75_views": _percentile(values, 0.75),
            "peak_views": max(values),
            "confidence": "low" if len(values) < 5 else "medium" if len(values) < 20 else "high",
        })
    return sorted(out, key=lambda item: (item["median_views"], item["count"]), reverse=True)
