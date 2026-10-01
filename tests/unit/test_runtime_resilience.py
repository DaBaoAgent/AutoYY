from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import autoyy.download as dl
import autoyy.resources as resources
from autoyy.retry import RetryPolicy, classify_failure, is_retryable
from autoyy.runtime import RuntimeOptions, runtime_plan, runtime_tick
from autoyy.soak import run_control_plane_soak
from autoyy.state import empty_state, load_state, save_state, set_stage


def make_project(root: Path, *, source: str = "pending", subtitle: str = "pending", voiceover: str = "pending") -> Path:
    topic = root / "01-topic"
    topic.mkdir(parents=True)
    state = empty_state()
    set_stage(state, topic.name, "source", source, fingerprint="source")
    set_stage(state, topic.name, "subtitle", subtitle, fingerprint="subtitle")
    set_stage(state, topic.name, "voiceover", voiceover, fingerprint="voiceover")
    save_state(root, state)
    return topic


def test_resource_plan_adapts_cpu_and_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(resources, "_windows_memory_gb", lambda: 16.0)
    monkeypatch.setattr(resources, "_nvidia_gpu", lambda: (None, None))
    resources.detect_resources.cache_clear()
    plan = resources.detect_resources()
    assert (plan.asr_device, plan.asr_workers, plan.download_workers) == ("cpu", 1, 4)
    monkeypatch.setattr(resources, "_nvidia_gpu", lambda: ("GPU", 16.0))
    resources.detect_resources.cache_clear()
    plan = resources.detect_resources()
    assert (plan.asr_device, plan.asr_workers) == ("cuda", 2)
    resources.detect_resources.cache_clear()


def test_retry_classification_and_deterministic_backoff() -> None:
    assert classify_failure(stderr="HTTP Error 429: Too Many Requests") == "RATE_LIMITED"
    assert classify_failure(stderr="connection reset by peer") == "NETWORK_TRANSIENT"
    assert classify_failure(stderr="HTTP Error 403 Forbidden") == "AUTH_REQUIRED"
    assert is_retryable("RATE_LIMITED")
    policy = RetryPolicy(max_attempts=3, base_seconds=2, max_seconds=10, jitter_ratio=0.1)
    assert policy.delay(2, seed="same") == policy.delay(2, seed="same")
    assert 3.6 <= policy.delay(2, seed="same") <= 4.4


def test_download_command_retries_rate_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            return subprocess.CompletedProcess(["fake"], 1, "", "HTTP Error 429: Too Many Requests")
        return subprocess.CompletedProcess(["fake"], 0, "ok", "")

    monkeypatch.setattr(dl.subprocess, "run", fake_run)
    opts = dl.DownloadOptions(
        tmp_path / "manifest.csv", tmp_path,
        max_command_attempts=3, retry_base_seconds=0, retry_max_seconds=0,
    )
    result = dl._run(["fake"], opts)
    assert result.returncode == 0
    assert calls == 3


def test_runtime_plan_blocks_legacy_inventory(tmp_path: Path) -> None:
    make_project(tmp_path)
    (tmp_path / "02 legacy topic").mkdir()
    result = runtime_plan(RuntimeOptions(tmp_path))
    codes = {item["code"] for item in result["blockers"]}
    deferred = {item["code"] for item in result["deferred"]}
    assert "LEGACY_TOPIC_NAMES_IGNORED" in codes
    assert "MANIFEST_REQUIRED" in deferred
    assert not result["runnable"]


def test_runtime_tick_stops_at_external_stage(tmp_path: Path) -> None:
    make_project(tmp_path, source="ready", subtitle="ready", voiceover="pending")
    before = load_state(tmp_path)["revision"]
    code, result = runtime_tick(RuntimeOptions(tmp_path, allow_legacy_ignored=True), reconcile=False)
    assert code == 1
    assert result["status"] == "waiting_external"
    assert result["external_candidates"][0]["stage"] == "voiceover"
    assert load_state(tmp_path)["revision"] == before


