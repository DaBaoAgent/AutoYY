from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .batch import batch_status
from .observability import recent_events
from .profiling import inventory_root
from .state import load_state
from .work import list_leases

ERROR_HINTS = {
    "LEGACY_TOPIC_NAMES_IGNORED": "Rename topic directories to NN-... before running batch automation.",
    "DOWNLOAD_VIDEO_FAILED": "Check yt-dlp output, URL availability, proxy/cookies authorization, and free disk space.",
    "DOWNLOAD_SUBTITLE_FAILED": "Verify subtitle availability or use local ASR fallback.",
    "ASR_BACKEND_MISSING": "Install the matching ASR optional extra or choose an installed backend.",
    "ASR_TRANSCRIPTION_FAILED": "Inspect the latest topic event for backend/device/model details and retry only that topic.",
    "STATE_CONFLICT": "Retry the operation; state writes use optimistic revision protection.",
    "LEASE_EXPIRED": "Claim the topic again and keep long work alive with work heartbeat.",
}


def diagnose(root: Path, *, event_limit: int = 50) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    inventory = inventory_root(root)
    status = batch_status(root)
    leases = list_leases(root)
    failures = recent_events(root, limit=event_limit, failures_only=True)
    codes = Counter(str(item.get("code") or "UNCLASSIFIED") for item in failures)
    findings: list[dict[str, Any]] = []
    if inventory["legacy_candidate_count"]:
        findings.append({
            "severity": "warning",
            "code": "LEGACY_TOPIC_NAMES_IGNORED",
            "count": inventory["legacy_candidate_count"],
            "hint": ERROR_HINTS["LEGACY_TOPIC_NAMES_IGNORED"],
        })
    blocked = sum(counts.get("blocked", 0) + counts.get("failed", 0) for counts in status["stage_counts"].values())
    if blocked:
        findings.append({"severity": "error", "code": "BLOCKED_OR_FAILED_STAGES", "count": blocked})
    if failures:
        findings.append({"severity": "warning", "code": "RECENT_FAILURE_EVENTS", "count": len(failures)})
    state_error = ""
    try:
        load_state(root, create=False)
    except FileNotFoundError:
        state_error = "state_missing"
    except ValueError as exc:
        state_error = str(exc)
        findings.append({"severity": "error", "code": "STATE_CORRUPT", "message": str(exc)})
    return {
        "root": str(root),
        "ok": not any(item["severity"] == "error" for item in findings),
        "inventory": {
            "recognized_topic_count": inventory["recognized_topic_count"],
            "legacy_candidate_count": inventory["legacy_candidate_count"],
            "legacy_candidates": inventory["legacy_candidates"][:20],
        },
        "batch": {
            "topic_count": status["topic_count"],
            "complete_count": status["complete_count"],
            "stage_counts": status["stage_counts"],
        },
        "active_leases": leases,
        "recent_failure_count": len(failures),
        "failure_codes": dict(codes),
        "recent_failures": failures[-10:],
        "state_note": state_error,
        "findings": findings,
        "error_hints": {code: ERROR_HINTS.get(code, "Inspect the event context and retry only the affected topic.") for code in codes},
    }
