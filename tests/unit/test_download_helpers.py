from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import autoyy.download as dl
from autoyy.manifest import ManifestRow


def row(language: str = "") -> ManifestRow:
    return ManifestRow({"folder_name": "01-a", "url": "https://www.youtube.com/watch?v=x", "subtitle_language": language})


def options(tmp_path: Path, **kwargs) -> dl.DownloadOptions:
    base = {"manifest": tmp_path / "m.csv", "output_root": tmp_path / "out"}
    base.update(kwargs)
    return dl.DownloadOptions(**base)


def proc(code: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["fake"], code, stdout, stderr)


def test_executable_prefix_optional_args_and_ffprobe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "tool.py"
    exe.write_text("", encoding="utf-8")
    assert dl._executable(str(exe)) == str(exe)
    assert dl._command_prefix(str(exe))[1] == str(exe)
    monkeypatch.setattr(dl.shutil, "which", lambda name: f"C:/fake/{name}.exe")
    assert dl._executable("yt-dlp").endswith("yt-dlp.exe")
    opts = options(tmp_path, ffmpeg_location="C:/ff", cookies_from_browser="chrome", proxy="p", js_runtimes="node")
    args = dl._optional_args(opts)
    assert "--cookies-from-browser" in args and "--proxy" in args and "--js-runtimes" in args
    assert dl._ffprobe_path(None).endswith("ffprobe.exe")


def test_run_wraps_os_and_timeout_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dl.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("missing")))
    with pytest.raises(RuntimeError, match="unavailable/timeout"):
        dl._run(["missing"], options(tmp_path))


def test_select_subtitle_language_preference_and_bad_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert dl._select_subtitle_language(row("fr"), options(tmp_path), "fake") == "fr"
    metadata = {"subtitles": {"zh-Hans": [{}], "en": [{}]}, "automatic_captions": {"en-US": [{}]}}
    monkeypatch.setattr(dl, "_run", lambda *_a, **_k: proc(stdout=json.dumps(metadata)))
    assert dl._select_subtitle_language(row(), options(tmp_path), "fake") == "en"
    monkeypatch.setattr(dl, "_run", lambda *_a, **_k: proc(stdout="not-json"))
    assert dl._select_subtitle_language(row(), options(tmp_path), "fake") is None
    monkeypatch.setattr(dl, "_run", lambda *_a, **_k: proc(code=1, stderr="bad"))
    assert dl._select_subtitle_language(row(), options(tmp_path), "fake") is None


def test_verified_ready_rejects_bad_subtitle_and_probe_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    topic = tmp_path / "01-a"
    topic.mkdir()
    (topic / "高清源视频.mp4").write_bytes(b"video")
    (topic / "字幕.srt").write_text("broken", encoding="utf-8")
    assert not dl._verified_ready(topic, "ffprobe")
    (topic / "字幕.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nok\n", encoding="utf-8")
    monkeypatch.setattr(dl, "probe_media", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("bad media")))
    assert not dl._verified_ready(topic, "ffprobe")


def test_download_video_and_subtitle_failure_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    topic = tmp_path / "01-a"
    topic.mkdir()
    opts = options(tmp_path)
    monkeypatch.setattr(dl, "_run", lambda *_a, **_k: proc(code=7, stderr="network"))
    with pytest.raises(RuntimeError, match="video failed"):
        dl._download_video(row(), topic, opts, "fake")
    monkeypatch.setattr(dl, "_select_subtitle_language", lambda *_a, **_k: None)
    with pytest.raises(RuntimeError, match="no usable subtitle"):
        dl._download_subtitle(row(), topic, opts, "fake")


def test_status_writer_is_atomic_csv(tmp_path: Path) -> None:
    path = tmp_path / "status.csv"
    dl._write_status(path, [{"folder_name": "01-a", "url": "u", "video": True, "subtitle": False, "subtitle_language": "", "attempts": 1, "status": "failed", "error": "x"}])
    text = path.read_text(encoding="utf-8-sig")
    assert "folder_name" in text and "01-a" in text
    assert not list(tmp_path.glob("*.tmp-*"))


def test_corrupt_nonzero_artifacts_are_quarantined_and_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    topic = tmp_path / "01-a"
    topic.mkdir()
    video = topic / "高清源视频.mp4"
    video.write_bytes(b"corrupt-but-nonzero")
    subtitle = topic / "字幕.srt"
    subtitle.write_text("broken but nonzero", encoding="utf-8")
    opts = options(tmp_path)

    def fake_probe(path: Path, *_a, **_k):
        if b"corrupt" in Path(path).read_bytes():
            raise RuntimeError("bad media")
        return {"duration": 1.0}

    def fake_run(command, *_a, **_k):
        if "--merge-output-format" in command:
            video.write_bytes(b"fresh-video")
        if "--write-subs" in command:
            (topic / "字幕.en.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nok\n", encoding="utf-8"
            )
        return proc()

    monkeypatch.setattr(dl, "probe_media", fake_probe)
    monkeypatch.setattr(dl, "_run", fake_run)
    monkeypatch.setattr(dl, "_select_subtitle_language", lambda *_a, **_k: "en")
    dl._download_video(row(), topic, opts, "fake", "ffprobe")
    dl._download_subtitle(row(), topic, opts, "fake")
    assert video.read_bytes() == b"fresh-video"
    assert dl.validate_srt(topic / "字幕.srt")["valid"]
    assert list(topic.glob("高清源视频.mp4.invalid*"))
    assert list(topic.glob("字幕.srt.invalid*"))


def test_trace_redacts_proxy_and_cookie_values() -> None:
    command = ["yt-dlp", "--proxy", "http://user:secret@example", "--cookies-from-browser", "chrome", "url"]
    redacted = dl._redact_command(command)
    assert "secret" not in " ".join(redacted)
    assert "chrome" not in " ".join(redacted)
    assert redacted.count("<redacted>") == 2
