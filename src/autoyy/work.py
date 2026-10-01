from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .batch import stage_plan
from .observability import record_event
from .scheduler import DEFAULT_STAGE_LIMITS, scheduler_plan
from .state import STAGES

LEASE_SCHEMA = 1
MIN_LEASE_SECONDS = 60
MAX_LEASE_SECONDS = 86_400

STAGE_IO = {
    "source": {"inputs": ["manifest row"], "outputs": ["高清源视频.*"]},
    "subtitle": {"inputs": ["高清源视频.*"], "outputs": ["字幕.srt"]},
    "voiceover": {"inputs": ["字幕.srt"], "outputs": ["爆款口播稿.candidate.txt", ".autoyy/voiceover-quality.json"]},
    "publication": {"inputs": ["爆款口播稿.txt"], "outputs": ["发布信息.txt"]},
    "cover": {"inputs": ["发布信息.txt"], "outputs": ["封面-3比4.*", "封面-4比3.*"]},
    "package": {"inputs": ["all stage outputs"], "outputs": []},
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _lease_dir(root: Path) -> Path:
    return root / ".autoyy" / "leases"


def _worker_lock_path(root: Path, worker_id: str) -> Path:
    digest = hashlib.sha256(worker_id.encode("utf-8")).hexdigest()[:24]
    return _lease_dir(root) / f".worker-{digest}.lock"


def _claim_lock_path(root: Path) -> Path:
    return _lease_dir(root) / ".claim.lock"


@contextmanager
def _claim_lock(root: Path) -> Iterator[None]:
    directory = _lease_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = _claim_lock_path(root)
    deadline = time.monotonic() + 10.0
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > 30:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("claim lock timeout") from None
            time.sleep(0.02)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


@contextmanager
def _worker_lock(root: Path, worker_id: str) -> Iterator[None]:
    directory = _lease_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = _worker_lock_path(root, worker_id)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        try:
            stale = time.time() - path.stat().st_mtime > 30
        except OSError:
            stale = False
        if not stale:
            raise RuntimeError(f"worker already has a claim operation in progress: {worker_id}") from exc
        path.unlink(missing_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        path.unlink(missing_ok=True)


def _read_lease(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if data.get("schema_version") == LEASE_SCHEMA else None


def _expired(lease: dict[str, Any], *, now: float | None = None) -> bool:
    return float(lease.get("expires_at_epoch") or 0) <= (time.time() if now is None else now)


def cleanup_expired_leases(root: Path) -> int:
    directory = _lease_dir(root)
    if not directory.is_dir():
        return 0
    removed = 0
    for path in directory.glob("*.json"):
        lease = _read_lease(path)
        if lease is None or _expired(lease):
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def list_leases(root: Path) -> list[dict[str, Any]]:
    cleanup_expired_leases(root)
    directory = _lease_dir(root)
    if not directory.is_dir():
        return []
    leases = []
    for path in sorted(directory.glob("*.json")):
        lease = _read_lease(path)
        if lease and not _expired(lease):
            leases.append(lease)
    return leases


def _lease_path(root: Path, topic: str, stage: str) -> Path:
    digest = hashlib.sha256(f"{topic}\0{stage}".encode()).hexdigest()[:24]
    return _lease_dir(root) / f"{digest}.json"


def _existing_worker_lease(root: Path, worker_id: str) -> dict[str, Any] | None:
    for lease in list_leases(root):
        if lease.get("worker_id") == worker_id:
            return lease
    return None


def claim_work(
    root: Path,
    *,
    worker_id: str,
    stage: str = "voiceover",
    lease_seconds: int = 1800,
    topic: str | None = None,
    capabilities: list[str] | None = None,
    strategy: str = "finish-first",
) -> dict[str, Any]:
    if stage not in {*STAGES, "auto"}:
        raise ValueError(f"unknown stage: {stage}")
    worker_id = worker_id.strip()
    if not worker_id:
        raise ValueError("worker_id is required")
    if not MIN_LEASE_SECONDS <= lease_seconds <= MAX_LEASE_SECONDS:
        raise ValueError(f"lease_seconds must be {MIN_LEASE_SECONDS}-{MAX_LEASE_SECONDS}")
    root = root.resolve()
    with _claim_lock(root), _worker_lock(root, worker_id):
        cleanup_expired_leases(root)
        existing = _existing_worker_lease(root, worker_id)
        if existing:
            if (stage != "auto" and existing.get("stage") != stage) or (topic is not None and existing.get("topic") != topic):
                raise RuntimeError(
                    f"worker already holds {existing.get('stage')} lease for {existing.get('topic')}; "
                    "release it before claiming different work"
                )
            return {"ok": True, "reused": True, "lease": existing}
        active_leases = list_leases(root)
        if stage == "auto":
            schedule = scheduler_plan(root, capabilities=capabilities, active_leases=active_leases, strategy=strategy)
            candidates = schedule["candidates"]
        else:
            active_stage_count = sum(item.get("stage") == stage for item in active_leases)
            if active_stage_count >= DEFAULT_STAGE_LIMITS[stage]:
                return {"ok": False, "reason": "stage_capacity_reached", "stage": stage, "stage_limit": DEFAULT_STAGE_LIMITS[stage]}
            plan = stage_plan(root, stage)
            candidates = [{**row, "stage": stage} for row in plan["results"] if row["runnable"]]
            active_topics = {str(item.get("topic")) for item in active_leases}
            candidates = [row for row in candidates if row["topic"] not in active_topics]
        if topic is not None:
            candidates = [row for row in candidates if row["topic"] == topic]
            if not candidates:
                raise RuntimeError(f"topic is not runnable for {stage}: {topic}")
        for row in candidates:
            selected_stage = str(row.get("stage") or stage)
            token = uuid.uuid4().hex
            now = time.time()
            lease = {
                "schema_version": LEASE_SCHEMA,
                "token": token,
                "worker_id": worker_id,
                "topic": row["topic"],
                "stage": selected_stage,
                "created_at": _now_iso(),
                "expires_at_epoch": now + lease_seconds,
                "lease_seconds": lease_seconds,
                "one_topic_only": True,
                "topic_path": str(root / row["topic"]),
                "required_inputs": STAGE_IO[selected_stage]["inputs"],
                "allowed_outputs": STAGE_IO[selected_stage]["outputs"],
            }
            path = _lease_path(root, row["topic"], selected_stage)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(lease, handle, ensure_ascii=False, indent=2)
            record_event(root, "lease_claim", command="work claim", topic=row["topic"], stage=selected_stage, status="ready", details={"worker_id": worker_id, "lease_seconds": lease_seconds})
            return {"ok": True, "reused": False, "lease": lease}
        return {"ok": False, "reason": "no_runnable_unleased_topic", "stage": stage, "capabilities": capabilities or []}


def heartbeat_work(root: Path, token: str, *, lease_seconds: int = 1800) -> dict[str, Any]:
    if not MIN_LEASE_SECONDS <= lease_seconds <= MAX_LEASE_SECONDS:
        raise ValueError(f"lease_seconds must be {MIN_LEASE_SECONDS}-{MAX_LEASE_SECONDS}")
    for path in _lease_dir(root).glob("*.json") if _lease_dir(root).is_dir() else []:
        lease = _read_lease(path)
        if lease and lease.get("token") == token and not _expired(lease):
            lease["expires_at_epoch"] = time.time() + lease_seconds
            lease["lease_seconds"] = lease_seconds
            temp = path.with_suffix(f".tmp-{uuid.uuid4().hex}")
            temp.write_text(json.dumps(lease, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)
            record_event(root, "lease_heartbeat", command="work heartbeat", topic=str(lease.get("topic", "")), stage=str(lease.get("stage", "")), status="ready", details={"worker_id": lease.get("worker_id", "")})
            return {"ok": True, "lease": lease}
    return {"ok": False, "reason": "lease_not_found_or_expired"}


def release_work(root: Path, token: str) -> dict[str, Any]:
    directory = _lease_dir(root)
    if not directory.is_dir():
        return {"ok": False, "reason": "lease_not_found"}
    for path in directory.glob("*.json"):
        lease = _read_lease(path)
        if lease and lease.get("token") == token:
            path.unlink(missing_ok=True)
            record_event(root, "lease_release", command="work release", topic=str(lease.get("topic", "")), stage=str(lease.get("stage", "")), status="ready", details={"worker_id": lease.get("worker_id", "")})
            return {"ok": True, "released": lease}
    return {"ok": False, "reason": "lease_not_found"}


def work_status(root: Path) -> dict[str, Any]:
    removed = cleanup_expired_leases(root)
    leases = list_leases(root)
    return {"active_count": len(leases), "expired_removed": removed, "leases": leases}


def require_active_lease(
    root: Path, token: str, *, topic: str, stage: str
) -> dict[str, Any]:
    for lease in list_leases(root):
        if lease.get("token") != token:
            continue
        if lease.get("topic") != topic or lease.get("stage") != stage:
            raise RuntimeError("lease does not match requested topic/stage")
        return lease
    raise RuntimeError("active lease not found or expired")