def test_runtime_source_uses_resource_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path)
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("folder_name,url\n01-topic,https://youtu.be/x\n", encoding="utf-8")
    captured = {}

    def fake_download(options):
        captured["workers"] = options.parallel
        captured["rate_limit"] = options.rate_limit
        return 0, {"valid": True, "results": []}

    monkeypatch.setattr("autoyy.runtime.run_download", fake_download)
    monkeypatch.setattr("autoyy.runtime.detect_resources", lambda: resources.ResourcePlan(8, 16, None, None, "cpu", 1, 4, 4))
    code, result = runtime_tick(RuntimeOptions(
        tmp_path, manifest=manifest, allow_legacy_ignored=True, rate_limit="20M"
    ))
    assert code == 0
    assert result["action"]["stage"] == "source"
    assert captured == {"workers": 4, "rate_limit": "20M"}


def test_soak_recovers_faults_without_duplicate_claims() -> None:
    result = run_control_plane_soak(topics=8, operations=100, workers=6, fault_rate=0.15, seed=7)
    assert result["invariants_ok"]
    assert result["duplicate_claims"] == 0
    assert result["dangling_leases"] == 0
    assert result["faults_injected"] > 0
    assert result["completed_topics"] == 8


def test_download_adaptive_parallel_reduces_on_retryable_wave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoyy.manifest import ManifestRow

    rows = [ManifestRow({"folder_name": f"{i:02d}-topic", "url": f"https://youtu.be/{i}"}) for i in range(1, 13)]
    monkeypatch.setattr(dl, "load_manifest", lambda *_a, **_k: rows)
    monkeypatch.setattr(dl, "_executable", lambda _v: "fake-ytdlp")
    monkeypatch.setattr(dl, "_ffprobe_path", lambda _v: "fake-ffprobe")
    monkeypatch.setattr(dl, "update_state", lambda *_a, **_k: None)
    monkeypatch.setattr(dl, "_write_status", lambda *_a, **_k: None)
    monkeypatch.setattr(dl, "_checkpoint_result", lambda _o, results, item: results.append(item))
    calls = 0

    def fake_process(row, *_args):
        nonlocal calls
        calls += 1
        failed = calls <= 8
        return {
            "folder_name": row.folder_name, "status": "failed" if failed else "complete",
            "error_code": "RATE_LIMITED" if failed else "", "elapsed_ms": 1,
            "video": not failed, "subtitle": not failed,
        }

    monkeypatch.setattr(dl, "process_row", fake_process)
    events = []
    monkeypatch.setattr(dl, "record_event", lambda *_a, **kw: events.append(kw))
    code, summary = dl.run_download(dl.DownloadOptions(tmp_path / "m.csv", tmp_path / "out", parallel=4))
    assert code == 1 and summary["initial_workers"] == 4
    adjustments = [item["details"] for item in events if item.get("details", {}).get("from")]
    assert adjustments[0]["from"] == 4
    assert adjustments[0]["to"] == 2


def test_download_optional_rate_limit(tmp_path: Path) -> None:
    opts = dl.DownloadOptions(tmp_path / "m.csv", tmp_path, rate_limit="15M")
    assert dl._optional_args(opts)[-2:] == ["--limit-rate", "15M"]


