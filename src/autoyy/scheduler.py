from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .batch import RUNNABLE_STATUS
from .paths import discover_topics
from .profiling import inventory_root
from .state import DEPENDENCIES, STAGES, empty_state, ensure_topic, load_state

DEFAULT_STAGE_LIMITS = {
    "source": 4,
    "subtitle": 1,
    "voiceover": 4,
    "publication": 6,
    "cover": 2,
    "package": 6,
}
STATUS_BONUS = {"stale": 30, "failed": 20, "pending": 10}
STRATEGIES = {"finish-first", "repair-first", "source-first"}


def parse_capabilities(value: str | None) -> list[str]:
    if not value:
        return list(STAGES)
    stages = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [stage for stage in stages if stage not in STAGES]
    if unknown:
        raise ValueError("unknown capabilities: " + ", ".join(unknown))
    return list(dict.fromkeys(stages))


def _priority(stage: str, status: str, strategy: str) -> int:
    stage_rank = STAGES.index(stage)
    status_rank = STATUS_BONUS.get(status, 0)
    if strategy == "finish-first":
        return stage_rank * 100 + status_rank
    if strategy == "repair-first":
        return status_rank * 100 + stage_rank
    return (len(STAGES) - stage_rank) * 100 + status_rank


def scheduler_plan(
    root: Path,
    *,
    capabilities: list[str] | None = None,
    active_leases: list[dict[str, Any]] | None = None,
    stage_limits: dict[str, int] | None = None,
    strategy: str = "finish-first",
) -> dict[str, Any]:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown scheduler strategy: {strategy}")
    capabilities = capabilities or list(STAGES)
    limits = {**DEFAULT_STAGE_LIMITS, **(stage_limits or {})}
    leases = active_leases or []
    active_topics = {str(item.get("topic")) for item in leases}
    active_by_stage = Counter(str(item.get("stage")) for item in leases)
    candidates: list[dict[str, Any]] = []
    try:
        state = load_state(root)
    except FileNotFoundError:
        state = empty_state()
    topics = discover_topics(root)
    for topic in topics:
        if topic.name in active_topics:
            continue
        record = ensure_topic(state, topic.name)
        for stage in capabilities:
            stage_limit = max(1, int(limits.get(stage, 1)))
            if active_by_stage[stage] >= stage_limit:
                continue
            item = record["stages"][stage]
            status = str(item.get("status") or "pending")
            blockers = [dep for dep in DEPENDENCIES[stage] if record["stages"][dep].get("status") != "ready"]
            if blockers or status not in RUNNABLE_STATUS:
                continue
            score = _priority(stage, status, strategy)
            candidates.append({
                "topic": topic.name, "stage": stage, "status": status, "score": score,
                "stage_active": active_by_stage[stage], "stage_limit": stage_limit,
                "reason": str(item.get("reason") or ""),
            })
    candidates.sort(key=lambda item: (-item["score"], item["topic"].casefold(), item["stage"]))
    inventory = inventory_root(root)
    warnings = []
    if inventory["legacy_candidate_count"]:
        warnings.append({
            "code": "LEGACY_TOPIC_NAMES_IGNORED",
            "count": inventory["legacy_candidate_count"],
            "examples": inventory["legacy_candidates"][:10],
        })
    return {
        "strategy": strategy,
        "capabilities": capabilities,
        "candidate_count": len(candidates),
        "active_by_stage": dict(active_by_stage),
        "stage_limits": {stage: limits[stage] for stage in capabilities},
        "next": candidates[0] if candidates else None,
        "candidates": candidates,
        "warnings": warnings,
    }
