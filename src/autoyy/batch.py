from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .paths import discover_topics
from .state import DEPENDENCIES, STAGES, empty_state, ensure_topic, load_state

RUNNABLE_STATUS = {"pending", "failed", "stale"}


def _read_state(root: Path) -> dict[str, Any]:
    try:
        return load_state(root)
    except FileNotFoundError:
        return empty_state()


def _stage_ready(record: dict[str, Any], stage: str) -> bool:
    return record["stages"][stage].get("status") == "ready"


def _dependency_blockers(record: dict[str, Any], stage: str) -> list[str]:
    return [dep for dep in DEPENDENCIES[stage] if not _stage_ready(record, dep)]


def stage_plan(root: Path, stage: str) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    state = _read_state(root)
    rows: list[dict[str, Any]] = []
    for topic in discover_topics(root):
        record = ensure_topic(state, topic.name)
        item = record["stages"][stage]
        blockers = _dependency_blockers(record, stage)
        status = str(item.get("status") or "pending")
        runnable = not blockers and status in RUNNABLE_STATUS
        rows.append({
            "topic": topic.name,
            "stage": stage,
            "status": status,
            "runnable": runnable,
            "blockers": blockers,
            "reason": str(item.get("reason") or ""),
        })
    return {
        "stage": stage,
        "topic_count": len(rows),
        "runnable_count": sum(row["runnable"] for row in rows),
        "ready_count": sum(row["status"] == "ready" for row in rows),
        "blocked_count": sum(bool(row["blockers"]) or row["status"] == "blocked" for row in rows),
        "results": rows,
    }


def batch_status(root: Path) -> dict[str, Any]:
    state = _read_state(root)
    topics = discover_topics(root)
    rows: list[dict[str, Any]] = []
    counters = {stage: Counter() for stage in STAGES}
    for topic in topics:
        record = ensure_topic(state, topic.name)
        stage_status = {
            stage: str(record["stages"][stage].get("status") or "pending")
            for stage in STAGES
        }
        for stage, status in stage_status.items():
            counters[stage][status] += 1
        next_stage = None
        blockers: list[str] = []
        for stage in STAGES:
            status = stage_status[stage]
            if status == "ready":
                continue
            blockers = _dependency_blockers(record, stage)
            if not blockers and status in RUNNABLE_STATUS:
                next_stage = stage
            break
        rows.append({
            "topic": topic.name,
            "stages": stage_status,
            "next_stage": next_stage,
            "blockers": blockers,
        })
    return {
        "topic_count": len(rows),
        "complete_count": sum(row["stages"]["package"] == "ready" for row in rows),
        "stage_counts": {stage: dict(counter) for stage, counter in counters.items()},
        "results": rows,
    }
