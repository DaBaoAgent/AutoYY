from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any

from .observability import recent_events
from .resources import ResourcePlan, detect_resources

FAILURE_STATUS = {"failed", "blocked", "error"}
NETWORK_CODES = {
    "RATE_LIMITED", "NETWORK_TIMEOUT", "NETWORK_TRANSIENT",
    "REMOTE_5XX", "PROCESS_TIMEOUT",
}


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return round(ordered[index], 3)


def stage_history(root: Path, *, limit: int = 5000) -> dict[str, Any]:
    rows = recent_events(root, limit=limit)
    by_stage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("event") != "topic_finish" or not row.get("stage"):
            continue
        by_stage[str(row["stage"])].append(row)
    metrics: dict[str, Any] = {}
    for stage, items in by_stage.items():
        elapsed = [float(item["elapsed_ms"]) for item in items if item.get("elapsed_ms") is not None]
        failures = [item for item in items if str(item.get("status")) in FAILURE_STATUS]
        codes = Counter(str(item.get("code") or "") for item in failures if item.get("code"))
        success_count = len(items) - len(failures)
        total_ms = sum(elapsed)
        metrics[stage] = {
            "samples": len(items),
            "success_count": success_count,
            "failure_count": len(failures),
            "failure_rate": round(len(failures) / len(items), 4) if items else 0.0,
            "p50_ms": round(median(elapsed), 3) if elapsed else None,
            "p95_ms": _percentile(elapsed, 0.95),
            "throughput_per_minute": round(success_count / (total_ms / 60000.0), 3) if total_ms > 0 else None,
            "failure_codes": dict(codes.most_common()),
        }
    return {"event_samples": len(rows), "stages": metrics}


def adaptive_resource_plan(root: Path, base: ResourcePlan | None = None) -> dict[str, Any]:
    base = base or detect_resources()
    history = stage_history(root)
    source = history["stages"].get("source", {})
    subtitle = history["stages"].get("subtitle", {})
    download_workers = base.download_workers
    source_samples = int(source.get("samples") or 0)
    source_codes = source.get("failure_codes") or {}
    network_failures = sum(int(source_codes.get(code, 0)) for code in NETWORK_CODES)
    network_failure_rate = network_failures / source_samples if source_samples else 0.0
    if source_samples >= 10 and network_failure_rate >= 0.20:
        download_workers = max(1, base.download_workers // 2)
    elif source_samples >= 20 and network_failure_rate <= 0.02:
        download_workers = min(8, base.download_workers + 1)

    asr_workers = base.asr_workers
    subtitle_samples = int(subtitle.get("samples") or 0)
    subtitle_failure_rate = float(subtitle.get("failure_rate") or 0.0)
    if subtitle_samples >= 10 and subtitle_failure_rate >= 0.15:
        asr_workers = 1
    elif (
        subtitle_samples >= 20
        and subtitle_failure_rate <= 0.02
        and base.asr_device == "cuda"
        and (base.gpu_memory_gb or 0) >= 12
    ):
        asr_workers = min(3, max(base.asr_workers, 2))

    return {
        "base": {
            "download_workers": base.download_workers,
            "asr_workers": base.asr_workers,
            "package_workers": base.package_workers,
        },
        "recommended": {
            "download_workers": download_workers,
            "asr_workers": asr_workers,
            "package_workers": base.package_workers,
        },
        "signals": {
            "network_failure_rate": round(network_failure_rate, 4),
            "subtitle_failure_rate": round(subtitle_failure_rate, 4),
        },
        "history": history,
    }
