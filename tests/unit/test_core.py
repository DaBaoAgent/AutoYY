from __future__ import annotations

import csv
from pathlib import Path

import pytest

from autoyy.manifest import FIELDS, load_manifest, validate_template_headers
from autoyy.paths import safe_child_path
from autoyy.publication import validate_publication_text
from autoyy.state import empty_state, ensure_topic, set_stage
from autoyy.subtitles import parse_srt, validate_srt


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def row(folder: str = "01-test", url: str = "https://www.youtube.com/watch?v=abc") -> dict[str, str]:
    return {field: "" for field in FIELDS} | {"id": "01", "folder_name": folder, "url": url, "match_grade": "exact"}


def test_safe_child_path_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_child_path(tmp_path, "..\\outside")
    with pytest.raises(ValueError):
        safe_child_path(tmp_path, str((tmp_path / "absolute").resolve()))


def test_manifest_rejects_duplicate_and_bad_url(tmp_path: Path) -> None:
    path = tmp_path / "manifest.csv"
    write_manifest(path, [row(), row()])
    with pytest.raises(ValueError, match="duplicate folder_name"):
        load_manifest(path, output_root=tmp_path)
    write_manifest(path, [row(url="https://example.com/video")])
    with pytest.raises(ValueError, match="unsupported url"):
        load_manifest(path, output_root=tmp_path)


def test_manifest_template_schema() -> None:
    validate_template_headers(Path("assets/topic-manifest-template.csv"))


def test_srt_non_contiguous_index_fails(tmp_path: Path) -> None:
    path = tmp_path / "bad.srt"
    path.write_text("1\n00:00:00,000 --> 00:00:01,000\n甲\n\n3\n00:00:02,000 --> 00:00:03,000\n乙\n", encoding="utf-8")
    cues, issues = parse_srt(path)
    assert len(cues) == 2
    assert any("expected 2" in issue for issue in issues)
    assert not validate_srt(path)["valid"]


def test_publication_strict_two_lines_unique_tags() -> None:
    good = validate_publication_text("这个标题不超过二十五字\n#纪录片 #监狱 #人物 #故事 #真相")
    assert good["valid"]
    duplicate = validate_publication_text("标题\n#纪录片 #纪录片 #人物 #故事 #真相")
    assert not duplicate["valid"]
    blank = validate_publication_text("标题\n\n#纪录片 #监狱 #人物 #故事 #真相")
    assert not blank["valid"]


def test_state_upstream_change_marks_downstream_stale() -> None:
    state = empty_state()
    ensure_topic(state, "01-test")
    set_stage(state, "01-test", "source", "ready", fingerprint="a")
    set_stage(state, "01-test", "subtitle", "ready", fingerprint="s")
    set_stage(state, "01-test", "voiceover", "ready", fingerprint="v")
    set_stage(state, "01-test", "source", "ready", fingerprint="b")
    assert state["topics"]["01-test"]["stages"]["subtitle"]["status"] == "stale"
    assert state["topics"]["01-test"]["stages"]["voiceover"]["status"] == "stale"


def test_state_corruption_is_not_silently_rebuilt(tmp_path: Path) -> None:
    from autoyy.state import load_state, state_path

    path = state_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupted"):
        load_state(tmp_path, create=True)


def test_running_state_recovers_to_failed(tmp_path: Path) -> None:
    from autoyy.state import load_state, recover_running, save_state

    state = empty_state()
    ensure_topic(state, "01-test")
    set_stage(state, "01-test", "subtitle", "running")
    save_state(tmp_path, state)
    loaded = load_state(tmp_path)
    assert recover_running(loaded) == 1
    assert loaded["topics"]["01-test"]["stages"]["subtitle"]["status"] == "failed"


def test_state_revision_conflict_prevents_lost_update(tmp_path: Path) -> None:
    from autoyy.state import empty_state, load_state, save_state

    first = empty_state()
    save_state(tmp_path, first)
    left = load_state(tmp_path)
    right = load_state(tmp_path)
    left["topics"]["left"] = {"stages": {}, "approved": {}}
    save_state(tmp_path, left)
    right["topics"]["right"] = {"stages": {}, "approved": {}}
    with pytest.raises(OSError, match="revision conflict"):
        save_state(tmp_path, right)


def test_file_fingerprint_tracks_content_not_only_metadata(tmp_path: Path) -> None:
    from autoyy.state import file_fingerprint

    path = tmp_path / "same-size.txt"
    path.write_text("abc", encoding="utf-8")
    before = file_fingerprint(path)
    path.write_text("xyz", encoding="utf-8")
    after = file_fingerprint(path)
    assert before.startswith("sha256:")
    assert before != after


def test_update_state_retries_revision_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoyy.state as state_module

    original = state_module.save_state
    calls = 0

    def flaky(root, state):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("state revision conflict: expected 0, current 1")
        return original(root, state)

    monkeypatch.setattr(state_module, "save_state", flaky)
    state_module.update_state(
        tmp_path, lambda state: state_module.set_stage(state, "01-a", "source", "ready"), create=True
    )
    loaded = state_module.load_state(tmp_path)
    assert loaded["topics"]["01-a"]["stages"]["source"]["status"] == "ready"
    assert calls == 2
