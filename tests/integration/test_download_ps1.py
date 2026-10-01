from __future__ import annotations

import csv
import os
import subprocess
from pathlib import Path

from autoyy.download import DownloadOptions, run_download
from autoyy.manifest import FIELDS

REPO = Path(__file__).resolve().parents[2]


def write_manifest(path: Path, rows: list[dict[str, str]], *, status: bool = False) -> None:
    fields = FIELDS + (["status"] if status else [])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def manifest_row(folder: str, url: str = "https://www.youtube.com/watch?v=ok", status: str = "") -> dict[str, str]:
    row = {field: "" for field in FIELDS}
    row.update({"id": folder[:2], "folder_name": folder, "url": url, "match_grade": "exact", "subtitle_language": "en"})
    if status:
        row["status"] = status
    return row


def make_fake_tools(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    ytdlp_py = tmp_path / "fake_ytdlp.py"
    ytdlp_py.write_text(
        """import json, pathlib, sys\nargs=sys.argv[1:]\nurl=next((x for x in args if x.startswith('https://')), '')\nif 'fail' in url: sys.exit(7)\nif '--dump-single-json' in args:\n print(json.dumps({'subtitles': {'en': [{}]}, 'automatic_captions': {}})); sys.exit(0)\nout=args[args.index('-o')+1]\nif '--skip-download' in args:\n p=pathlib.Path(out.replace('%(ext)s','en.srt')); p.parent.mkdir(parents=True,exist_ok=True); p.write_text('1\\n00:00:00,000 --> 00:00:07,500\\nvalid subtitle text\\n',encoding='utf-8')\nelse:\n p=pathlib.Path(out.replace('%(ext)s','mp4')); p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b'FAKEVIDEO')\n""",
        encoding="utf-8",
    )
    ytdlp_cmd = tmp_path / "fake-ytdlp.cmd"
    ytdlp_cmd.write_text('@echo off\r\npython "%~dp0fake_ytdlp.py" %*\r\n', encoding="ascii")
    ffprobe_py = tmp_path / "fake_ffprobe.py"
    ffprobe_py.write_text("import json; print(json.dumps({'format': {'duration': '8.0'}}))\n", encoding="utf-8")
    ffprobe_cmd = tmp_path / "ffprobe.cmd"
    ffprobe_cmd.write_text('@echo off\r\npython "%~dp0fake_ffprobe.py" %*\r\n', encoding="ascii")
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + os.pathsep + env.get("PATH", "")
    return ytdlp_py, env


def test_unsupported_url_is_row_failure_exit_1(tmp_path: Path, monkeypatch) -> None:
    ytdlp, env = make_fake_tools(tmp_path)
    monkeypatch.setenv("PATH", env["PATH"])
    manifest = tmp_path / "manifest.csv"
    write_manifest(manifest, [manifest_row("01-bad", "https://example.com/video")])
    code, result = run_download(DownloadOptions(manifest, tmp_path / "out", yt_dlp=str(ytdlp)))
    assert code == 1
    assert result["failed_count"] == 1
    assert result["results"][0]["error"] == "unsupported URL"


def test_zero_byte_ready_is_not_skipped(tmp_path: Path, monkeypatch) -> None:
    ytdlp, env = make_fake_tools(tmp_path)
    monkeypatch.setenv("PATH", env["PATH"])
    output = tmp_path / "out"
    topic = output / "01-zero"
    topic.mkdir(parents=True)
    (topic / "高清源视频.mp4").write_bytes(b"")
    (topic / "字幕.srt").write_bytes(b"")
    manifest = tmp_path / "manifest.csv"
    write_manifest(manifest, [manifest_row("01-zero", status="ready")], status=True)
    code, result = run_download(DownloadOptions(manifest, output, yt_dlp=str(ytdlp)))
    assert code == 0
    assert result["results"][0]["status"] == "complete"
    assert result["results"][0]["attempts"] == 1
    assert (topic / "高清源视频.mp4").stat().st_size > 0
    assert (topic / "字幕.srt").stat().st_size > 0


def test_duplicate_and_escape_are_usage_errors(tmp_path: Path, monkeypatch) -> None:
    ytdlp, env = make_fake_tools(tmp_path)
    monkeypatch.setenv("PATH", env["PATH"])
    manifest = tmp_path / "manifest.csv"
    write_manifest(manifest, [manifest_row("01-a"), manifest_row("01-a")])
    code, _ = run_download(DownloadOptions(manifest, tmp_path / "out", yt_dlp=str(ytdlp)))
    assert code == 2
    write_manifest(manifest, [manifest_row("..\\outside")])
    code, _ = run_download(DownloadOptions(manifest, tmp_path / "out2", yt_dlp=str(ytdlp)))
    assert code == 2
    assert not (tmp_path / "outside").exists()


def test_parallel_two_rows_complete(tmp_path: Path, monkeypatch) -> None:
    ytdlp, env = make_fake_tools(tmp_path)
    monkeypatch.setenv("PATH", env["PATH"])
    manifest = tmp_path / "manifest.csv"
    write_manifest(manifest, [manifest_row("01-a", "https://www.youtube.com/watch?v=a"), manifest_row("02-b", "https://www.youtube.com/watch?v=b")])
    code, result = run_download(DownloadOptions(manifest, tmp_path / "out", yt_dlp=str(ytdlp), parallel=2))
    assert code == 0
    assert result["complete_count"] == 2
    assert all(item["status"] == "complete" for item in result["results"])


def test_powershell_wrapper_parallel_exit_semantics(tmp_path: Path) -> None:
    ytdlp, env = make_fake_tools(tmp_path)
    manifest = tmp_path / "manifest.csv"
    write_manifest(manifest, [manifest_row("01-a", "https://www.youtube.com/watch?v=a"), manifest_row("02-b", "https://www.youtube.com/watch?v=b")])
    output = tmp_path / "out"
    powershell = "pwsh" if shutil_which("pwsh") else "powershell.exe"
    command = [
        powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REPO / "scripts" / "download_from_manifest.ps1"),
        "-Manifest", str(manifest), "-OutputRoot", str(output), "-YtDlp", str(ytdlp), "-Parallel", "2",
    ]
    proc = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (output / "下载状态.csv").is_file()


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


