from __future__ import annotations

import re
import statistics
import time
from pathlib import Path
from typing import Any

from .batch import batch_status
from .observability import recent_events
from .paths import TOPIC_RE, discover_topics

LEGACY_TOPIC_RE = re.compile(r"^\d{2}(?:\s|_)")


def inventory_root(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise FileNotFoundError(root)
    directories = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.casefold())
    recognized = [p for p in directories if TOPIC_RE.match(p.name)]
    legacy = [p for p in directories if not TOPIC_RE.match(p.name) and LEGACY_TOPIC_RE.match(p.name)]
    recognized_names = {p.name for p in recognized}
    file_count = 0
    total_bytes = 0
    topic_file_count = 0
    topic_bytes = 0
    for directory in directories:
        for path in directory.iterdir():
            if not path.is_file():
                continue
            file_count += 1
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            total_bytes += size
            if directory.name in recognized_names:
                topic_file_count += 1
                topic_bytes += size
    return {
        "directory_count": len(directories),
        "recognized_topic_count": len(recognized),
        "recognized_topics": [p.name for p in recognized],
        "legacy_candidate_count": len(legacy),
        "legacy_candidates": [p.name for p in legacy],
        "other_directory_count": len(directories) - len(recognized) - len(legacy),
        "file_count": file_count,
        "total_bytes": total_bytes,
        "recognized_topic_file_count": topic_file_count,
        "recognized_topic_bytes": topic_bytes,
    }


def _timed(label: str, func, timings: dict[str, float]):
    started = time.perf_counter()
    value = func()
    timings[label] = round((time.perf_counter() - started) * 1000.0, 3)
    return value


def _event_throughput(root: Path) -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    for item in recent_events(root, limit=2000):
        if item.get("event") != "topic_finish" or item.get("elapsed_ms") is None:
            continue
        key = str(item.get("command") or item.get("stage") or "unknown")
        groups.setdefault(key, []).append(float(item["elapsed_ms"]))
    result: dict[str, Any] = {}
    for key, values in groups.items():
        ordered = sorted(values)
        p95_index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * 0.95))))
        result[key] = {
            "count": len(values),
            "median_ms": round(statistics.median(values), 3),
            "p95_ms": round(ordered[p95_index], 3),
            "max_ms": round(max(values), 3),
        }
    return result


def profile_workload(root: Path) -> dict[str, Any]:
    root = root.resolve()
    timings: dict[str, float] = {}
    inventory = _timed("inventory_ms", lambda: inventory_root(root), timings)
    topics = _timed("discover_topics_ms", lambda: discover_topics(root), timings)
    status = _timed("batch_status_ms", lambda: batch_status(root), timings)
    warnings: list[dict[str, Any]] = []
    if inventory["legacy_candidate_count"]:
        warnings.append({
            "code": "LEGACY_TOPIC_NAMES_IGNORED",
            "message": f"{inventory['legacy_candidate_count']} topic-like directories do not match ^NN- and are excluded",
            "hint": "Rename them to NN-... or migrate them deliberately before batch execution.",
            "examples": inventory["legacy_candidates"][:10],
        })
    if len(topics) != inventory["recognized_topic_count"]:
        warnings.append({
            "code": "INVENTORY_COUNT_MISMATCH",
            "message": "topic discovery count differs from inventory count",
        })
    return {
        "root": str(root),
        "inventory": inventory,
        "batch": {
            "topic_count": status["topic_count"],
            "complete_count": status["complete_count"],
            "stage_counts": status["stage_counts"],
        },
        "timings_ms": timings,
        "historical_topic_latency": _event_throughput(root),
        "warnings": warnings,
    }