def test_runtime_run_advances_machine_stages_then_waits_for_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoyy.runtime import runtime_run
    from autoyy.state import update_state

    make_project(tmp_path)
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("folder_name,url\n01-topic,https://youtu.be/x\n", encoding="utf-8")

    def fake_source(options, _resources, _run_id):
        update_state(options.root, lambda state: set_stage(state, "01-topic", "source", "ready", fingerprint="src"), create=True)
        return {"code": 0, "stage": "source", "status": "ready", "result": {"results": []}}

    def fake_subtitle(options, _resources, _run_id, _topic=None):
        update_state(options.root, lambda state: set_stage(state, "01-topic", "subtitle", "ready", fingerprint="srt"), create=True)
        return {"code": 0, "stage": "subtitle", "status": "ready"}

    monkeypatch.setattr("autoyy.runtime._run_source", fake_source)
    monkeypatch.setattr("autoyy.runtime._run_subtitle", fake_subtitle)
    code, result = runtime_run(RuntimeOptions(tmp_path, manifest=manifest, allow_legacy_ignored=True))
    assert code == 1
    assert result["status"] == "waiting_external"
    assert result["final"]["external_candidates"][0]["stage"] == "voiceover"


def test_runtime_cuda_oom_falls_back_to_cpu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoyy.runtime as runtime_module

    make_project(tmp_path, source="ready")
    calls = []

    def fake_transcribe(args):
        device = args[args.index("--device") + 1]
        calls.append(device)
        if device == "cuda":
            print("CUDA out of memory")
            return 1
        return 0

    monkeypatch.setattr(runtime_module, "transcribe_main", fake_transcribe)
    plan = resources.ResourcePlan(8, 16, "GPU", 12, "cuda", 1, 4, 4)
    result = runtime_module._run_subtitle(RuntimeOptions(tmp_path), plan, "run-x")
    assert result["code"] == 0
    assert result["cpu_fallback"]
    assert calls == ["cuda", "cpu"]


def test_retry_runner_recovers_transient_failures() -> None:
    from autoyy.retry import run_with_retry

    calls = []
    sleeps = []

    def operation(attempt):
        calls.append(attempt)
        if attempt < 3:
            raise RuntimeError("connection reset by peer")
        return "ok"

    value, meta = run_with_retry(
        operation,
        policy=RetryPolicy(max_attempts=3, base_seconds=0, max_seconds=0),
        sleep=sleeps.append,
    )
    assert value == "ok"
    assert meta == {"attempts": 3, "retried": True}
    assert calls == [1, 2, 3]
    assert sleeps == [0, 0]


def test_retry_runner_stops_on_nonretryable_failure() -> None:
    from autoyy.retry import run_with_retry

    with pytest.raises(RuntimeError, match="403"):
        run_with_retry(
            lambda _attempt: (_ for _ in ()).throw(RuntimeError("HTTP Error 403 Forbidden")),
            policy=RetryPolicy(max_attempts=5, base_seconds=0, max_seconds=0),
        )


def test_runtime_package_stage_updates_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoyy.runtime as runtime_module
    from autoyy.state import update_state

    make_project(tmp_path, source="ready", subtitle="ready", voiceover="ready")
    update_state(tmp_path, lambda state: set_stage(state, "01-topic", "publication", "ready", fingerprint="pub"), create=True)
    update_state(tmp_path, lambda state: set_stage(state, "01-topic", "cover", "ready", fingerprint="cover"), create=True)
    monkeypatch.setattr(runtime_module, "_ffprobe", lambda _options: "ffprobe")
    monkeypatch.setattr(runtime_module, "validate_topic", lambda folder, **_kwargs: {
        "folder": folder.name, "complete": True, "issues": []
    })
    code, result = runtime_tick(RuntimeOptions(tmp_path, allow_legacy_ignored=True), reconcile=False)
    assert code == 0
    assert result["action"]["stage"] == "package"
    state = load_state(tmp_path)
    assert state["topics"]["01-topic"]["stages"]["package"]["status"] == "ready"