def test_download_updates_stage_state(tmp_path: Path, monkeypatch) -> None:
    import json

    ytdlp, env = make_fake_tools(tmp_path)
    monkeypatch.setenv("PATH", env["PATH"])
    manifest = tmp_path / "manifest.csv"
    write_manifest(manifest, [manifest_row("01-a")])
    output = tmp_path / "out"
    code, _ = run_download(DownloadOptions(manifest, output, yt_dlp=str(ytdlp)))
    assert code == 0
    state = json.loads((output / ".autoyy" / "state.json").read_text(encoding="utf-8"))
    stages = state["topics"]["01-a"]["stages"]
    assert stages["source"]["status"] == "ready"
    assert stages["subtitle"]["status"] == "ready"


def test_verified_existing_download_resumes_without_redownload(tmp_path: Path, monkeypatch) -> None:
    ytdlp, env = make_fake_tools(tmp_path)
    monkeypatch.setenv("PATH", env["PATH"])
    manifest = tmp_path / "manifest.csv"
    write_manifest(manifest, [manifest_row("01-a")])
    output = tmp_path / "out"
    first_code, first = run_download(DownloadOptions(manifest, output, yt_dlp=str(ytdlp)))
    assert first_code == 0 and first["results"][0]["attempts"] == 1
    second_code, second = run_download(DownloadOptions(manifest, output, yt_dlp=str(ytdlp)))
    assert second_code == 0
    assert second["results"][0]["status"] == "ready(skip)"
    assert second["results"][0]["attempts"] == 0


def test_invalid_download_options_are_usage_errors(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    write_manifest(manifest, [manifest_row("01-a")])
    code, result = run_download(DownloadOptions(manifest, tmp_path / "out", plan_only=False, parallel=0))
    assert code == 2
    assert "parallel" in result["error"]
