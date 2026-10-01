from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .history import adaptive_resource_plan
from .runtime import RuntimeOptions, runtime_tick
from .state import recover_running, update_state
from .work import cleanup_expired_leases

SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def supervisor_path(root: Path) -> Path:
    return root / ".autoyy" / "supervisor.json"


def _lock_path(root: Path) -> Path:
    return root / ".autoyy" / "supervisor.lock"


def _stop_path(root: Path) -> Path:
    return root / ".autoyy" / "supervisor.stop"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def load_supervisor(root: Path) -> dict[str, Any] | None:
    path = supervisor_path(root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"supervisor state is corrupted: {path}") from exc
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported supervisor state schema")
    return payload


def save_supervisor(root: Path, payload: dict[str, Any]) -> Path:
    path = supervisor_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}-{uuid.uuid4().hex}")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return path


@contextmanager
def supervisor_lock(root: Path) -> Iterator[dict[str, Any]]:
    path = _lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            payload = {"pid": os.getpid(), "token": token, "created_at": _now_iso()}
            os.write(fd, json.dumps(payload).encode("utf-8"))
            os.close(fd)
            break
        except FileExistsError:
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
                pid = int(current.get("pid") or 0)
            except (OSError, ValueError, json.JSONDecodeError):
                pid = 0
            if _pid_alive(pid):
                raise RuntimeError(f"supervisor already running with pid {pid}") from None
            path.unlink(missing_ok=True)
    try:
        yield {"pid": os.getpid(), "token": token}
    finally:
        try:
            current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            current = {}
        if current.get("token") == token:
            path.unlink(missing_ok=True)


def supervisor_status(root: Path) -> dict[str, Any]:
    state = load_supervisor(root)
    lock = None
    path = _lock_path(root)
    if path.is_file():
        try:
            lock = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            lock = {"corrupted": True}
    if state is None:
        return {"configured": False, "running": False, "state": None, "lock": lock}
    pid = int((lock or {}).get("pid") or 0)
    return {
        "configured": True,
        "running": bool(lock and _pid_alive(pid)),
        "stop_requested": _stop_path(root).exists() or bool(state.get("stop_requested")),
        "state": state,
        "lock": lock,
    }


def request_supervisor_stop(root: Path) -> dict[str, Any]:
    state = load_supervisor(root)
    if state is None:
        return {"ok": False, "reason": "supervisor_not_initialized"}
    state["stop_requested"] = True
    state["updated_at"] = _now_iso()
    save_supervisor(root, state)
    marker = _stop_path(root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(_now_iso(), encoding="utf-8")
    return {"ok": True, "session_id": state.get("session_id"), "status": state.get("status")}


def _retry_delay(attempt: int, base: float, maximum: float) -> float:
    return min(maximum, base * (2 ** max(0, attempt - 1)))


def _safe_config(options: RuntimeOptions) -> dict[str, Any]:
    return {
        "root": str(options.root),
        "manifest": str(options.manifest) if options.manifest else None,
        "strategy": options.strategy,
        "allow_legacy_ignored": options.allow_legacy_ignored,
        "download_workers": options.download_workers,
        "asr_workers": options.asr_workers,
        "asr_device": options.asr_device,
        "asr_model_size": options.asr_model_size,
        "rate_limit": options.rate_limit,
        "max_command_attempts": options.max_command_attempts,
    }


def _new_session(root: Path, options: RuntimeOptions, previous: dict[str, Any] | None) -> dict[str, Any]:
    resumable = bool(previous and previous.get("status") not in {"complete", "stopped"})
    generation = int((previous or {}).get("generation") or 0) + 1
    resume_count = int((previous or {}).get("resume_count") or 0) + (1 if resumable else 0)
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": f"supervisor-{uuid.uuid4().hex[:12]}",
        "generation": generation,
        "resume_count": resume_count,
        "resumed_from": (previous or {}).get("session_id") if resumable else None,
        "pid": os.getpid(),
        "status": "starting",
        "started_at": _now_iso(),
        "heartbeat_at": _now_iso(),
        "updated_at": _now_iso(),
        "cycles": 0,
        "stop_requested": False,
        "consecutive_idle": 0,
        "retry_ledger": dict((previous or {}).get("retry_ledger") or {}),
        "config": _safe_config(options),
        "last_code": None,
        "last_result": None,
        "tuning": None,
    }


