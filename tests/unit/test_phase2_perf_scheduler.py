from __future__ import annotations

import argparse
import threading
from pathlib import Path

from autoyy.diagnostics import diagnose
from autoyy.download import DownloadOptions, process_row
from autoyy.manifest import ManifestRow
from autoyy.observability import recent_events, record_event
from autoyy.profiling import inventory_root, profile_workload
from autoyy.scheduler import scheduler_plan
from autoyy.state import empty_state, media_fingerprint, save_state, set_stage
from autoyy.transcribe import process_folder
from autoyy.work import claim_work, release_work


def make_scheduler_project(root: Path) -> None:
    state = empty_state()
    for name in ("01-near-done", "02-new"):
        (root / name).mkdir(parents=True)
    set_stage(state, "01-near-done", "source", "ready", fingerprint="s1")
    set_stage(state, "01-near-done", "subtitle", "ready", fingerprint="t1")
    save_state(root, state)


def test_inventory_surfaces_legacy_topic_names(tmp_path: Path) -> None:
    (tmp_path / "01-standard").mkdir()
    (tmp_path / "02 legacy topic").mkdir()
    (tmp_path / "notes").mkdir()
    result = inventory_root(tmp_path)
    assert result["recognized_topic_count"] == 1
    assert result["legacy_candidate_count"] == 1
    assert result["legacy_candidates"] == ["02 legacy topic"]
    profile = profile_workload(tmp_path)
    assert profile["warnings"][0]["code"] == "LEGACY_TOPIC_NAMES_IGNORED"


def test_scheduler_prioritizes_near_complete_topic(tmp_path: Path) -> None:
    make_scheduler_project(tmp_path)
    result = scheduler_plan(tmp_path, capabilities=["source", "voiceover"])
    assert result["next"]["topic"] == "01-near-done"
    assert result["next"]["stage"] == "voiceover"
    assert result["next"]["score"] > 10


def test_auto_claim_uses_scheduler_and_keeps_one_topic(tmp_path: Path) -> None:
    make_scheduler_project(tmp_path)
    first = claim_work(
        tmp_path,
        worker_id="agent-a",
        stage="auto",
        capabilities=["source", "voiceover"],
    )
    assert first["ok"]
    assert first["lease"]["topic"] == "01-near-done"
    assert first["lease"]["stage"] == "voiceover"
    second = claim_work(
        tmp_path,
        worker_id="agent-a",
        stage="auto",
        capabilities=["source", "voiceover"],
    )
    assert second["reused"]
    assert second["lease"]["token"] == first["lease"]["token"]
    assert release_work(tmp_path, first["lease"]["token"])["ok"]


def test_media_fingerprint_samples_large_files(tmp_path: Path) -> None:
    small = tmp_path / "small.bin"
    small.write_bytes(b"a" * 1024)
    large = tmp_path / "large.bin"
    with large.open("wb") as handle:
        handle.seek(9 * 1024 * 1024 - 1)
        handle.write(b"x")
    assert media_fingerprint(small).startswith("sha256:")
    sampled = media_fingerprint(large)
    assert sampled.startswith("sha256-sampled:")
    assert str(large.stat().st_size) in sampled


def test_observability_and_diagnose_surface_failures(tmp_path: Path) -> None:
    (tmp_path / "01-topic").mkdir()
    record_event(
        tmp_path,
        "topic_finish",
        command="download",
        topic="01-topic",
        stage="source",
        status="failed",
        code="DOWNLOAD_VIDEO_FAILED",
        message="network test failure",
    )
    events = recent_events(tmp_path, failures_only=True)
    assert events[-1]["code"] == "DOWNLOAD_VIDEO_FAILED"
    result = diagnose(tmp_path)
    assert result["failure_codes"]["DOWNLOAD_VIDEO_FAILED"] == 1
    assert "DOWNLOAD_VIDEO_FAILED" in result["error_hints"]


def test_download_overlaps_video_with_subtitle_probe(tmp_path: Path, monkeypatch) -> None:
    import autoyy.download as download_module

    video_started = threading.Event()
    metadata_started = threading.Event()
    calls = {"ready": 0}

    def fake_ready(*_args):
        calls["ready"] += 1
        return calls["ready"] > 1

    def fake_video(*_args, **_kwargs):
        video_started.set()
        assert metadata_started.wait(1)

    def fake_language(*_args, **_kwargs):
        assert video_started.wait(1)
        metadata_started.set()
        return "en"

    monkeypatch.setattr(download_module, "_verified_ready", fake_ready)
    monkeypatch.setattr(download_module, "_download_video", fake_video)
    monkeypatch.setattr(download_module, "_select_subtitle_language", fake_language)
    monkeypatch.setattr(download_module, "_download_subtitle", lambda *_a, **_k: "en")
    monkeypatch.setattr(download_module, "_video_valid", lambda *_a: True)
    monkeypatch.setattr(download_module, "_subtitle_valid", lambda *_a: True)
    row = ManifestRow({"folder_name": "01-topic", "url": "https://www.youtube.com/watch?v=abc"})
    options = DownloadOptions(tmp_path / "manifest.csv", tmp_path, overlap_assets=True)
    result = process_row(row, options, "yt-dlp", "ffprobe")
    assert result["status"] == "complete"
    assert metadata_started.is_set()


