from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autoyy.history import adaptive_resource_plan
from autoyy.observability import record_event
from autoyy.queue_policy import clear_topic_policy, load_queue_policy, set_topic_policy
from autoyy.runtime import RuntimeOptions
from autoyy.scheduler import scheduler_plan
from autoyy.state import empty_state, save_state, set_stage
from autoyy.supervisor import (
    load_supervisor,
    request_supervisor_stop,
    run_supervisor,
    save_supervisor,
    supervisor_status,
)


def _project(root: Path, names: tuple[str, ...] = ("01-a", "02-b")) -> dict:
    state = empty_state()
    for name in names:
        (root / name).mkdir(parents=True)
        set_stage(state, name, "source", "pending")
    save_state(root, state)
    return state


def test_queue_policy_round_trip(tmp_path: Path) -> None:
    _project(tmp_path)
    due = (datetime.now(UTC) + timedelta(hours=2)).isoformat(timespec="seconds")
    record = set_topic_policy(tmp_path, "01-a", priority=5, due_at=due)
    assert record["priority"] == 5
    assert record["due_at"] == due
    assert load_queue_policy(tmp_path)["topics"]["01-a"]["priority"] == 5
    assert clear_topic_policy(tmp_path, "01-a")
    assert "01-a" not in load_queue_policy(tmp_path)["topics"]
    with pytest.raises(ValueError, match="priority"):
        set_topic_policy(tmp_path, "01-a", priority=11)


def test_scheduler_aging_eventually_beats_new_high_priority(tmp_path: Path) -> None:
    state = _project(tmp_path)
    old = (datetime.now(UTC) - timedelta(days=2)).isoformat(timespec="seconds")
    state["topics"]["01-a"]["stages"]["source"]["updated_at"] = old
    save_state(tmp_path, state)
    set_topic_policy(tmp_path, "02-b", priority=10)
    plan = scheduler_plan(tmp_path, capabilities=["source"])
    assert plan["next"]["topic"] == "01-a"
    assert plan["next"]["fairness_bonus"] > 1000


def test_scheduler_overdue_sla_gets_urgency_bonus(tmp_path: Path) -> None:
    _project(tmp_path)
    overdue = (datetime.now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds")
    set_topic_policy(tmp_path, "02-b", due_at=overdue)
    plan = scheduler_plan(tmp_path, capabilities=["source"])
    second = next(item for item in plan["candidates"] if item["topic"] == "02-b")
    assert second["sla_state"] == "overdue"
    assert second["sla_bonus"] == 1000
    assert plan["next"]["topic"] == "02-b"


def test_history_reduces_download_workers_after_network_failures(tmp_path: Path) -> None:
    _project(tmp_path, ("01-a",))
    for index in range(10):
        record_event(
            tmp_path,
            "topic_finish",
            topic="01-a",
            stage="source",
            status="failed" if index < 4 else "complete",
            code="RATE_LIMITED" if index < 4 else "",
            elapsed_ms=1000,
        )
    plan = adaptive_resource_plan(tmp_path)
    assert plan["signals"]["network_failure_rate"] == 0.4
    assert plan["recommended"]["download_workers"] <= plan["base"]["download_workers"]


def test_supervisor_resumes_persisted_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, ("01-a",))
    previous = {
        "schema_version": 1,
        "session_id": "old-session",
        "generation": 1,
        "resume_count": 0,
        "status": "interrupted",
        "retry_ledger": {},
    }
    save_supervisor(tmp_path, previous)
    monkeypatch.setattr(
        "autoyy.supervisor.runtime_tick",
        lambda *_a, **_k: (1, {"status": "waiting_external", "candidate": None, "action": {}}),
    )
    code, state = run_supervisor(RuntimeOptions(tmp_path), interval_seconds=0, max_cycles=1)
    assert code == 0
    assert state["status"] == "paused"
    assert state["generation"] == 2
    assert state["resume_count"] == 1
    assert state["resumed_from"] == "old-session"
    assert load_supervisor(tmp_path)["session_id"] == state["session_id"]


def test_supervisor_stop_and_status(tmp_path: Path) -> None:
    _project(tmp_path, ("01-a",))
    save_supervisor(tmp_path, {
        "schema_version": 1,
        "session_id": "s1",
        "generation": 1,
        "resume_count": 0,
        "status": "running",
        "retry_ledger": {},
    })
    result = request_supervisor_stop(tmp_path)
    assert result["ok"]
    status = supervisor_status(tmp_path)
    assert status["configured"]
    assert status["state"]["stop_requested"] is True


def test_supervisor_rejects_live_duplicate_process(tmp_path: Path) -> None:
    _project(tmp_path, ("01-a",))
    lock = tmp_path / ".autoyy" / "supervisor.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "token": "live"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="already running"):
        run_supervisor(RuntimeOptions(tmp_path), interval_seconds=0, max_cycles=1)


