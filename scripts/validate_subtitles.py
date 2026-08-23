#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕质量工程：校验 SRT 语法、时间轴顺序、与视频时长的对齐漂移。

用法:
  python scripts/validate_subtitles.py <output-root> [--manifest 清单.csv]
       [--max-drift-pct 5] [--ffprobe-location PATH] [--json-out 报告.json]

检查项（每个选题目录）:
  1. SRT 存在且非空（无字幕目录标记 missing）
  2. SRT 语法：编号连续、时间戳格式 HH:MM:SS,mmm、开始时间非递减
  3. 字幕-视频对齐：最后一条字幕结束时间 与 视频时长 的相对漂移
     超过 --max-drift-pct 报警（防止 yt-dlp 字幕与视频版本错配）
  4. 无视频但有字幕 / 有视频但字幕为空 等异常组合

退出码: 0 = 全部通过; 1 = 存在告警; 2 = 运行错误
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts"}
TIME_RE = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2}),(\d{3})$"
)


def ts_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def video_duration(path: Path, ffprobe: str) -> float | None:
    """ffprobe 取视频时长（秒）。失败返回 None。"""
    cmd = [
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def find_video(folder: Path) -> Path | None:
    for ext in VIDEO_EXTENSIONS:
        cand = folder / f"高清源视频{ext}"
        if cand.is_file() and cand.stat().st_size > 0:
            return cand
    videos = [
        item for item in folder.iterdir()
        if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS
        and item.stat().st_size > 0
    ]
    return max(videos, key=lambda p: p.stat().st_size) if videos else None


def parse_srt(path: Path) -> tuple[list[float], list[float], list[str]]:
    """返回 (starts, ends, issues)。时间戳用毫秒精度秒。"""
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    raw = raw.lstrip("\ufeff").strip()
    if not raw:
        return [], [], ["空字幕文件"]
    blocks = re.split(r"\n\s*\n", raw)
    starts: list[float] = []
    ends: list[float] = []
    issues: list[str] = []
    for idx, block in enumerate(blocks, start=1):
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if not lines:
            continue
        # 编号行
        if not re.fullmatch(r"\d+", lines[0].strip()):
            issues.append(f"块 {idx}: 编号行异常 ({lines[0]!r})")
        # 时间轴行（可能带 HTML 标签，如 <i>）
        time_line = next((ln for ln in lines[1:] if "-->" in ln), None)
        if time_line is None:
            issues.append(f"块 {idx}: 缺少时间轴行")
            continue
        match = TIME_RE.match(time_line.strip())
        if not match:
            issues.append(f"块 {idx}: 时间戳格式异常 ({time_line.strip()!r})")
            continue
        g = match.groups()
        start = ts_to_seconds(*g[:4])
        end = ts_to_seconds(*g[4:])
        if end < start:
            issues.append(f"块 {idx}: 结束时间早于开始时间")
        starts.append(start)
        ends.append(end)
        # 文本非空
        text_lines = [ln for ln in lines[1:] if "-->" not in ln and ln.strip()]
        if not text_lines:
            issues.append(f"块 {idx}: 无文本内容")
    # 时间轴递增
    for i in range(1, len(starts)):
        if starts[i] < ends[i - 1] - 0.001:
            issues.append(f"块 {i + 1}: 开始时间早于上一条结束（重叠 {ends[i-1]-starts[i]:.2f}s）")
    return starts, ends, issues


def validate_folder(folder: Path, max_drift_pct: float, ffprobe: str) -> dict:
    name = folder.name
    issues: list[str] = []
    video = find_video(folder)
    srts = sorted(
        item for item in folder.glob("*.srt")
        if item.is_file() and item.stat().st_size > 0
    )

    if video is None:
        issues.append("无视频文件")
    if not srts:
        issues.append("无字幕文件")
        return {"folder": name, "valid": False, "issues": issues,
                "subtitle_end": None, "video_duration": None, "drift_pct": None}

    # 主字幕：优先 字幕.srt，否则取第一个
    main_srt = next((p for p in srts if p.name == "字幕.srt"), srts[0])
    starts, ends, srt_issues = parse_srt(main_srt)
    issues.extend(f"字幕 {main_srt.name}: {msg}" for msg in srt_issues)

    duration = video_duration(video, ffprobe) if video else None
    drift_pct: float | None = None
    if ends and duration:
        drift_pct = abs(ends[-1] - duration) / duration * 100.0
        if drift_pct > max_drift_pct:
            issues.append(
                f"字幕-视频对齐漂移 {drift_pct:.1f}% (字幕末条 {ends[-1]:.0f}s vs 视频 {duration:.0f}s)，"
                f"超过阈值 {max_drift_pct}%，字幕可能与视频版本不匹配"
            )

    return {
        "folder": name,
        "valid": not issues,
        "subtitle_blocks": len(ends),
        "subtitle_end": round(ends[-1], 1) if ends else None,
        "video_duration": round(duration, 1) if duration else None,
        "drift_pct": round(drift_pct, 2) if drift_pct is not None else None,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AutoYY 字幕质量校验：语法 + 时间轴 + 与视频对齐漂移",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--manifest", type=Path,
                        help="可选 manifest CSV，按 folder_name 列限定检查目录")
    parser.add_argument("--max-drift-pct", type=float, default=5.0,
                        help="字幕与视频时长允许的最大相对漂移百分比（默认 5）")
    parser.add_argument("--ffprobe-location", help="ffprobe 所在目录（默认 PATH 探测）")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    root = args.output_root.resolve()
    if not root.is_dir():
        print(f"目录不存在: {root}", file=sys.stderr)
        return 2

    ffprobe = shutil.which("ffprobe")
    if args.ffprobe_location:
        cand = Path(args.ffprobe_location) / "ffprobe.exe"
        if cand.is_file():
            ffprobe = str(cand)
    if not ffprobe:
        print("未找到 ffprobe，请用 --ffprobe-location 指定 "
              "（如 C:\\Users\\xxx13\\ffmpeg\\ffmpeg-8.1.1-essentials_build\\bin）", file=sys.stderr)
        return 2

    if args.manifest is not None:
        import csv
        folders: list[Path] = []
        with open(args.manifest, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                fname = (row.get("folder_name") or "").strip()
                if fname:
                    folders.append(root / fname)
    else:
        folders = sorted(
            item for item in root.iterdir()
            if item.is_dir() and not item.name.startswith((".", "@"))
        )

    results = [
        validate_folder(folder, args.max_drift_pct, ffprobe)
        for folder in folders
    ]
    summary = {
        "root": str(root),
        "folder_count": len(results),
        "valid_count": sum(item["valid"] for item in results),
        "invalid_count": sum(not item["valid"] for item in results),
        "results": results,
    }
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if summary["invalid_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