def test_runtime_cli_surface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autoyy.cli import main

    topic = make_project(tmp_path, source="ready", subtitle="ready")
    (topic / "高清源视频.mp4").write_bytes(b"video")
    (topic / "字幕.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nok\n", encoding="utf-8")
    monkeypatch.setattr("autoyy.runtime._ffprobe", lambda _options: "ffprobe")
    monkeypatch.setattr("autoyy.runtime.probe_media", lambda *_a, **_k: {"duration": 1.0})
    monkeypatch.setattr(resources, "_nvidia_gpu", lambda: (None, None))
    assert main(["resources", "--json"]) == 0
    assert main(["runtime", "plan", str(tmp_path), "--json"]) == 0
    assert main(["runtime", "tick", str(tmp_path), "--json"]) == 1
    assert main(["runtime", "run", str(tmp_path), "--dry-run", "--json"]) == 0
    assert main(["runtime", "soak", "--topics", "3", "--operations", "30", "--workers", "2", "--fault-rate", "0.1", "--json"]) == 0
    assert main(["runtime", "plan", str(tmp_path / "missing"), "--json"]) == 2


def test_soak_rejects_invalid_options() -> None:
    with pytest.raises(ValueError, match="topics"):
        run_control_plane_soak(topics=100)
    with pytest.raises(ValueError, match="fault_rate"):
        run_control_plane_soak(topics=2, fault_rate=1.0)


def test_reconcile_existing_promotes_verified_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoyy.runtime as runtime_module

    topic = make_project(tmp_path)
    (topic / "高清源视频.mp4").write_bytes(b"video")
    (topic / "字幕.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nok\n", encoding="utf-8"
    )
    monkeypatch.setattr(runtime_module, "_ffprobe", lambda _options: "ffprobe")
    monkeypatch.setattr(runtime_module, "probe_media", lambda *_a, **_k: {"duration": 1.0})
    result = runtime_module.reconcile_existing(RuntimeOptions(tmp_path))
    assert result["changed_count"] == 2
    state = load_state(tmp_path)
    stages = state["topics"]["01-topic"]["stages"]
    assert stages["source"]["status"] == "ready"
    assert stages["subtitle"]["status"] == "ready"


def test_reconcile_downgrades_missing_ready_artifacts(tmp_path: Path) -> None:
    import autoyy.runtime as runtime_module

    make_project(tmp_path, source="ready", subtitle="ready")
    result = runtime_module.reconcile_existing(RuntimeOptions(tmp_path))
    assert result["changed_count"] == 2
    stages = load_state(tmp_path)["topics"]["01-topic"]["stages"]
    assert stages["source"]["status"] == "failed"
    assert stages["subtitle"]["status"] == "failed"


def test_runtime_continues_ready_branch_when_other_source_needs_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoyy.state import update_state

    first = tmp_path / "01-first"
    second = tmp_path / "02-second"
    first.mkdir()
    second.mkdir()
    state = empty_state()
    set_stage(state, first.name, "source", "ready", fingerprint="src1")
    set_stage(state, first.name, "subtitle", "pending")
    set_stage(state, second.name, "source", "pending")
    save_state(tmp_path, state)

    def fake_subtitle(options, _resources, _run_id, _topic=None):
        update_state(options.root, lambda st: set_stage(st, first.name, "subtitle", "ready", fingerprint="srt1"), create=True)
        return {"code": 0, "stage": "subtitle", "status": "ready"}

    monkeypatch.setattr("autoyy.runtime._run_subtitle", fake_subtitle)
    plan = runtime_plan(RuntimeOptions(tmp_path, allow_legacy_ignored=True))
    assert plan["runnable"]
    assert plan["deferred"][0]["code"] == "MANIFEST_REQUIRED"
    code, result = runtime_tick(RuntimeOptions(tmp_path, allow_legacy_ignored=True), reconcile=False)
    assert code == 0
    assert result["action"]["stage"] == "subtitle"


