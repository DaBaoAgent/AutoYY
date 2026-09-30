from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import pytest

from autoyy.manifest import FIELDS, load_manifest, validate_template_headers
from autoyy.media import find_primary_subtitle, find_primary_video, probe_media
from autoyy.paths import discover_topics, is_nonempty_file, require_topics, safe_child_path
from autoyy.peer import FIELDS as PEER_FIELDS
from autoyy.peer import load_library, save_library
from autoyy.publication import validate_publication_file, validate_publication_text
from autoyy.subtitles import coverage_metrics, parse_srt, validate_srt


def test_paths_cover_empty_trim_root_and_archive_discovery(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    assert not is_nonempty_file(empty)
    empty.write_bytes(b"x")
    assert is_nonempty_file(empty)
    for bad in ("", " 01-a", "."):
        with pytest.raises(ValueError):
            safe_child_path(tmp_path, bad)
    (tmp_path / "01-a").mkdir()
    archive = tmp_path / "@done"
    (archive / "02-b").mkdir(parents=True)
    assert [p.name for p in discover_topics(tmp_path)] == ["01-a"]
    assert {p.name for p in discover_topics(tmp_path, include_archives=True)} == {"01-a", "02-b"}
    assert require_topics(tmp_path)[0].name == "01-a"


def test_manifest_missing_headers_empty_rows_and_invalid_grade(tmp_path: Path) -> None:
    missing = tmp_path / "missing.csv"
    missing.write_text("folder_name\n01-a\n", encoding="utf-8")
    with pytest.raises(ValueError, match="required headers"):
        load_manifest(missing)
    empty = tmp_path / "empty.csv"
    empty.write_text("folder_name,url\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no data"):
        load_manifest(empty)
    bad = tmp_path / "bad.csv"
    with bad.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        row = {field: "" for field in FIELDS}
        row.update(folder_name="01-a", url="https://www.youtube.com/watch?v=x", match_grade="wrong")
        writer.writerow(row)
    with pytest.raises(ValueError, match="invalid match_grade"):
        load_manifest(bad)
    bad_headers = tmp_path / "template.csv"
    bad_headers.write_text("x,y\n", encoding="utf-8")
    with pytest.raises(ValueError, match="headers differ"):
        validate_template_headers(bad_headers)


def test_subtitle_parser_error_modes_and_metrics(tmp_path: Path) -> None:
    bad = tmp_path / "bad.srt"
    bad.write_text("x\nBAD --> TIME\ntext\n\n2\n00:00:03,000 --> 00:00:02,000\ntext\n", encoding="utf-8")
    _cues, issues = parse_srt(bad)
    assert any("invalid index" in issue for issue in issues)
    reverse = tmp_path / "reverse.srt"
    reverse.write_text("1\n00:00:00,000 --> 00:00:02,000\na\n\n2\n00:00:01,000 --> 00:00:03,000\nb\n", encoding="utf-8")
    result = validate_srt(reverse, video_duration=10.0)
    assert not result["valid"]
    assert any("overlap" in issue for issue in result["issues"])
    assert any("drift" in issue for issue in result["issues"])
    assert coverage_metrics([], 10.0)["coverage_pct"] is None


def test_publication_rejects_labels_emoji_long_title_and_bad_tag(tmp_path: Path) -> None:
    assert not validate_publication_text("标题：测试\n#a #b #c #d #e")["valid"]
    assert not validate_publication_text("测试😀\n#a #b #c #d #e")["valid"]
    assert not validate_publication_text("甲" * 26 + "\n#a #b #c #d #e")["valid"]
    assert not validate_publication_text("测试\n#a #b bad #d #e")["valid"]
    missing = validate_publication_file(tmp_path / "none.txt")
    assert not missing["valid"]


def test_media_discovery_and_probe_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    topic = tmp_path / "01-a"
    topic.mkdir()
    (topic / "other.mp4").write_bytes(b"123")
    (topic / "small.webm").write_bytes(b"1")
    (topic / "other.srt").write_text("x", encoding="utf-8")
    assert find_primary_video(topic).name == "other.mp4"
    assert find_primary_subtitle(topic).name == "other.srt"

    class Proc:
        returncode = 0
        stdout = json.dumps({"format": {"duration": "12.5"}})
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())
    assert probe_media(topic / "other.mp4", "ffprobe")["duration"] == 12.5

    class BadProc:
        returncode = 1
        stdout = ""
        stderr = "broken"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: BadProc())
    with pytest.raises(RuntimeError, match="ffprobe failed"):
        probe_media(topic / "other.mp4", "ffprobe")


def test_peer_library_roundtrip_backup_and_bad_headers(tmp_path: Path) -> None:
    path = tmp_path / "peer.csv"
    row = {field: "" for field in PEER_FIELDS}
    row.update(platform="douyin", title="x", views="1", likes="0", comments="0", hook_pattern="数字冲击")
    save_library(path, [row])
    assert load_library(path)[0]["title"] == "x"
    row["views"] = "2"
    save_library(path, [row], backup=True)
    assert path.with_suffix(".csv.bak").is_file()
    bad = tmp_path / "bad.csv"
    bad.write_text("x,y\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid headers"):
        load_library(bad)


def test_deliverable_jpeg_size_and_bad_cover_ratio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autoyy.deliverables import image_size, validate_topic

    jpeg = tmp_path / "tiny.jpg"
    jpeg.write_bytes(b"\xff\xd8\xff\xc0\x00\x07\x08\x00\x10\x00\x20")
    assert image_size(jpeg) == (32, 16)

    topic = tmp_path / "01-bad-cover"
    topic.mkdir()
    (topic / "高清源视频.mp4").write_bytes(b"video")
    (topic / "字幕.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\ntext\n", encoding="utf-8")
    (topic / "发布信息.txt").write_text("标题\n#a #b #c #d #e", encoding="utf-8")
    bad_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (100).to_bytes(4, "big") + (100).to_bytes(4, "big") + b"\x00" * 8
    (topic / "封面-3比4.png").write_bytes(bad_png)
    (topic / "封面-4比3.png").write_bytes(bad_png)
    monkeypatch.setattr("autoyy.deliverables.validate_voiceover", lambda *_a, **_k: {"valid": True, "issues": []})
    result = validate_topic(topic)
    assert not result["complete"]
    assert sum("ratio" in issue for issue in result["issues"]) == 2


def test_empty_topic_reports_all_missing_deliverables(tmp_path: Path) -> None:
    from autoyy.deliverables import validate_topic

    topic = tmp_path / "01-empty"
    topic.mkdir()
    result = validate_topic(topic)
    assert not result["complete"]
    assert any("primary video" in issue for issue in result["issues"])
    assert any("subtitle missing" in issue for issue in result["issues"])
    assert any("publication" in issue for issue in result["issues"])
    assert any("voiceover" in issue for issue in result["issues"])
    assert any("封面-3比4" in issue for issue in result["issues"])
    assert any("封面-4比3" in issue for issue in result["issues"])
