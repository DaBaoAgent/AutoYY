from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVENT_SCHEMA = 1
MAX_EVENTS_BYTES = 10 * 1024 * 1024
TAIL_READ_BYTES = 4 * 1024 * 1024


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def events_path(root: Path) -> Path:
    return root / ".autoyy" / "events.jsonl"


def _lock_path(root: Path) -> Path:
    return root / ".autoyy" / "events.lock"


@contextmanager
def _event_lock(root: Path, timeout: float = 2.0) -> Iterator[None]:
    path = _lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > 15:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                yield
                return
            time.sleep(0.02)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def record_event(
    root: Path,
    event: str,
    *,
    run_id: str = "",
    command: str = "",
    topic: str = "",
    stage: str = "",
    status: str = "",
    code: str = "",
    message: str = "",
    elapsed_ms: float | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schema_version": EVENT_SCHEMA,
        "ts": now_iso(),
        "event": event,
        "run_id": run_id,
        "command": command,
        "topic": topic,
        "stage": stage,
        "status": status,
        "code": code,
        "message": message,
    }
    if elapsed_ms is not None:
        payload["elapsed_ms"] = round(float(elapsed_ms), 3)
    if details:
        payload["details"] = details
    try:
        path = events_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _event_lock(root):
            if path.is_file() and path.stat().st_size >= MAX_EVENTS_BYTES:
                rotated = path.with_name("events.1.jsonl")
                rotated.unlink(missing_ok=True)
                os.replace(path, rotated)
            with path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(line)
    except OSError:
        return


def recent_events(root: Path, *, limit: int = 100, failures_only: bool = False) -> list[dict[str, Any]]:
    path = events_path(root)
    if not path.is_file() or limit <= 0:
        return []
    try:
        with path.open("rb") as handle:
            size = path.stat().st_size
            handle.seek(max(0, size - TAIL_READ_BYTES))
            data = handle.read().decode("utf-8", errors="ignore")
        lines = data.splitlines()
        if size > TAIL_READ_BYTES and lines:
            lines = lines[1:]
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if failures_only and item.get("status") not in {"failed", "blocked", "error"}:
            continue
        rows.append(item)
        if len(rows) >= limit:
            break
    rows.reverse()
    return rows


class RunRecorder:
    def __init__(self, root: Path, command: str, *, run_id: str | None = None) -> None:
        self.root = root
        self.command = command
        self.run_id = run_id or new_run_id(command.replace(" ", "-"))
        self.started = time.perf_counter()
        record_event(root, "run_start", run_id=self.run_id, command=command, status="running")

    def event(self, event: str, **kwargs: Any) -> None:
        record_event(self.root, event, run_id=self.run_id, command=self.command, **kwargs)

    def finish(self, *, status: str, code: str = "", message: str = "", details: dict[str, Any] | None = None) -> None:
        elapsed = (time.perf_counter() - self.started) * 1000.0
        record_event(
            self.root, "run_finish", run_id=self.run_id, command=self.command,
            status=status, code=code, message=message, elapsed_ms=elapsed, details=details,
        )