def test_supervisor_persists_retry_backoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, ("01-a",))
    result = {
        "status": "failed",
        "candidate": {"topic": "01-a", "stage": "subtitle"},
        "action": {"stage": "subtitle", "status": "failed", "error_code": "ASR_TRANSCRIPTION_FAILED"},
    }
    monkeypatch.setattr("autoyy.supervisor.runtime_tick", lambda *_a, **_k: (1, result))
    code, state = run_supervisor(
        RuntimeOptions(tmp_path), interval_seconds=0, max_cycles=1,
        retry_base_seconds=10, retry_max_seconds=30,
    )
    assert code == 0
    entry = state["retry_ledger"]["01-a\0subtitle"]
    assert entry["attempts"] == 1
    assert entry["next_retry_epoch"] > 0


def test_high_level_cli_surface(tmp_path: Path) -> None:
    from autoyy.cli import main

    _project(tmp_path, ("01-a",))
    assert main([
        "queue", "set", str(tmp_path), "01-a",
        "--priority", "4", "--sla-minutes", "60", "--json",
    ]) == 0
    assert main(["queue", "show", str(tmp_path), "01-a", "--json"]) == 0
    assert main(["history", str(tmp_path), "--json"]) == 0
    assert main(["supervisor", "status", str(tmp_path), "--json"]) == 0
    assert main([
        "supervisor", "run", str(tmp_path),
        "--allow-legacy-ignored", "--interval-seconds", "0",
        "--max-cycles", "1", "--json",
    ]) == 0
    status = supervisor_status(tmp_path)
    assert status["state"]["status"] == "paused"
    assert main(["queue", "clear", str(tmp_path), "01-a", "--json"]) == 0


def test_supervisor_recovers_stale_process_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, ("01-a",))
    lock = tmp_path / ".autoyy" / "supervisor.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 99999999, "token": "dead"}), encoding="utf-8")
    monkeypatch.setattr(
        "autoyy.supervisor.runtime_tick",
        lambda *_a, **_k: (1, {"status": "waiting_external", "candidate": None, "action": {}}),
    )
    code, state = run_supervisor(RuntimeOptions(tmp_path), interval_seconds=0, max_cycles=1)
    assert code == 0
    assert state["status"] == "paused"
    assert not lock.exists()


def test_stop_request_during_cycle_is_not_lost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, ("01-a",))

    def fake_tick(options, **_kwargs):
        request_supervisor_stop(options.root)
        return 1, {"status": "waiting_external", "candidate": None, "action": {}}

    monkeypatch.setattr("autoyy.supervisor.runtime_tick", fake_tick)
    code, state = run_supervisor(RuntimeOptions(tmp_path), interval_seconds=0, max_cycles=5)
    assert code == 0
    assert state["status"] == "stopped"
    assert state["cycles"] == 1
    assert not (tmp_path / ".autoyy" / "supervisor.stop").exists()


def test_scheduler_uses_directory_age_before_state_exists(tmp_path: Path) -> None:
    first = tmp_path / "01-old"
    second = tmp_path / "02-new"
    first.mkdir()
    second.mkdir()
    old_epoch = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(first, (old_epoch, old_epoch))
    plan = scheduler_plan(tmp_path, capabilities=["source"])
    assert plan["next"]["topic"] == "01-old"
    assert plan["next"]["fairness_bonus"] > 1000


def test_supervisor_recovers_interrupted_running_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = empty_state()
    (tmp_path / "01-a").mkdir()
    set_stage(state, "01-a", "source", "running")
    save_state(tmp_path, state)
    monkeypatch.setattr(
        "autoyy.supervisor.runtime_tick",
        lambda *_a, **_k: (1, {"status": "waiting_external", "candidate": None, "action": {}}),
    )
    code, _session = run_supervisor(RuntimeOptions(tmp_path), interval_seconds=0, max_cycles=1)
    assert code == 0
    from autoyy.state import load_state

    source = load_state(tmp_path)["topics"]["01-a"]["stages"]["source"]
    assert source["status"] == "failed"
    assert source["reason"] == "interrupted previous run"


def test_supervisor_applies_history_worker_recommendations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, ("01-a",))
    captured = {}
    monkeypatch.setattr(
        "autoyy.supervisor.adaptive_resource_plan",
        lambda _root: {"recommended": {"download_workers": 3, "asr_workers": 1, "package_workers": 4}},
    )
    def fake_tick(options, **_kwargs):
        captured["download_workers"] = options.download_workers
        return 1, {"status": "waiting_external", "candidate": None, "action": {}}
    monkeypatch.setattr("autoyy.supervisor.runtime_tick", fake_tick)
    code, _state = run_supervisor(RuntimeOptions(tmp_path), interval_seconds=0, max_cycles=1)
    assert code == 0
    assert captured["download_workers"] == 3


