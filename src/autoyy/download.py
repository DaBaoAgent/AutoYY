from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .manifest import ManifestRow, is_supported_youtube_url, load_manifest
from .media import find_primary_subtitle, find_primary_video, probe_media
from .paths import safe_child_path
from .state import file_fingerprint, recover_running, set_stage, update_state
from .subtitles import validate_srt


@dataclass(slots=True)
class DownloadOptions:
    manifest: Path
    output_root: Path
    yt_dlp: str = "yt-dlp"
    ffmpeg_location: str | None = None
    cookies_from_browser: str | None = None
    proxy: str | None = None
    min_height: int = 720
    max_height: int = 1080
    plan_only: bool = False
    parallel: int = 1
    use_aria2: bool = False
    aria2_connections: int = 8
    trace: bool = False
    js_runtimes: str | None = None
    command_timeout: int = 7200


def _executable(value: str) -> str | None:
    path = Path(value)
    if path.is_file():
        return str(path)
    return shutil.which(value)



def _command_prefix(executable: str) -> list[str]:
    return [sys.executable, executable] if Path(executable).suffix.lower() == ".py" else [executable]

def _ffprobe_path(ffmpeg_location: str | None) -> str | None:
    if ffmpeg_location:
        base = Path(ffmpeg_location)
        candidate = base / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffprobe")


def _optional_args(options: DownloadOptions) -> list[str]:
    args: list[str] = []
    if options.ffmpeg_location:
        args += ["--ffmpeg-location", options.ffmpeg_location]
    if options.cookies_from_browser:
        args += ["--cookies-from-browser", options.cookies_from_browser]
    if options.proxy:
        args += ["--proxy", options.proxy]
    if options.js_runtimes:
        args += ["--js-runtimes", options.js_runtimes]
    return args


def _redact_command(command: list[str]) -> list[str]:
    redacted = list(command)
    sensitive = {"--proxy", "--cookies", "--cookies-from-browser"}
    for index, token in enumerate(redacted[:-1]):
        if token in sensitive:
            redacted[index + 1] = "<redacted>"
    return redacted


def _run(command: list[str], options: DownloadOptions) -> subprocess.CompletedProcess[str]:
    if options.trace:
        print("TRACE>", subprocess.list2cmdline(_redact_command(command)))
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=options.command_timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"external command unavailable/timeout: {exc}") from exc


def _select_subtitle_language(row: ManifestRow, options: DownloadOptions, yt_dlp: str) -> str | None:
    requested = (row.data.get("subtitle_language") or "").strip()
    if requested:
        return requested
    command = [*_command_prefix(yt_dlp), "--dump-single-json", "--skip-download", "--no-playlist", "--no-warnings", row.url, *_optional_args(options)]
    proc = _run(command, options)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        metadata = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    manual = [key for key in (metadata.get("subtitles") or {}) if key != "live_chat"]
    automatic = [key for key in (metadata.get("automatic_captions") or {}) if key != "live_chat"]
    groups = [
        [x for x in manual if x == "en"], [x for x in manual if x.startswith("en")],
        [x for x in automatic if x == "en"], [x for x in automatic if x.startswith("en")],
        [x for x in manual if x.startswith("zh")], [x for x in automatic if x.startswith("zh")],
        manual, automatic,
    ]
    for group in groups:
        if group:
            return group[0]
    return None


def _verified_ready(topic_dir: Path, ffprobe: str | None) -> bool:
    video = find_primary_video(topic_dir)
    subtitle = find_primary_subtitle(topic_dir)
    if video is None or subtitle is None:
        return False
    report = validate_srt(subtitle)
    if not report["valid"]:
        return False
    if ffprobe:
        try:
            probe_media(video, ffprobe, timeout=60)
        except RuntimeError:
            return False
    return True


def _quarantine(path: Path) -> Path:
    target = path.with_name(path.name + ".invalid")
    counter = 1
    while target.exists():
        target = path.with_name(path.name + f".invalid-{counter}")
        counter += 1
    path.replace(target)
    return target


def _video_valid(topic_dir: Path, ffprobe: str | None) -> bool:
    video = find_primary_video(topic_dir)
    if video is None or not ffprobe:
        return False
    try:
        probe_media(video, ffprobe, timeout=60)
    except RuntimeError:
        return False
    return True


def _subtitle_valid(topic_dir: Path) -> bool:
    subtitle = find_primary_subtitle(topic_dir)
    return subtitle is not None and validate_srt(subtitle)["valid"]