def test_whisper_transcribes_directly_from_video_without_ffmpeg(tmp_path: Path, monkeypatch) -> None:
    import autoyy.transcribe as transcribe_module

    topic = tmp_path / "01-topic"
    topic.mkdir()
    video = topic / "高清源视频.mp4"
    video.write_bytes(b"fake")
    seen: dict[str, Path] = {}

    monkeypatch.setattr(transcribe_module, "find_primary_video", lambda _folder: video)
    monkeypatch.setattr(transcribe_module, "find_primary_subtitle", lambda _folder: None)
    monkeypatch.setattr(transcribe_module, "resolve_backend", lambda _name, _language=None: "whisper")

    def fake_whisper(source, *_args, **_kwargs):
        seen["source"] = source
        return [(0.0, 1.0, "direct decode works")]

    monkeypatch.setattr(transcribe_module, "transcribe_whisper", fake_whisper)
    args = argparse.Namespace(
        overwrite=False,
        dry_run=False,
        ffmpeg_timeout=10,
        device="cpu",
        model_size="tiny",
        vad=True,
        beam_size=1,
        run_id="test-run",
    )
    result = process_folder(topic, args, "whisper", None, "en")
    assert result["status"] == "ready"
    assert result["backend"] == "whisper"
    assert seen["source"] == video
    assert (topic / "字幕.srt").is_file()


def test_phase2_cli_surfaces_profile_schedule_and_auto_claim(tmp_path: Path) -> None:
    from autoyy.cli import main

    make_scheduler_project(tmp_path)
    assert main(["profile", str(tmp_path), "--json"]) == 0
    assert main(["schedule", str(tmp_path), "--capabilities", "source,voiceover", "--json"]) == 0
    assert main(["batch", "inventory", str(tmp_path), "--json"]) == 0
    assert main([
        "work", "claim", str(tmp_path), "--worker-id", "agent-cli",
        "--stage", "auto", "--capabilities", "source,voiceover", "--json",
    ]) == 0


def test_auto_scheduler_enforces_stage_capacity(tmp_path: Path) -> None:
    state = empty_state()
    for index in range(1, 7):
        name = f"{index:02d}-topic"
        (tmp_path / name).mkdir()
        ensure = state.setdefault("topics", {}).setdefault(name, {"stages": {}, "approved": {}})
        _ = ensure
    save_state(tmp_path, state)
    leases = []
    for index in range(4):
        result = claim_work(
            tmp_path,
            worker_id=f"source-{index}",
            stage="auto",
            capabilities=["source"],
        )
        assert result["ok"]
        leases.append(result["lease"]["token"])
    blocked = claim_work(
        tmp_path,
        worker_id="source-over-capacity",
        stage="auto",
        capabilities=["source"],
    )
    assert not blocked["ok"]
    for token in leases:
        assert release_work(tmp_path, token)["ok"]


def test_scheduler_strategy_is_explicit_and_deterministic(tmp_path: Path) -> None:
    make_scheduler_project(tmp_path)
    finish = scheduler_plan(
        tmp_path,
        capabilities=["source", "voiceover"],
        strategy="finish-first",
    )
    source = scheduler_plan(
        tmp_path,
        capabilities=["source", "voiceover"],
        strategy="source-first",
    )
    assert finish["next"]["stage"] == "voiceover"
    assert source["next"]["stage"] == "source"
    assert finish["strategy"] == "finish-first"
    assert source["strategy"] == "source-first"


def test_phase2_cli_errors_are_structured(tmp_path: Path) -> None:
    from autoyy.cli import main

    missing = tmp_path / "missing"
    assert main(["profile", str(missing), "--json"]) == 2
    assert main(["diagnose", str(missing), "--json"]) == 2
    assert main(["schedule", str(missing), "--json"]) == 2
    (tmp_path / "01-topic").mkdir()
    assert main(["schedule", str(tmp_path), "--capabilities", "bogus", "--json"]) == 2


def test_event_log_rotates_without_breaking_execution(tmp_path: Path, monkeypatch) -> None:
    import autoyy.observability as obs

    monkeypatch.setattr(obs, "MAX_EVENTS_BYTES", 128)
    for index in range(20):
        obs.record_event(
            tmp_path,
            "topic_finish",
            command="download",
            topic=f"{index:02d}-topic",
            status="failed",
            code="TEST_FAILURE",
            message="x" * 40,
        )
    assert (tmp_path / ".autoyy" / "events.jsonl").is_file()
    assert (tmp_path / ".autoyy" / "events.1.jsonl").is_file()
    assert obs.recent_events(tmp_path, limit=5)


def test_manual_claim_cannot_bypass_stage_capacity(tmp_path: Path) -> None:
    state = empty_state()
    for index in (1, 2):
        name = f"{index:02d}-topic"
        (tmp_path / name).mkdir()
        set_stage(state, name, "source", "ready", fingerprint=f"s{index}")
    save_state(tmp_path, state)
    first = claim_work(tmp_path, worker_id="asr-a", stage="subtitle")
    assert first["ok"]
    second = claim_work(tmp_path, worker_id="asr-b", stage="subtitle")
    assert not second["ok"]
    assert second["reason"] == "stage_capacity_reached"
