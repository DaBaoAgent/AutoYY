#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 ASR 兜底转录：视频没有可用字幕时，用本地语音识别生成 字幕.srt。

后端优先级（--backend auto 自动探测）：
  1. funasr       阿里 FunASR paraformer-zh，中文纪录片效果最佳（需 pip install funasr）
  2. faster-whisper 已随本机 Python 预装，CPU int8 即可跑；模型经 HF 镜像 hf-mirror.com 下载

用法:
  python scripts/transcribe.py <output-root> [--manifest 清单.csv]
       [--backend auto|funasr|whisper] [--model-size medium] [--language zh]
       [--device cpu] [--ffmpeg-location PATH] [--vad] [--beam-size 5]
       [--only-missing] [--overwrite] [--dry-run]

行为:
  - 扫描 output-root 下每个子目录（或 --manifest 的 folder_name 列）
  - 目录里有视频但没有可用 .srt 时：ffmpeg 抽 16k 单声道 wav -> ASR -> 写 字幕.srt
  - 已有非空 .srt 的目录默认跳过（断点续跑；--overwrite 强制重转）
  - 有失败目录时退出码为 1，其余为 0

输出 SRT 规范:
  - 每条字幕一句（过长按中文标点折行，单行尽量 <= 42 字）
  - 时间戳 HH:MM:SS,mmm，编号从 1 递增，UTF-8 无 BOM
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows 终端中文输出兜底
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts"}
# 国内访问 HuggingFace 走镜像（faster-whisper 模型下载）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

_BACKEND_CACHE: dict[str, object] = {}