def _apply_adaptive_workers(options: RuntimeOptions, tuning: dict[str, Any]) -> RuntimeOptions:
    recommended = tuning.get("recommended", {})
    return replace(
        options,
        download_workers=options.download_workers or int(recommended.get("download_workers") or 1),
        asr_workers=options.asr_workers or int(recommended.get("asr_workers") or 1),
    )


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    candidate = result.get("candidate") or {}
    action = result.get("action") or {}
    return {
        "status": result.get("status"),
        "topic": candidate.get("topic"),
        "stage": candidate.get("stage") or action.get("stage"),
        "action_status": action.get("status"),
        "error_code": action.get("error_code"),
        "next": result.get("next"),
    }



def _daemon_command(
    options: RuntimeOptions,
    *,
    interval_seconds: float,
    stop_when_idle: bool,
    idle_cycles: int,
    retry_base_seconds: float,
    retry_max_seconds: float,
    retune_every: int,
) -> list[str]:
    if options.proxy or options.cookies_from_browser:
        raise ValueError("background supervisor start refuses proxy/cookie parameters on the process command line")
    command = [
        sys.executable, "-m", "autoyy", "supervisor", "run", str(options.root),
        "--interval-seconds", str(interval_seconds),
        "--idle-cycles", str(idle_cycles),
        "--retry-base-seconds", str(retry_base_seconds),
        "--retry-max-seconds", str(retry_max_seconds),
        "--retune-every", str(retune_every),
    ]
    if options.manifest:
        command += ["--manifest", str(options.manifest)]
    if options.yt_dlp != "yt-dlp":
        command += ["--yt-dlp", options.yt_dlp]
    if options.ffmpeg_location:
        command += ["--ffmpeg-location", options.ffmpeg_location]
    if options.use_aria2:
        command.append("--use-aria2")
    if options.js_runtimes:
        command += ["--js-runtimes", options.js_runtimes]
    if options.allow_legacy_ignored:
        command.append("--allow-legacy-ignored")
    if options.strategy != "finish-first":
        command += ["--strategy", options.strategy]
    if options.download_workers:
        command += ["--download-workers", str(options.download_workers)]
    if options.asr_workers:
        command += ["--asr-workers", str(options.asr_workers)]
    if options.asr_device:
        command += ["--asr-device", options.asr_device]
    if options.asr_model_size != "medium":
        command += ["--asr-model-size", options.asr_model_size]
    if options.hf_endpoint:
        command += ["--hf-endpoint", options.hf_endpoint]
    if options.rate_limit:
        command += ["--rate-limit", options.rate_limit]
    if options.max_command_attempts != 3:
        command += ["--max-command-attempts", str(options.max_command_attempts)]
    if stop_when_idle:
        command.append("--stop-when-idle")
    return command


def start_supervisor(
    options: RuntimeOptions,
    *,
    interval_seconds: float = 2.0,
    stop_when_idle: bool = False,
    idle_cycles: int = 3,
    retry_base_seconds: float = 5.0,
    retry_max_seconds: float = 300.0,
    retune_every: int = 20,
    wait_seconds: float = 5.0,
) -> dict[str, Any]:
    root = options.root.resolve()
    current = supervisor_status(root)
    if current.get("running"):
        return {"ok": False, "reason": "supervisor_already_running", "status": current}
    command = _daemon_command(
        options, interval_seconds=interval_seconds, stop_when_idle=stop_when_idle,
        idle_cycles=idle_cycles, retry_base_seconds=retry_base_seconds,
        retry_max_seconds=retry_max_seconds, retune_every=retune_every,
    )
    log_path = root / ".autoyy" / "supervisor.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL, "stdout": log, "stderr": log,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    finally:
        log.close()
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while time.monotonic() < deadline:
        status = supervisor_status(root)
        if status.get("running"):
            return {"ok": True, "pid": int((status.get("lock") or {}).get("pid") or process.pid), "launcher_pid": process.pid, "log": str(log_path), "status": status}
        if process.poll() is not None:
            final_status = supervisor_status(root)
            final_state = final_status.get("state") or {}
            if process.returncode == 0 and final_state.get("status") in {"idle", "stopped", "paused", "complete"}:
                return {
                    "ok": True, "pid": process.pid, "log": str(log_path),
                    "status": final_status, "exited": True,
                }
            return {"ok": False, "reason": "supervisor_exited_early", "exit_code": process.returncode, "log": str(log_path)}
        time.sleep(0.1)
    return {"ok": True, "pid": process.pid, "log": str(log_path), "status": supervisor_status(root), "starting": True}