def test_reconcile_is_idempotent_after_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoyy.runtime as runtime_module

    topic = make_project(tmp_path)
    (topic / "高清源视频.mp4").write_bytes(b"video")
    (topic / "字幕.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nok\n", encoding="utf-8"
    )
    monkeypatch.setattr(runtime_module, "_ffprobe", lambda _options: "ffprobe")
    monkeypatch.setattr(runtime_module, "probe_media", lambda *_a, **_k: {"duration": 1.0})
    first = runtime_module.reconcile_existing(RuntimeOptions(tmp_path))
    revision = load_state(tmp_path)["revision"]
    second = runtime_module.reconcile_existing(RuntimeOptions(tmp_path))
    assert first["changed_count"] == 2
    assert second["changed_count"] == 0
    assert load_state(tmp_path)["revision"] == revision


def test_soak_recovers_orphaned_worker_leases() -> None:
    result = run_control_plane_soak(
        topics=8,
        operations=120,
        workers=8,
        fault_rate=0.10,
        crash_rate=0.10,
        seed=7,
    )
    assert result["crashes_injected"] > 0
    assert result["faults_injected"] > 0
    assert result["completed_topics"] == 8
    assert result["duplicate_claims"] == 0
    assert result["dangling_leases"] == 0
    assert result["capacity_violations"] == 0
    assert result["invariants_ok"]


def test_runtime_reports_blocked_work_instead_of_complete(tmp_path: Path) -> None:
    topic = tmp_path / "01-topic"
    topic.mkdir()
    state = empty_state()
    set_stage(state, topic.name, "source", "ready", fingerprint="source")
    set_stage(state, topic.name, "subtitle", "ready", fingerprint="subtitle")
    set_stage(state, topic.name, "voiceover", "blocked", fingerprint="script", reason="blocked_quality")
    save_state(tmp_path, state)
    code, result = runtime_tick(RuntimeOptions(tmp_path, allow_legacy_ignored=True), reconcile=False)
    assert code == 1
    assert result["status"] == "blocked_work"
    assert result["blocked_stages"][0]["stage"] == "voiceover"


def test_runtime_waits_for_active_lease_instead_of_reporting_complete(tmp_path: Path) -> None:
    from autoyy.work import claim_work

    make_project(tmp_path, source="ready", subtitle="ready", voiceover="pending")
    claimed = claim_work(
        tmp_path,
        worker_id="writer-active",
        stage="voiceover",
        topic="01-topic",
    )
    assert claimed["ok"]
    code, result = runtime_tick(
        RuntimeOptions(tmp_path, allow_legacy_ignored=True),
        reconcile=False,
    )
    assert code == 1
    assert result["status"] == "waiting_active"
    assert result["active_leases"][0]["worker_id"] == "writer-active"


def test_runtime_skips_failed_subtitle_for_rest_of_same_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoyy.runtime import runtime_run
    from autoyy.state import update_state

    state = empty_state()
    for name in ("01-first", "02-second"):
        (tmp_path / name).mkdir()
        set_stage(state, name, "source", "ready", fingerprint=f"src-{name}")
        set_stage(state, name, "subtitle", "pending")
    save_state(tmp_path, state)
    calls = []

    def fake_subtitle(options, _resources, _run_id, topic=None):
        calls.append(topic)
        if topic == "01-first":
            return {"code": 1, "stage": "subtitle", "status": "failed"}
        update_state(
            options.root,
            lambda current: set_stage(current, "02-second", "subtitle", "ready", fingerprint="srt-2"),
            create=True,
        )
        return {"code": 0, "stage": "subtitle", "status": "ready"}

    monkeypatch.setattr("autoyy.runtime._run_subtitle", fake_subtitle)
    monkeypatch.setattr(
        "autoyy.runtime.reconcile_existing",
        lambda _options: {"changed_count": 0, "changes": [], "issues": [], "skipped": True},
    )
    code, result = runtime_run(RuntimeOptions(tmp_path, allow_legacy_ignored=True))
    assert code == 1
    assert result["status"] == "deferred_failures"
    assert result["deferred_failures"] == [("01-first", "subtitle")]
    assert calls == ["01-first", "02-second"]
    assert result["pass_limit"] == 20