def fmt_ts(seconds: float) -> str:
    """秒 -> SRT 时间戳 HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def split_long_line(text: str, limit: int = 42) -> list[str]:
    """长句按中文/英文标点折行，避免 SRT 单行过长。

    优先在句末标点（。！？）断开，其次逗号/顿号，最后硬切。
    """
    if len(text) <= limit:
        return [text]
    text = text.strip()
    pieces: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        # 从后往前找句末/句中标点
        cut = -1
        for idx in range(len(window) - 1, -1, -1):
            if window[idx] in "。！？!?；;":
                cut = idx + 1
                break
        if cut < 0:
            for idx in range(len(window) - 1, -1, -1):
                if window[idx] in "，,、：: ":
                    cut = idx + 1
                    break
        if cut < 0:
            cut = limit
        pieces.append(window[:cut].strip())
        text = text[cut:].strip()
    if text:
        pieces.append(text)
    return [p for p in pieces if p]


def write_srt(path: Path, segments: list[tuple[float, float, str]]) -> None:
    """segments: [(start, end, text), ...] -> UTF-8 SRT 文件"""
    blocks: list[str] = []
    for idx, (start, end, text) in enumerate(segments, start=1):
        if not text.strip():
            continue
        lines = split_long_line(text)
        body = "\n".join(lines)
        blocks.append(f"{idx}\n{fmt_ts(start)} --> {fmt_ts(end)}\n{body}")
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def find_video(folder: Path) -> Path | None:
    """规范文件名优先（高清源视频.*），其次目录中最大的视频文件。"""
    for ext in VIDEO_EXTENSIONS:
        cand = folder / f"高清源视频{ext}"
        if cand.is_file() and cand.stat().st_size > 0:
            return cand
    videos = [
        item for item in folder.iterdir()
        if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS
        and item.stat().st_size > 0
    ]
    if not videos:
        return None
    return max(videos, key=lambda p: p.stat().st_size)


def find_existing_srt(folder: Path) -> Path | None:
    for item in folder.glob("*.srt"):
        if item.is_file() and item.stat().st_size > 0:
            return item
    return None


def extract_audio(video: Path, wav_path: Path, ffmpeg: str) -> None:
    """抽 16k 单声道 wav（ASR 标准输入）。"""
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000",
        str(wav_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg 抽音频失败: {proc.stderr.strip()[:300]}")


def get_backend(name: str) -> str:
    """解析 --backend：auto 时优先 funasr，其次 faster-whisper。"""
    if name != "auto":
        return name
    try:
        import funasr  # noqa: F401
        return "funasr"
    except ImportError:
        return "whisper"


def transcribe_whisper(
    wav_path: Path, model_size: str, language: str, device: str,
    vad: bool, beam_size: int,
) -> list[tuple[float, float, str]]:
    from faster_whisper import WhisperModel

    if "whisper_model" not in _BACKEND_CACHE:
        compute_type = "int8" if device == "cpu" else "float16"
        print(f"  加载 faster-whisper 模型 {model_size} ({device}/{compute_type})，首次运行自动下载…")
        _BACKEND_CACHE["whisper_model"] = WhisperModel(
            model_size, device=device, compute_type=compute_type,
        )
    model = _BACKEND_CACHE["whisper_model"]
    segments, _info = model.transcribe(
        str(wav_path), language=language, beam_size=beam_size,
        vad_filter=vad, without_timestamps=False,
    )
    out: list[tuple[float, float, str]] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if text:
            out.append((float(seg.start), float(seg.end), text))
    return out


def transcribe_funasr(
    wav_path: Path, device: str,
) -> list[tuple[float, float, str]]:
    from funasr import AutoModel

    if "funasr_model" not in _BACKEND_CACHE:
        print(f"  加载 FunASR paraformer-zh (device={device})，首次运行自动下载…")
        _BACKEND_CACHE["funasr_model"] = AutoModel(
            model="paraformer-zh",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            device=device,
            disable_update=True,
        )
    model = _BACKEND_CACHE["funasr_model"]
    res = model.generate(input=str(wav_path), batch_size_s=300)
    out: list[tuple[float, float, str]] = []
    if res:
        sentence_info = res[0].get("sentence_info") or []
        for item in sentence_info:
            text = (item.get("text") or "").strip()
            if text:
                out.append((float(item["start"]) / 1000.0, float(item["end"]) / 1000.0, text))
    return out


def process_folder(
    folder: Path, backend: str, model_size: str, language: str,
    device: str, ffmpeg: str, vad: bool, beam_size: int,
    overwrite: bool, dry_run: bool,
) -> tuple[str, str]:
    """返回 (folder_name, 结果状态)。状态: OK / SKIP / FAIL。"""
    name = folder.name
    video = find_video(folder)
    if video is None:
        return name, "SKIP(无视频)"
    existing = find_existing_srt(folder)
    if existing is not None and not overwrite:
        return name, f"SKIP(已有字幕 {existing.name})"

    if dry_run:
        return name, "DRY(待转写)"

    try:
        with tempfile.TemporaryDirectory(prefix="autoyy_asr_") as tmp:
            wav_path = Path(tmp) / "audio.wav"
            print(f"  [{name}] 抽音频 {video.name} …")
            extract_audio(video, wav_path, ffmpeg)
            if backend == "funasr":
                segments = transcribe_funasr(wav_path, device)
            else:
                segments = transcribe_whisper(
                    wav_path, model_size, language, device, vad, beam_size,
                )
            if not segments:
                return name, "FAIL(未识别到语音)"
            target = folder / "字幕.srt"
            write_srt(target, segments)
            print(f"  [{name}] OK: {len(segments)} 条字幕 -> {target.name}")
            return name, "OK"
    except Exception as exc:  # noqa: BLE001
        print(f"  [{name}] 错误: {exc}")
        return name, f"FAIL({exc})"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AutoYY 本地 ASR 兜底转录：无字幕视频 -> 字幕.srt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("output_root", type=Path, help="选题目录根（或 manifest 所在根）")
    parser.add_argument("--manifest", type=Path, help="可选 manifest CSV，按 folder_name 列限定处理目录")
    parser.add_argument("--backend", choices=["auto", "funasr", "whisper"], default="auto",
                        help="ASR 后端（默认 auto：有 funasr 用 funasr，否则 faster-whisper）")
    parser.add_argument("--model-size", default="medium",
                        help="faster-whisper 模型大小 small/base/medium/large-v3（默认 medium；质量优先用 large-v3，CPU 慢可夜间跑）")
    parser.add_argument("--language", default="zh", help="识别语言（默认 zh）")
    parser.add_argument("--device", default="cpu", help="cpu 或 cuda（本机 torch 为 CPU 版）")
    parser.add_argument("--ffmpeg-location", help="ffmpeg/ffprobe 所在目录（默认 PATH 探测）")
    parser.add_argument("--vad", action="store_true", default=True,
                        help="启用 VAD 滤静音（默认开，纪录片人声更干净）")
    parser.add_argument("--no-vad", dest="vad", action="store_false")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--overwrite", action="store_true", help="已有字幕也强制重转")
    parser.add_argument("--dry-run", action="store_true", help="只列出待转写目录不执行")
    args = parser.parse_args()

    root = args.output_root.resolve()
    if not root.is_dir():
        print(f"目录不存在: {root}", file=sys.stderr)
        return 2

    ffmpeg = shutil.which("ffmpeg")
    if args.ffmpeg_location:
        cand = Path(args.ffmpeg_location) / "ffmpeg.exe"
        if cand.is_file():
            ffmpeg = str(cand)
    if not ffmpeg:
        print("未找到 ffmpeg，请用 --ffmpeg-location 指定（如 C:\\Users\\xxx13\\ffmpeg\\ffmpeg-8.1.1-essentials_build\\bin）",
              file=sys.stderr)
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
    if not folders:
        print("没有可处理的目录", file=sys.stderr)
        return 2

    backend = get_backend(args.backend)
    print(f"后端: {backend} | 模型: {args.model_size} | 语言: {args.language} | 设备: {args.device}")
    results: list[tuple[str, str]] = []
    for folder in folders:
        results.append(process_folder(
            folder, backend, args.model_size, args.language, args.device,
            ffmpeg, args.vad, args.beam_size, args.overwrite, args.dry_run,
        ))

    ok = [n for n, s in results if s == "OK"]
    skipped = [n for n, s in results if s.startswith("SKIP") or s.startswith("DRY")]
    failed = [n for n, s in results if s.startswith("FAIL")]
    for name, status in results:
        print(f"  {status:24s} {name}")
    print(f"\n汇总: OK {len(ok)} | 跳过 {len(skipped)} | 失败 {len(failed)} / 共 {len(results)}")
    if failed:
        print("失败目录: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
