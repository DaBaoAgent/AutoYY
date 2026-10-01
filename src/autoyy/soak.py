from __future__ import annotations

import json
import random
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .scheduler import DEFAULT_STAGE_LIMITS
from .state import STAGES, empty_state, load_state, save_state, set_stage, update_state
from .work import claim_work, list_leases, release_work


def run_control_plane_soak(
    *,
    topics: int = 100,
    operations: int = 1000,
    workers: int = 6,
    fault_rate: float = 0.05,
    crash_rate: float = 0.02,
    seed: int = 1,
) -> dict[str, Any]:
    if topics < 1 or topics > 99:
        raise ValueError("topics must be between 1 and 99")
    if operations < 1 or operations > 100_000:
        raise ValueError("operations must be between 1 and 100000")
    if workers < 1 or workers > 32:
        raise ValueError("workers must be between 1 and 32")
    if not 0 <= fault_rate < 1:
        raise ValueError("fault_rate must be >=0 and <1")
    if not 0 <= crash_rate < 1:
        raise ValueError("crash_rate must be >=0 and <1")
    rng = random.Random(seed)
    started = time.perf_counter()
    injected = 0
    crashes = 0
    successes = 0
    duplicate_claims = 0
    capacity_errors = 0
    capacity_violations = 0
    max_active_by_stage: Counter[str] = Counter()
    executed = 0

    def expire_lease(root: Path, token: str) -> None:
        for path in (root / ".autoyy" / "leases").glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("token") == token:
                payload["expires_at_epoch"] = time.time() - 1
                path.write_text(json.dumps(payload), encoding="utf-8")
                return

    with tempfile.TemporaryDirectory(prefix="autoyy-soak-") as tmp:
        root = Path(tmp)
        state = empty_state()
        for index in range(1, topics + 1):
            name = f"{index:02d}-soak-{index}"
            (root / name).mkdir()
            set_stage(state, name, "source", "pending", fingerprint="")
        save_state(root, state)
        while executed < operations:
            claims: list[dict[str, Any]] = []
            claimed_topics: set[str] = set()
            for worker_index in range(workers):
                result = claim_work(
                    root,
                    worker_id=f"soak-worker-{worker_index}",
                    stage="auto",
                    capabilities=list(STAGES),
                    strategy="repair-first",
                    lease_seconds=60,
                )
                if not result.get("ok"):
                    if result.get("reason") == "stage_capacity_reached":
                        capacity_errors += 1
                    continue
                lease = result["lease"]
                topic = str(lease["topic"])
                if topic in claimed_topics:
                    duplicate_claims += 1
                claimed_topics.add(topic)
                claims.append(lease)
            if not claims:
                break
            active_counts = Counter(str(lease["stage"]) for lease in claims)
            for stage, count in active_counts.items():
                max_active_by_stage[stage] = max(max_active_by_stage[stage], count)
                if count > DEFAULT_STAGE_LIMITS[stage]:
                    capacity_violations += 1
            for lease in claims:
                if executed >= operations:
                    release_work(root, str(lease["token"]))
                    continue
                executed += 1
                if rng.random() < crash_rate:
                    crashes += 1
                    expire_lease(root, str(lease["token"]))
                    continue
                fail = rng.random() < fault_rate
                if fail:
                    injected += 1
                    status = "failed"
                    reason = "SOAK_TRANSIENT_FAULT"
                    fingerprint = "fault"
                else:
                    successes += 1
                    status = "ready"
                    reason = ""
                    fingerprint = f"soak:{executed}"
                def apply(state, *, lease=lease, status=status, fingerprint=fingerprint, reason=reason):
                    set_stage(
                        state, str(lease["topic"]), str(lease["stage"]), status,
                        fingerprint=fingerprint, reason=reason,
                    )
                update_state(root, apply, create=True)
                release_work(root, str(lease["token"]))
        final_state = load_state(root)
        completed = 0
        failed_stages = 0
        for record in final_state["topics"].values():
            statuses = [record["stages"][stage]["status"] for stage in STAGES]
            if all(status == "ready" for status in statuses):
                completed += 1
            failed_stages += sum(status == "failed" for status in statuses)
        dangling = list_leases(root)
    elapsed = time.perf_counter() - started
    return {
        "topics": topics,
        "operations_requested": operations,
        "operations_executed": executed,
        "workers": workers,
        "fault_rate": fault_rate,
        "crash_rate": crash_rate,
        "faults_injected": injected,
        "crashes_injected": crashes,
        "successful_transitions": successes,
        "completed_topics": completed,
        "failed_stages_remaining": failed_stages,
        "duplicate_claims": duplicate_claims,
        "dangling_leases": len(dangling),
        "capacity_errors": capacity_errors,
        "capacity_violations": capacity_violations,
        "max_active_by_stage": dict(max_active_by_stage),
        "elapsed_seconds": round(elapsed, 3),
        "operations_per_second": round(executed / elapsed, 2) if elapsed else None,
        "invariants_ok": duplicate_claims == 0 and not dangling and capacity_violations == 0,
    }