def _download_video(row: ManifestRow, topic_dir: Path, options: DownloadOptions, yt_dlp: str, ffprobe: str | None = None) -> None:
    existing = find_primary_video(topic_dir)
    if existing is not None:
        if _video_valid(topic_dir, ffprobe):
            return
        _quarantine(existing)
    command = [
        *_command_prefix(yt_dlp), "--continue", "--no-overwrites", "--no-playlist", "--retries", "10", "--fragment-retries", "10",
        "--concurrent-fragments", "8", "--embed-metadata", "--merge-output-format", "mp4", "--remux-video", "mp4",
        "-f", f"bestvideo[height<={options.max_height}][height>={options.min_height}]+bestaudio/best[height<={options.max_height}][height>={options.min_height}]",
        "-o", str(topic_dir / "高清源视频.%(ext)s"), row.url, *_optional_args(options),
    ]
    if options.use_aria2:
        aria2 = shutil.which("aria2c")
        if aria2:
            command += ["--downloader", aria2, "--downloader-args", f"aria2c:--max-connection-per-server={options.aria2_connections} --split={options.aria2_connections} --min-split-size=1M --file-allocation=none"]
    proc = _run(command, options)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp video failed ({proc.returncode}): {proc.stderr.strip()[:300]}")
    if find_primary_video(topic_dir) is None:
        raise RuntimeError("yt-dlp reported success but no non-empty video exists")


