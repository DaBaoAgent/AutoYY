from __future__ import annotations

import csv
import json
import struct
from pathlib import Path

import pytest

from autoyy import cli
from autoyy.config import peer_library, work_root
from autoyy.deliverables import image_size, validate_root, validate_topic
from autoyy.doctor import run_doctor
from autoyy.result import CheckResult, exit_code
from autoyy.state import save_state


def make_png_header(path: Path, width: int, height: int) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", width, height) + b"\x00" * 8)


def test_result_contract() -> None:
    assert exit_code() == 0
    assert exit_code(failures=1) == 1
    assert exit_code(usage_error=True) == 2
    result = CheckResult(ok=False, code="bad", message="x", warnings=["w"], details={"a": 1})
    assert result.to_dict()["code"] == "bad"


def test_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTOYY_WORK_ROOT", str(tmp_path))
    assert work_root() == tmp_path
    assert peer_library(tmp_path) == tmp_path / ".autoyy" / "peer-hit-library.csv"


def test_image_size_png_and_unknown(tmp_path: Path) -> None:
    png = tmp_path / "a.png"
    make_png_header(png, 1200, 1600)
    assert image_size(png) == (1200, 1600)
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"nope")
    assert image_size(bad) is None


def test_validate_topic_combines_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    topic = tmp_path / "01-ok"
    topic.mkdir()
    (topic / "高清源视频.mp4").write_bytes(b"video")
    (topic / "字幕.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\ntext\n", encoding="utf-8")
    (topic / "发布信息.txt").write_text("标题\n#a #b #c #d #e", encoding="utf-8")
    make_png_header(topic / "封面-3比4.png", 1200, 1600)
    make_png_header(topic / "封面-4比3.png", 1600, 1200)
    monkeypatch.setattr("autoyy.deliverables.validate_voiceover", lambda *_args, **_kwargs: {"valid": True, "issues": [], "chars": 5000})
    result = validate_topic(topic)
    assert result["complete"]
    assert result["covers"]["封面-3比4"]["ratio_ok"]


def test_validate_root_count_and_empty(tmp_path: Path) -> None:
    assert not validate_root(tmp_path)["valid"]
    assert validate_root(tmp_path, allow_empty=True)["valid"]
    (tmp_path / "01-a").mkdir()
    result = validate_root(tmp_path, expected_count=2)
    assert not result["valid"]
    assert "expected 2" in result["issues"][0]


def test_doctor_with_mocked_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in ("topic-manifest-template.csv", "topic-cover-3x4-approved.png", "topic-cover-4x3-approved.png"):
        (assets / name).write_bytes(b"x")
    monkeypatch.setattr("autoyy.doctor.shutil.which", lambda name: f"C:/fake/{name}.exe" if name in {"yt-dlp", "ffmpeg", "ffprobe", "node"} else None)
    monkeypatch.setattr("autoyy.doctor.work_root", lambda: tmp_path / "work")
    result = run_doctor(tmp_path)
    assert result["ok"]
    assert result["checks"]["optional_commands"]["node"]["ok"]


def test_cli_doctor_and_download_dispatch(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "run_doctor", lambda *_args, **_kwargs: {"ok": True, "checks": {}})
    assert cli.main(["--json", "doctor"]) == 0
    assert '"ok": true' in capsys.readouterr().out
    monkeypatch.setattr(cli, "run_download", lambda _options: (1, {"valid": False, "results": []}))
    code = cli.main(["--json", "download", "--manifest", "x.csv", "--output-root", "out"])
    assert code == 1


def test_cli_publication_state_and_recover(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    topic = tmp_path / "01-a"
    topic.mkdir()
    (topic / "发布信息.txt").write_text("标题\n#a #b #c #d #e", encoding="utf-8")
    assert cli.main(["--json", "publication", "validate", str(tmp_path)]) == 0
    state = json.loads((tmp_path / ".autoyy" / "state.json").read_text(encoding="utf-8"))
    assert state["topics"]["01-a"]["stages"]["publication"]["status"] == "ready"
    state["topics"]["01-a"]["stages"]["voiceover"]["status"] = "running"
    save_state(tmp_path, state)
    assert cli.main(["--json", "state", "show", str(tmp_path)]) == 0
    assert cli.main(["--json", "state", "recover", str(tmp_path)]) == 0
    assert '"recovered": 1' in capsys.readouterr().out


def test_cli_peer_patterns(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    library = tmp_path / "peer.csv"
    fields = ["platform", "title", "hashtags", "views", "likes", "comments", "topic_category", "hook_pattern", "published_at", "source"]
    with library.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"platform": "douyin", "title": "a", "hashtags": "", "views": "100", "likes": "1", "comments": "0", "topic_category": "x", "hook_pattern": "数字冲击", "published_at": "2026-09-01", "source": ""})
    assert cli.main(["--json", "peer", "patterns", "--library", str(library), "--platform", "douyin"]) == 0
    assert "median_views" in capsys.readouterr().out


