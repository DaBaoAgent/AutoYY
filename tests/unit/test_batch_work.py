from __future__ import annotations

import json
import time
from pathlib import Path

from autoyy.batch import batch_status, stage_plan
from autoyy.state import empty_state, save_state, set_stage
from autoyy.work import claim_work, heartbeat_work, release_work, work_status


def make_project(root: Path, count: int = 3) -> None:
    state = empty_state()
    for index in range(1, count + 1):
        name = f"{index:02d}-topic-{index}"
        (root / name).mkdir(parents=True)
        set_stage(state, name, "source", "ready", fingerprint=f"source-{index}")
        set_stage(state, name, "subtitle", "ready", fingerprint=f"subtitle-{index}")
    save_state(root, state)


def test_batch_plan_reports_runnable_voiceovers(tmp_path: Path) -> None:
    make_project(tmp_path, 3)
    plan = stage_plan(tmp_path, "voiceover")
    assert plan["topic_count"] == 3
    assert plan["runnable_count"] == 3
    assert all(row["runnable"] for row in plan["results"])
    status = batch_status(tmp_path)
    assert status["topic_count"] == 3
    assert all(row["next_stage"] == "voiceover" for row in status["results"])


def test_worker_can_hold_only_one_topic_lease(tmp_path: Path) -> None:
    make_project(tmp_path, 3)
    first = claim_work(tmp_path, worker_id="writer-a", stage="voiceover")
    second = claim_work(tmp_path, worker_id="writer-a", stage="voiceover")
    assert first["ok"] and second["ok"]
    assert not first["reused"]
    assert second["reused"]
    assert first["lease"]["token"] == second["lease"]["token"]
    assert work_status(tmp_path)["active_count"] == 1


def test_distinct_workers_receive_distinct_topics(tmp_path: Path) -> None:
    make_project(tmp_path, 3)
    left = claim_work(tmp_path, worker_id="writer-a", stage="voiceover")
    right = claim_work(tmp_path, worker_id="writer-b", stage="voiceover")
    assert left["lease"]["topic"] != right["lease"]["topic"]
    assert work_status(tmp_path)["active_count"] == 2


def test_release_allows_worker_to_claim_next_topic(tmp_path: Path) -> None:
    make_project(tmp_path, 2)
    first = claim_work(tmp_path, worker_id="writer-a", stage="voiceover")
    token = first["lease"]["token"]
    assert release_work(tmp_path, token)["ok"]
    second = claim_work(tmp_path, worker_id="writer-a", stage="voiceover", topic="02-topic-2")
    assert second["ok"]
    assert second["lease"]["topic"] == "02-topic-2"


def test_expired_lease_is_reclaimed(tmp_path: Path) -> None:
    make_project(tmp_path, 1)
    result = claim_work(tmp_path, worker_id="writer-a", stage="voiceover", lease_seconds=60)
    lease_files = list((tmp_path / ".autoyy" / "leases").glob("*.json"))
    assert len(lease_files) == 1
    payload = json.loads(lease_files[0].read_text(encoding="utf-8"))
    payload["expires_at_epoch"] = time.time() - 1
    lease_files[0].write_text(json.dumps(payload), encoding="utf-8")
    reclaimed = claim_work(tmp_path, worker_id="writer-b", stage="voiceover")
    assert reclaimed["ok"]
    assert reclaimed["lease"]["topic"] == result["lease"]["topic"]


def test_heartbeat_extends_active_lease(tmp_path: Path) -> None:
    make_project(tmp_path, 1)
    result = claim_work(tmp_path, worker_id="writer-a", stage="voiceover", lease_seconds=60)
    before = result["lease"]["expires_at_epoch"]
    heartbeat = heartbeat_work(tmp_path, result["lease"]["token"], lease_seconds=120)
    assert heartbeat["ok"]
    assert heartbeat["lease"]["expires_at_epoch"] > before


def test_stage_plan_blocks_missing_dependencies(tmp_path: Path) -> None:
    (tmp_path / "01-topic").mkdir()
    plan = stage_plan(tmp_path, "voiceover")
    assert plan["runnable_count"] == 0
    assert plan["results"][0]["blockers"] == ["subtitle"]


def test_multi_topic_scaffold_requires_matching_lease(tmp_path: Path) -> None:
    from autoyy.cli import main

    make_project(tmp_path, 2)
    topic = tmp_path / "01-topic-1"
    (topic / "字幕.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n测试字幕\n", encoding="utf-8"
    )
    (topic / "爆款口播稿.candidate.txt").write_text("甲" * 4500, encoding="utf-8")
    assert main(["voiceover", "scaffold", str(topic)]) == 2
    claim = claim_work(tmp_path, worker_id="writer-run-a", stage="voiceover", topic=topic.name)
    token = claim["lease"]["token"]
    assert main(["voiceover", "scaffold", str(topic), "--lease-token", token]) == 0
    quality = json.loads((topic / ".autoyy" / "voiceover-quality.json").read_text(encoding="utf-8"))
    assert quality["writer"]["lease_token"] == token
    assert quality["writer"]["run_id"] == "writer-run-a"


def test_stale_worker_claim_lock_recovers(tmp_path: Path) -> None:
    import os

    import autoyy.work as work_module

    make_project(tmp_path, 1)
    lock = work_module._worker_lock_path(tmp_path, "writer-a")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("dead", encoding="utf-8")
    old = time.time() - 60
    os.utime(lock, (old, old))
    result = claim_work(tmp_path, worker_id="writer-a", stage="voiceover")
    assert result["ok"]


def test_batch_and_work_cli_lifecycle(tmp_path: Path) -> None:
    from autoyy.cli import main

    make_project(tmp_path, 2)
    assert main(["batch", "status", str(tmp_path), "--json"]) == 0
    assert main(["batch", "plan", str(tmp_path), "voiceover", "--json"]) == 0
    assert main([
        "work", "claim", str(tmp_path), "--worker-id", "cli-writer",
        "--stage", "voiceover", "--json",
    ]) == 0
    leases = work_status(tmp_path)["leases"]
    assert len(leases) == 1
    token = leases[0]["token"]
    assert main([
        "work", "heartbeat", str(tmp_path), token,
        "--lease-seconds", "120", "--json",
    ]) == 0
    assert main(["work", "status", str(tmp_path), "--json"]) == 0
    assert main(["work", "release", str(tmp_path), token, "--json"]) == 0
    assert work_status(tmp_path)["active_count"] == 0


def test_worker_cannot_switch_topic_or_stage_with_active_lease(tmp_path: Path) -> None:
    make_project(tmp_path, 2)
    first = claim_work(tmp_path, worker_id="writer-a", stage="voiceover", topic="01-topic-1")
    assert first["ok"]
    import pytest

    with pytest.raises(RuntimeError, match="already holds"):
        claim_work(tmp_path, worker_id="writer-a", stage="voiceover", topic="02-topic-2")
    with pytest.raises(RuntimeError, match="already holds"):
        claim_work(tmp_path, worker_id="writer-a", stage="publication")


def test_work_cli_rejects_missing_root(tmp_path: Path) -> None:
    from autoyy.cli import main

    missing = tmp_path / "missing"
    assert main(["work", "status", str(missing), "--json"]) == 2
    assert main(["work", "release", str(missing), "token", "--json"]) == 2
    assert main(["work", "heartbeat", str(missing), "token", "--json"]) == 2
