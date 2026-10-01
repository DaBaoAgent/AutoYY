from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MIN_PRIORITY = -10
MAX_PRIORITY = 10


def policy_path(root: Path) -> Path:
    return root / ".autoyy" / "queue-policy.json"


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_queue_policy(root: Path) -> dict[str, Any]:
    path = policy_path(root)
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "topics": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"queue policy is corrupted: {path}") from exc
    if payload.get("schema_version") != SCHEMA_VERSION or not isinstance(payload.get("topics"), dict):
        raise ValueError("unsupported queue policy schema")
    return payload


def save_queue_policy(root: Path, payload: dict[str, Any]) -> Path:
    path = policy_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}-{uuid.uuid4().hex}")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return path


def set_topic_policy(
    root: Path,
    topic: str,
    *,
    priority: int | None = None,
    due_at: str | None = None,
) -> dict[str, Any]:
    payload = load_queue_policy(root)
    record = payload["topics"].setdefault(topic, {})
    if priority is not None:
        if not MIN_PRIORITY <= priority <= MAX_PRIORITY:
            raise ValueError(f"priority must be {MIN_PRIORITY}..{MAX_PRIORITY}")
        record["priority"] = priority
    if due_at is not None:
        if due_at and _parse_time(due_at) is None:
            raise ValueError("due_at must be ISO-8601")
        if due_at:
            record["due_at"] = due_at
        else:
            record.pop("due_at", None)
    record["updated_at"] = _now().isoformat(timespec="seconds")
    if not record.get("priority") and not record.get("due_at"):
        payload["topics"].pop(topic, None)
    save_queue_policy(root, payload)
    return payload["topics"].get(topic, {})


def clear_topic_policy(root: Path, topic: str) -> bool:
    payload = load_queue_policy(root)
    existed = topic in payload["topics"]
    payload["topics"].pop(topic, None)
    if existed:
        save_queue_policy(root, payload)
    return existed


def candidate_policy_score(
    *,
    stage_updated_at: str | None,
    topic_policy: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _now()
    updated = _parse_time(stage_updated_at)
    wait_minutes = max(0.0, (now - updated).total_seconds() / 60.0) if updated else 0.0
    fairness_bonus = min(5000, int(wait_minutes))
    policy = topic_policy or {}
    priority = int(policy.get("priority") or 0)
    priority_bonus = priority * 50
    due = _parse_time(str(policy.get("due_at") or ""))
    sla_bonus = 0
    sla_state = "none"
    seconds_to_due: float | None = None
    if due is not None:
        seconds_to_due = (due - now).total_seconds()
        if seconds_to_due <= 0:
            sla_bonus, sla_state = 1000, "overdue"
        elif seconds_to_due <= 3600:
            sla_bonus, sla_state = 600, "due_1h"
        elif seconds_to_due <= 6 * 3600:
            sla_bonus, sla_state = 300, "due_6h"
        else:
            sla_state = "scheduled"
    return {
        "priority": priority,
        "priority_bonus": priority_bonus,
        "wait_minutes": round(wait_minutes, 2),
        "fairness_bonus": fairness_bonus,
        "due_at": policy.get("due_at"),
        "seconds_to_due": None if seconds_to_due is None else round(seconds_to_due, 1),
        "sla_state": sla_state,
        "sla_bonus": sla_bonus,
        "score_bonus": priority_bonus + fairness_bonus + sla_bonus,
    }