def _download_subtitle(row: ManifestRow, topic_dir: Path, options: DownloadOptions, yt_dlp: str) -> str:
    existing = find_primary_subtitle(topic_dir)
    if existing is not None:
        if _subtitle_valid(topic_dir):
            return (row.data.get("subtitle_language") or "").strip()
        _quarantine(existing)
    language = _select_subtitle_language(row, options, yt_dlp)
    if not language:
        raise RuntimeError("no usable subtitle track found")
    command = [
        *_command_prefix(yt_dlp), "--skip-download", "--no-playlist", "--write-subs", "--write-auto-subs",
        "--sub-langs", language, "--convert-subs", "srt", "-o", str(topic_dir / "字幕.%(ext)s"), row.url,
        *_optional_args(options),
    ]
    proc = _run(command, options)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp subtitle failed ({proc.returncode}): {proc.stderr.strip()[:300]}")
    generated = sorted(topic_dir.glob("字幕*.srt"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if not generated:
        raise RuntimeError("yt-dlp reported success but no SRT exists")
    target = topic_dir / "字幕.srt"
    source = generated[0]
    if source != target:
        temp_target = target.with_suffix(".tmp")
        temp_target.write_bytes(source.read_bytes())
        os.replace(temp_target, target)
    if not validate_srt(target)["valid"]:
        raise RuntimeError("downloaded subtitle failed SRT validation")
    return language


def process_row(row: ManifestRow, options: DownloadOptions, yt_dlp: str, ffprobe: str | None) -> dict[str, object]:
    if not is_supported_youtube_url(row.url):
        return {"folder_name": row.folder_name, "url": row.url, "video": False, "subtitle": False, "subtitle_language": "", "attempts": 1, "status": "failed", "error": "unsupported URL"}
    topic_dir = safe_child_path(options.output_root, row.folder_name)
    topic_dir.mkdir(parents=True, exist_ok=True)
    if _verified_ready(topic_dir, ffprobe):
        return {"folder_name": row.folder_name, "url": row.url, "video": True, "subtitle": True, "subtitle_language": row.data.get("subtitle_language", ""), "attempts": 0, "status": "ready(skip)"}
    try:
        _download_video(row, topic_dir, options, yt_dlp, ffprobe)
        language = _download_subtitle(row, topic_dir, options, yt_dlp)
        ready = _verified_ready(topic_dir, ffprobe)
        return {
            "folder_name": row.folder_name,
            "url": row.url,
            "video": _video_valid(topic_dir, ffprobe),
            "subtitle": _subtitle_valid(topic_dir),
            "subtitle_language": language,
            "attempts": 1,
            "status": "complete" if ready else "incomplete",
            "error": "" if ready else "media/subtitle verification failed",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "folder_name": row.folder_name,
            "url": row.url,
            "video": _video_valid(topic_dir, ffprobe),
            "subtitle": _subtitle_valid(topic_dir),
            "subtitle_language": (row.data.get("subtitle_language") or "").strip(),
            "attempts": 1,
            "status": "failed",
            "error": str(exc)[:500],
        }


def _write_status(path: Path, results: list[dict[str, object]]) -> None:
    fields = ["folder_name", "url", "video", "subtitle", "subtitle_language", "attempts", "status", "error"]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    os.replace(tmp, path)


def _apply_result_state(state: dict, options: DownloadOptions, item: dict[str, object]) -> None:
    topic_dir = safe_child_path(options.output_root, str(item.get("folder_name", "")))
    video = find_primary_video(topic_dir) if topic_dir.is_dir() else None
    subtitle = find_primary_subtitle(topic_dir) if topic_dir.is_dir() else None
    complete = item.get("status") in {"complete", "ready(skip)"}
    video_ready = item.get("video") is True and complete
    subtitle_ready = item.get("subtitle") is True and complete
    error = str(item.get("error", ""))
    set_stage(
        state, topic_dir.name, "source", "ready" if video_ready else "failed",
        fingerprint=file_fingerprint(video) if video_ready and video else "missing",
        reason="" if video_ready else (error or "source verification failed"),
    )
    set_stage(
        state, topic_dir.name, "subtitle", "ready" if subtitle_ready else "failed",
        fingerprint=file_fingerprint(subtitle) if subtitle_ready and subtitle else "missing",
        reason="" if subtitle_ready else (error or "subtitle verification failed"),
    )


def _checkpoint_result(options: DownloadOptions, results: list[dict[str, object]], item: dict[str, object]) -> None:
    results.append(item)
    update_state(options.output_root, lambda state: _apply_result_state(state, options, item), create=True)
    _write_status(options.output_root / "下载状态.csv", sorted(results, key=lambda row: str(row.get("folder_name", ""))))


def run_download(options: DownloadOptions) -> tuple[int, dict[str, object]]:
    options.output_root = options.output_root.resolve()
    try:
        rows = load_manifest(options.manifest, output_root=options.output_root, validate_urls=False)
    except (OSError, ValueError) as exc:
        return 2, {"valid": False, "error": str(exc), "results": []}
    if options.parallel < 1 or options.parallel > 8:
        return 2, {"valid": False, "error": "parallel must be between 1 and 8", "results": []}
    if options.aria2_connections < 1 or options.aria2_connections > 16:
        return 2, {"valid": False, "error": "aria2_connections must be between 1 and 16", "results": []}
    if options.min_height < 144 or options.max_height > 4320 or options.min_height > options.max_height:
        return 2, {"valid": False, "error": "invalid min/max height range", "results": []}
    if options.ffmpeg_location and not Path(options.ffmpeg_location).is_dir():
        return 2, {"valid": False, "error": f"ffmpeg location not found: {options.ffmpeg_location}", "results": []}
    yt_dlp = _executable(options.yt_dlp)
    if not options.plan_only and not yt_dlp:
        return 2, {"valid": False, "error": f"yt-dlp executable not found: {options.yt_dlp}", "results": []}
    if not options.js_runtimes:
        if shutil.which("node"):
            options.js_runtimes = "node"
        elif shutil.which("deno"):
            options.js_runtimes = "deno"
    if options.plan_only:
        plans = [{"folder_name": row.folder_name, "url": row.url, "topic_dir": str(safe_child_path(options.output_root, row.folder_name))} for row in rows]
        return 0, {"valid": True, "planned": len(plans), "results": plans}
    options.output_root.mkdir(parents=True, exist_ok=True)
    try:
        update_state(options.output_root, lambda state: recover_running(state), create=True)
    except (OSError, ValueError) as exc:
        return 2, {"valid": False, "error": str(exc), "results": []}
    ffprobe = _ffprobe_path(options.ffmpeg_location)
    if not ffprobe:
        return 2, {"valid": False, "error": "ffprobe executable not found; media completion cannot be verified", "results": []}
    results: list[dict[str, object]] = []
    interrupted = False
    try:
        if options.parallel <= 1:
            for row in rows:
                _checkpoint_result(options, results, process_row(row, options, yt_dlp, ffprobe))
        else:
            with ThreadPoolExecutor(max_workers=min(options.parallel, 8), thread_name_prefix="autoyy-download") as pool:
                futures = {pool.submit(process_row, row, options, yt_dlp, ffprobe): row.folder_name for row in rows}
                for future in as_completed(futures):
                    _checkpoint_result(options, results, future.result())
    except KeyboardInterrupt:
        interrupted = True
    finally:
        results.sort(key=lambda item: str(item.get("folder_name", "")))
        _write_status(options.output_root / "下载状态.csv", results)
    failed = sum(item.get("status") not in {"complete", "ready(skip)"} for item in results)
    if interrupted:
        failed += 1
    summary = {
        "valid": failed == 0 and len(results) == len(rows),
        "topic_count": len(rows),
        "complete_count": sum(item.get("status") in {"complete", "ready(skip)"} for item in results),
        "failed_count": failed,
        "interrupted": interrupted,
        "status_csv": str(options.output_root / "下载状态.csv"),
        "results": results,
    }
    return (0 if summary["valid"] else 1), summary
