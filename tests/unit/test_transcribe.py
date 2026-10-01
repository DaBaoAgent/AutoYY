from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import autoyy.transcribe as tr
from autoyy.subtitles import validate_srt


def args(**overrides) -> argparse.Namespace:
    values = {
        "overwrite": False,
        "dry_run": False,
        "ffmpeg_timeout": 10,
        "device": "cpu",
        "model_size": "tiny",
        "vad": True,
        "beam_size": 1,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_language_normalization_and_backend_preference() -> None:
    assert tr.normalize_language("zh-CN") == "zh"
    assert tr.normalize_language("en-US") == "en"
    assert tr.normalize_language("auto") is None
    assert tr.backend_preference("zh") == ("funasr", "whisper")
    assert tr.backend_preference("en") == ("whisper", "funasr")


def test_invalid_existing_subtitle_is_repaired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "01-topic"
    folder.mkdir()
    (folder / "高清源视频.mp4").write_bytes(b"video")
    (folder / "字幕.srt").write_text("broken", encoding="utf-8")

    monkeypatch.setattr(tr, "resolve_backend", lambda *_a, **_k: "whisper")
    monkeypatch.setattr(
        tr, "extract_audio", lambda _video, wav, _ffmpeg, _timeout: wav.write_bytes(b"audio")
    )
    monkeypatch.setattr(
        tr,
        "transcribe_whisper",
        lambda *_a, **_k: [(0.0, 1.0, "repaired subtitle")],
    )
    result = tr.process_folder(folder, args(), "auto", "fake-ffmpeg", "en")
    assert result["status"] == "ready"
    assert result["backend"] == "whisper"
    assert result["language"] == "en"
    assert validate_srt(folder / "字幕.srt")["valid"]
    assert (folder / "字幕.srt.invalid").is_file()


def test_dry_run_does_not_quarantine_invalid_subtitle(tmp_path: Path) -> None:
    folder = tmp_path / "01-topic"
    folder.mkdir()
    (folder / "高清源视频.mp4").write_bytes(b"video")
    subtitle = folder / "字幕.srt"
    subtitle.write_text("broken", encoding="utf-8")
    result = tr.process_folder(folder, args(dry_run=True), "auto", None, "en")
    assert result["status"] == "pending"
    assert result["reason"] == "invalid_subtitle_repair"
    assert subtitle.is_file()
    assert not (folder / "字幕.srt.invalid").exists()


def test_explicit_non_chinese_funasr_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tr, "_backend_available", lambda _name: True)
    with pytest.raises(RuntimeError, match="cannot transcribe"):
        tr.resolve_backend("funasr", "en")


def test_invalid_subtitle_is_not_quarantined_when_backend_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "01-topic"
    folder.mkdir()
    (folder / "高清源视频.mp4").write_bytes(b"video")
    subtitle = folder / "字幕.srt"
    subtitle.write_text("broken", encoding="utf-8")
    monkeypatch.setattr(
        tr, "resolve_backend", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no backend"))
    )
    result = tr.process_folder(folder, args(), "auto", "fake-ffmpeg", "en")
    assert result["status"] == "failed"
    assert subtitle.is_file()
    assert not (folder / "字幕.srt.invalid").exists()
