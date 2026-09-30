from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
STAGES = ["source", "subtitle", "voiceover", "publication", "cover", "package"]
DEPENDENCIES = {
    "source": [],
    "subtitle": ["source"],
    "voiceover": ["subtitle"],
    "publication": ["voiceover"],
    "cover": ["publication"],
    "package": ["source", "subtitle", "voiceover", "publication", "cover"],
}
VALID_STATUS = {"pending", "running", "ready", "blocked", "failed", "stale"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def file_fingerprint(path: Path) -> str:
    if not path.is_file():
        return "missing"
    stat = path.stat()
    digest = hashlib.sha256()
    digest.update(str(stat.st_size).encode())
    digest.update(b":")
    digest.update(str(stat.st_mtime_ns).encode())
    return digest.hexdigest()


def state_path(root: Path) -> Path:
    return root / ".autoyy" / "state.json"


def empty_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "updated_at": now_iso(), "topics": {}}


def load_state(root: Path, *, create: bool = False) -> dict[str, Any]:
    path = state_path(root)
    if not path.exists():
        if create:
            return empty_state()
        raise FileNotFoundError(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"state file is corrupted: {path}") from exc
    if data.get("schema_version") != SCHEMA_VERSION or not isinstance(data.get("topics"), dict):
        raise ValueError("unsupported state schema")
    return data


def save_state(root: Path, state: dict[str, Any]) -> Path:
    state["updated_at"] = now_iso()
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def ensure_topic(state: dict[str, Any], topic: str) -> dict[str, Any]:
    topics = state.setdefault("topics", {})
    record = topics.setdefault(topic, {"stages": {}, "approved": {}})
    stages = record.setdefault("stages", {})
    for stage in STAGES:
        stages.setdefault(stage, {"status": "pending", "fingerprint": "", "reason": "", "updated_at": now_iso()})
    return record


def set_stage(
    state: dict[str, Any],
    topic: str,
    stage: str,
    status: str,
    *,
    fingerprint: str = "",
    reason: str = "",
    outputs: list[str] | None = None,
) -> None:
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    if status not in VALID_STATUS:
        raise ValueError(f"invalid status: {status}")
    record = ensure_topic(state, topic)
    current = record["stages"][stage]
    old_fingerprint = current.get("fingerprint", "")
    if fingerprint and old_fingerprint and fingerprint != old_fingerprint:
        approval = record.setdefault("approved", {}).get(stage)
        if isinstance(approval, dict):
            approval["approved"] = False
            approval["reason"] = f"{stage} fingerprint changed"
            approval["updated_at"] = now_iso()
    current.update({
        "status": status,
        "fingerprint": fingerprint,
        "reason": reason,
        "outputs": outputs or current.get("outputs", []),
        "updated_at": now_iso(),
    })
    if fingerprint and old_fingerprint and fingerprint != old_fingerprint:
        mark_dependents_stale(state, topic, stage, reason=f"upstream {stage} changed")


def mark_dependents_stale(state: dict[str, Any], topic: str, changed_stage: str, *, reason: str) -> None:
    record = ensure_topic(state, topic)
    stale = {changed_stage}
    changed = True
    while changed:
        changed = False
        for stage, deps in DEPENDENCIES.items():
            if stage in stale:
                continue
            if any(dep in stale for dep in deps):
                stale.add(stage)
                changed = True
    stale.discard(changed_stage)
    for stage in stale:
        item = record["stages"][stage]
        if item.get("status") == "ready":
            item["status"] = "stale"
            item["reason"] = reason
            item["updated_at"] = now_iso()
        approval = record.setdefault("approved", {}).get(stage)
        if isinstance(approval, dict) and approval.get("approved"):
            approval["approved"] = False
            approval["reason"] = reason
            approval["updated_at"] = now_iso()


def set_approved(
    state: dict[str, Any],
    topic: str,
    stage: str,
    approved: bool = True,
    *,
    reason: str = "",
) -> None:
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    record = ensure_topic(state, topic)
    stage_record = record["stages"][stage]
    if approved and stage_record.get("status") != "ready":
        raise ValueError(f"cannot approve {stage}: stage is not ready")
    record.setdefault("approved", {})[stage] = {
        "approved": approved,
        "fingerprint": stage_record.get("fingerprint", ""),
        "reason": reason,
        "updated_at": now_iso(),
    }


def is_approved(state: dict[str, Any], topic: str, stage: str) -> bool:
    record = ensure_topic(state, topic)
    approval = record.setdefault("approved", {}).get(stage) or {}
    stage_record = record["stages"][stage]
    return (
        approval.get("approved") is True
        and stage_record.get("status") == "ready"
        and approval.get("fingerprint") == stage_record.get("fingerprint")
    )


def force_stage(state: dict[str, Any], topic: str, stage: str, *, reason: str = "forced rerun") -> None:
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    record = ensure_topic(state, topic)
    item = record["stages"][stage]
    item["status"] = "pending"
    item["reason"] = reason
    item["updated_at"] = now_iso()
    approval = record.setdefault("approved", {}).get(stage)
    if isinstance(approval, dict):
        approval["approved"] = False
        approval["reason"] = reason
        approval["updated_at"] = now_iso()
    mark_dependents_stale(state, topic, stage, reason=reason)


def recover_running(state: dict[str, Any]) -> int:
    count = 0
    for topic in state.get("topics", {}).values():
        for item in topic.get("stages", {}).values():
            if item.get("status") == "running":
                item["status"] = "failed"
                item["reason"] = "interrupted previous run"
                item["updated_at"] = now_iso()
                count += 1
    return count
