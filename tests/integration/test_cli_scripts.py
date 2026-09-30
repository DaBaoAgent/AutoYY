from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def run_script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(REPO / "scripts" / name), *args], cwd=REPO, capture_output=True, text=True, check=False)


def test_empty_deliverables_and_subtitles_fail(tmp_path: Path) -> None:
    deliverables = run_script("validate_deliverables.py", str(tmp_path))
    assert deliverables.returncode == 1
    subtitles = run_script("validate_subtitles.py", str(tmp_path))
    assert subtitles.returncode == 1


def test_empty_publication_root_fails(tmp_path: Path) -> None:
    result = run_script("validate_publication_info.py", str(tmp_path))
    assert result.returncode == 1


def test_invalid_jimeng_title_does_not_write(tmp_path: Path) -> None:
    topic = tmp_path / "01-test"
    topic.mkdir()
    csv_path = tmp_path / "covers.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["目录名", "主标题", "副标题", "竖版画面", "横版画面"])
        writer.writerow(["01-test", "短", "也很短", "竖版场景", "横版场景"])
    result = run_script("gen_jimeng_cover_prompts.py", str(tmp_path), "--csv", str(csv_path))
    assert result.returncode == 1
    assert not (topic / "封面提示词-即梦.txt").exists()


def test_valid_jimeng_prompt_is_not_overwritten_without_force(tmp_path: Path) -> None:
    topic = tmp_path / "01-test"
    topic.mkdir()
    csv_path = tmp_path / "covers.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["目录名", "主标题", "副标题", "竖版画面", "横版画面"])
        writer.writerow(["01-test", "一二三四五六", "一二三四五六七八", "竖版场景", "横版场景"])
    first = run_script("gen_jimeng_cover_prompts.py", str(tmp_path), "--csv", str(csv_path))
    assert first.returncode == 0
    path = topic / "封面提示词-即梦.txt"
    original = path.read_text(encoding="utf-8")
    path.write_text("approved", encoding="utf-8")
    second = run_script("gen_jimeng_cover_prompts.py", str(tmp_path), "--csv", str(csv_path))
    assert second.returncode == 0
    assert path.read_text(encoding="utf-8") == "approved"
    path.write_text(original, encoding="utf-8")


def test_jimeng_dry_run_does_not_write_prompt_or_state(tmp_path: Path) -> None:
    topic = tmp_path / "01-test"
    topic.mkdir()
    csv_path = tmp_path / "covers.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["目录名", "主标题", "副标题", "竖版画面", "横版画面"])
        writer.writerow(["01-test", "一二三四五六", "一二三四五六七八", "竖版场景", "横版场景"])
    result = run_script("gen_jimeng_cover_prompts.py", str(tmp_path), "--csv", str(csv_path), "--dry-run")
    assert result.returncode == 0
    assert not (topic / "封面提示词-即梦.txt").exists()
    assert not (tmp_path / ".autoyy" / "state.json").exists()


def test_transcribe_dry_run_does_not_write_state(tmp_path: Path) -> None:
    topic = tmp_path / "01-test"
    topic.mkdir()
    (topic / "高清源视频.mp4").write_bytes(b"not-a-real-video")
    result = run_script("transcribe.py", str(tmp_path), "--dry-run")
    assert result.returncode == 0
    assert "pending" in result.stdout
    assert not (tmp_path / ".autoyy" / "state.json").exists()


def test_transcribe_missing_video_fails_batch(tmp_path: Path) -> None:
    (tmp_path / "01-test").mkdir()
    result = run_script("transcribe.py", str(tmp_path), "--backend", "whisper")
    assert result.returncode == 1
    assert "video_missing" in result.stdout