def test_pid_alive_detects_current_process() -> None:
    import autoyy.supervisor as supervisor_module

    assert supervisor_module._pid_alive(os.getpid())


def test_daemon_command_refuses_sensitive_process_arguments(tmp_path: Path) -> None:
    import autoyy.supervisor as supervisor_module

    _project(tmp_path, ("01-a",))
    with pytest.raises(ValueError, match="proxy/cookie"):
        supervisor_module._daemon_command(
            RuntimeOptions(tmp_path, proxy="http://secret"),
            interval_seconds=1, stop_when_idle=False, idle_cycles=3,
            retry_base_seconds=1, retry_max_seconds=10, retune_every=20,
        )


def test_queue_cli_rejects_empty_or_invalid_sla(tmp_path: Path) -> None:
    from autoyy.cli import main

    _project(tmp_path, ("01-a",))
    assert main(["queue", "set", str(tmp_path), "01-a", "--json"]) == 2
    assert main(["queue", "set", str(tmp_path), "01-a", "--sla-minutes", "0", "--json"]) == 2
    assert main(["queue", "set", str(tmp_path), "99-missing", "--priority", "1", "--json"]) == 2


def test_voiceover_attest_cli_passes_only_attestation_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoyy.cli as cli_module

    topic = tmp_path / "01-a"
    topic.mkdir()
    captured = {}

    def fake_attest(path, *, issuer, run_id):
        captured.update({"path": path, "issuer": issuer, "run_id": run_id})
        return path / ".autoyy" / "voiceover-quality.json"

    monkeypatch.setattr(cli_module, "attest_quality_file", fake_attest)
    code = cli_module.main([
        "voiceover", "attest", str(topic),
        "--issuer", "verifier", "--run-id", "review-1", "--json",
    ])
    assert code == 0
    assert captured == {
        "path": topic.resolve(), "issuer": "verifier", "run_id": "review-1"
    }


def test_start_supervisor_reports_real_locked_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoyy.supervisor as supervisor_module

    _project(tmp_path, ("01-a",))
    statuses = iter([
        {"configured": False, "running": False, "state": None, "lock": None},
        {"configured": True, "running": True, "state": {"status": "running"}, "lock": {"pid": 777}},
    ])
    monkeypatch.setattr(supervisor_module, "supervisor_status", lambda _root: next(statuses))

    class FakeProcess:
        pid = 123
        returncode = None

        def poll(self):
            return None

    monkeypatch.setattr(supervisor_module.subprocess, "Popen", lambda *_a, **_k: FakeProcess())
    result = supervisor_module.start_supervisor(RuntimeOptions(tmp_path), wait_seconds=1)
    assert result["ok"]
    assert result["pid"] == 777
    assert result["launcher_pid"] == 123


def test_start_supervisor_accepts_fast_clean_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoyy.supervisor as supervisor_module

    _project(tmp_path, ("01-a",))
    statuses = iter([
        {"configured": False, "running": False, "state": None, "lock": None},
        {"configured": True, "running": False, "state": {"status": "idle"}, "lock": None},
        {"configured": True, "running": False, "state": {"status": "idle"}, "lock": None},
    ])
    monkeypatch.setattr(supervisor_module, "supervisor_status", lambda _root: next(statuses))

    class FakeProcess:
        pid = 321
        returncode = 0

        def poll(self):
            return 0

    monkeypatch.setattr(supervisor_module.subprocess, "Popen", lambda *_a, **_k: FakeProcess())
    result = supervisor_module.start_supervisor(RuntimeOptions(tmp_path), wait_seconds=1)
    assert result["ok"] and result["exited"]
    assert result["status"]["state"]["status"] == "idle"


def test_daemon_command_serializes_nonsecret_runtime_options(tmp_path: Path) -> None:
    import autoyy.supervisor as supervisor_module

    manifest = tmp_path / "manifest.csv"
    command = supervisor_module._daemon_command(
        RuntimeOptions(
            tmp_path, manifest=manifest, allow_legacy_ignored=True,
            strategy="repair-first", download_workers=3, asr_workers=2,
            asr_device="cuda", asr_model_size="small", rate_limit="10M",
            max_command_attempts=4,
        ),
        interval_seconds=1.5, stop_when_idle=True, idle_cycles=4,
        retry_base_seconds=2, retry_max_seconds=30, retune_every=10,
    )
    text = " ".join(command)
    for token in ("--manifest", "--allow-legacy-ignored", "repair-first", "--download-workers", "--asr-workers", "--stop-when-idle"):
        assert token in text