def run_supervisor(
    options: RuntimeOptions,
    *,
    interval_seconds: float = 2.0,
    max_cycles: int = 0,
    stop_when_idle: bool = False,
    idle_cycles: int = 3,
    retry_base_seconds: float = 5.0,
    retry_max_seconds: float = 300.0,
    retune_every: int = 20,
) -> tuple[int, dict[str, Any]]:
    if interval_seconds < 0:
        raise ValueError("interval_seconds must be non-negative")
    if max_cycles < 0:
        raise ValueError("max_cycles must be >= 0")
    if idle_cycles < 1:
        raise ValueError("idle_cycles must be positive")
    if retry_base_seconds < 0 or retry_max_seconds < retry_base_seconds:
        raise ValueError("invalid retry backoff")
    if retune_every < 1:
        raise ValueError("retune_every must be positive")

    root = options.root.resolve()
    previous = load_supervisor(root)
    with supervisor_lock(root):
        _stop_path(root).unlink(missing_ok=True)
        recovered_running = update_state(root, lambda state: recover_running(state), create=True)
        cleanup_expired_leases(root)
        session = _new_session(root, options, previous)
        session["recovered_running_stages"] = sum(
            1
            for topic in recovered_running.get("topics", {}).values()
            for item in topic.get("stages", {}).values()
            if item.get("reason") == "interrupted previous run"
        )
        save_supervisor(root, session)
        code = 0
        try:
            while True:
                latest = load_supervisor(root) or session
                if _stop_path(root).exists() or latest.get("stop_requested"):
                    session["status"] = "stopped"
                    code = 0
                    break
                cycle = int(session.get("cycles") or 0) + 1
                if cycle == 1 or cycle % retune_every == 0 or session.get("tuning") is None:
                    session["tuning"] = adaptive_resource_plan(root)
                tuned = _apply_adaptive_workers(options, session["tuning"])
                now = time.time()
                ledger = session.setdefault("retry_ledger", {})
                skip: set[tuple[str, str]] = set()
                for key, entry in list(ledger.items()):
                    if float(entry.get("next_retry_epoch") or 0) > now:
                        topic, stage = key.split("\0", 1)
                        skip.add((topic, stage))
                    elif int(entry.get("attempts") or 0) <= 0:
                        ledger.pop(key, None)
                tick_code, result = runtime_tick(
                    tuned,
                    run_id=str(session["session_id"]),
                    reconcile=cycle == 1,
                    skip_candidates=skip,
                )
                candidate = result.get("candidate") or {}
                topic = str(candidate.get("topic") or "")
                stage = str(candidate.get("stage") or "")
                key = f"{topic}\0{stage}" if topic and stage else ""
                if key and tick_code == 1:
                    entry = ledger.setdefault(key, {"attempts": 0})
                    attempts = int(entry.get("attempts") or 0) + 1
                    entry.update({
                        "attempts": attempts,
                        "last_error_code": str((result.get("action") or {}).get("error_code") or ""),
                        "last_failed_at": _now_iso(),
                        "next_retry_epoch": now + _retry_delay(attempts, retry_base_seconds, retry_max_seconds),
                    })
                elif key and tick_code == 0:
                    ledger.pop(key, None)

                status = str(result.get("status") or "")
                idle_like = status in {
                    "idle", "waiting_external", "waiting_input", "waiting_active",
                    "blocked_work", "deferred_failures",
                }
                session["consecutive_idle"] = int(session.get("consecutive_idle") or 0) + 1 if idle_like else 0
                session["cycles"] = cycle
                session["heartbeat_at"] = _now_iso()
                session["updated_at"] = _now_iso()
                session["last_code"] = tick_code
                session["last_result"] = _result_summary(result)
                session["pid"] = os.getpid()
                session["status"] = "running" if not idle_like else status
                stop_after_cycle = _stop_path(root).exists()
                if stop_after_cycle:
                    session["stop_requested"] = True
                    session["status"] = "stopped"
                save_supervisor(root, session)

                if stop_after_cycle:
                    code = 0
                    break
                if tick_code == 2:
                    session["status"] = "error"
                    code = 2
                    break
                if stop_when_idle and int(session["consecutive_idle"]) >= idle_cycles:
                    session["status"] = "idle"
                    code = 0
                    break
                if max_cycles and cycle >= max_cycles:
                    session["status"] = "paused"
                    code = 0
                    break
                if interval_seconds:
                    time.sleep(interval_seconds)
        except KeyboardInterrupt:
            session["status"] = "interrupted"
            code = 130
        finally:
            session["heartbeat_at"] = _now_iso()
            session["updated_at"] = _now_iso()
            session["pid"] = None
            save_supervisor(root, session)
            if session.get("status") == "stopped":
                _stop_path(root).unlink(missing_ok=True)
        return code, session