def test_cli_state_show_missing_returns_usage(tmp_path: Path) -> None:
    assert cli.main(["--json", "state", "show", str(tmp_path)]) == 2


def test_state_stale_propagation_revokes_approval(tmp_path: Path) -> None:
    from autoyy.state import empty_state, is_approved, set_approved, set_stage

    state = empty_state()
    set_stage(state, "01-a", "source", "ready", fingerprint="source-a")
    set_stage(state, "01-a", "subtitle", "ready", fingerprint="sub-a")
    set_stage(state, "01-a", "voiceover", "ready", fingerprint="voice-a")
    set_approved(state, "01-a", "voiceover", True, reason="reviewed")
    assert is_approved(state, "01-a", "voiceover")
    set_stage(state, "01-a", "subtitle", "ready", fingerprint="sub-b")
    assert state["topics"]["01-a"]["stages"]["voiceover"]["status"] == "stale"
    assert not is_approved(state, "01-a", "voiceover")


def test_cli_state_approve_requires_ready_stage(tmp_path: Path) -> None:
    from autoyy.state import empty_state, set_stage

    state = empty_state()
    set_stage(state, "01-a", "voiceover", "ready", fingerprint="voice-a")
    save_state(tmp_path, state)
    assert cli.main(["--json", "state", "approve", str(tmp_path), "01-a", "voiceover"]) == 0
    loaded = json.loads((tmp_path / ".autoyy" / "state.json").read_text(encoding="utf-8"))
    assert loaded["topics"]["01-a"]["approved"]["voiceover"]["approved"] is True
    assert cli.main(["--json", "state", "approve", str(tmp_path), "01-a", "cover"]) == 2


def test_doctor_does_not_create_missing_work_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assets = tmp_path / "repo" / "assets"
    assets.mkdir(parents=True)
    for name in ("topic-manifest-template.csv", "topic-cover-3x4-approved.png", "topic-cover-4x3-approved.png"):
        (assets / name).write_bytes(b"x")
    target = tmp_path / "future" / "work"
    monkeypatch.setattr("autoyy.doctor.shutil.which", lambda name: f"C:/fake/{name}.exe" if name in {"yt-dlp", "ffmpeg", "ffprobe", "node"} else None)
    monkeypatch.setattr("autoyy.doctor.work_root", lambda: target)
    result = run_doctor(tmp_path / "repo")
    assert result["ok"]
    assert not target.exists()


def test_voiceover_cli_uses_standard_chinese_final_filename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    topic = tmp_path / "01-a"
    topic.mkdir()
    final = topic / "爆款口播稿.txt"
    final.write_text("正文", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "validate_voiceover_batch",
        lambda *_args, **_kwargs: {"valid": True, "results": [{"topic": "01-a", "valid": True, "issues": []}]},
    )
    assert cli.main(["--json", "voiceover", "validate", str(tmp_path)]) == 0
    state = json.loads((tmp_path / ".autoyy" / "state.json").read_text(encoding="utf-8"))
    fingerprint = state["topics"]["01-a"]["stages"]["voiceover"]["fingerprint"]
    assert fingerprint not in {"", "missing"}


def test_state_force_resets_stage_and_stales_dependents(tmp_path: Path) -> None:
    from autoyy.state import empty_state, save_state, set_stage

    state = empty_state()
    set_stage(state, "01-a", "subtitle", "ready", fingerprint="sub")
    set_stage(state, "01-a", "voiceover", "ready", fingerprint="voice")
    save_state(tmp_path, state)
    assert cli.main(["--json", "state", "force", str(tmp_path), "01-a", "subtitle"]) == 0
    loaded = json.loads((tmp_path / ".autoyy" / "state.json").read_text(encoding="utf-8"))
    assert loaded["topics"]["01-a"]["stages"]["subtitle"]["status"] == "pending"
    assert loaded["topics"]["01-a"]["stages"]["voiceover"]["status"] == "stale"


def test_voiceover_scaffold_selects_profile(tmp_path: Path) -> None:
    topic = tmp_path / "01-a"
    topic.mkdir()
    (topic / "字幕.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\ntext\n", encoding="utf-8")
    (topic / "爆款口播稿.candidate.txt").write_text("candidate", encoding="utf-8")
    assert cli.main(["--json", "voiceover", "scaffold", str(topic), "--voice-profile", "laorou"]) == 0
    record = json.loads((topic / ".autoyy" / "voiceover-quality.json").read_text(encoding="utf-8"))
    assert record["voice_profile"] == "laorou"
    assert record["humanizer"]["mode"] == "embedded"
