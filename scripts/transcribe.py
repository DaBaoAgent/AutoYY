#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autoyy.manifest import load_manifest
from autoyy.media import find_primary_subtitle, find_primary_video
from autoyy.paths import discover_topics, safe_child_path
from autoyy.result import EXIT_FAILED, EXIT_OK, EXIT_USAGE
from autoyy.state import file_fingerprint, load_state, recover_running, save_state, set_stage

_BACKEND_CACHE: dict[str, object] = {}


def fmt_ts(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(milliseconds, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def split_long_line(text: str, limit: int = 42) -> list[str]:
    text = text.strip()
    out: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        cut = max((window.rfind(ch) + 1 for ch in "。！？!?；;"), default=0)
        if cut <= 0:
            cut = max((window.rfind(ch) + 1 for ch in "，,、：: "), default=0)
        cut = cut or limit
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        out.append(text)
    return [item for item in out if item]


def write_srt_atomic(path: Path, segments: list[tuple[float, float, str]]) -> None:
    blocks = []
    for index, (start, end, text) in enumerate(segments, 1):
        body = "\n".join(split_long_line(text))
        if body:
            blocks.append(f"{index}\n{fmt_ts(start)} --> {fmt_ts(end)}\n{body}")
    if not blocks:
        raise RuntimeError("ASR returned no subtitle blocks")
    tmp = path.with_suffix(".srt.tmp")
    tmp.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def resolve_backend(name: str) -> str:
    if name == "funasr":
        try:
            import funasr  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("FunASR unavailable; install .[asr-funasr]") from exc
        return "funasr"
    if name == "whisper":
        try:
            import faster_whisper  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("faster-whisper unavailable; install .[asr-whisper]") from exc
        return "whisper"
    try:
        import funasr  # noqa: F401
        return "funasr"
    except ImportError:
        try:
            import faster_whisper  # noqa: F401
            return "whisper"
        except ImportError as exc:
            raise RuntimeError("No ASR backend; install .[asr-funasr] or .[asr-whisper]") from exc


def extract_audio(video: Path, wav_path: Path, ffmpeg: str, timeout: int) -> None:
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"ffmpeg unavailable/timeout: {exc}") from exc
    if proc.returncode != 0 or not wav_path.is_file() or wav_path.stat().st_size <= 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {proc.stderr.strip()[:300]}")


def transcribe_whisper(wav_path: Path, model_size: str, language: str, device: str, vad: bool, beam_size: int) -> list[tuple[float, float, str]]:
    from faster_whisper import WhisperModel
    key = f"whisper:{model_size}:{device}"
    if key not in _BACKEND_CACHE:
        compute_type = "int8" if device == "cpu" else "float16"
        _BACKEND_CACHE[key] = WhisperModel(model_size, device=device, compute_type=compute_type)
    model = _BACKEND_CACHE[key]
    segments, _ = model.transcribe(str(wav_path), language=language, beam_size=beam_size, vad_filter=vad, without_timestamps=False)
    return [(float(seg.start), float(seg.end), (seg.text or "").strip()) for seg in segments if (seg.text or "").strip()]


def transcribe_funasr(wav_path: Path, device: str) -> list[tuple[float, float, str]]:
    from funasr import AutoModel
    key = f"funasr:{device}"
    if key not in _BACKEND_CACHE:
        _BACKEND_CACHE[key] = AutoModel(model="paraformer-zh", vad_model="fsmn-vad", punc_model="ct-punc", device=device, disable_update=True)
    result = _BACKEND_CACHE[key].generate(input=str(wav_path), batch_size_s=300)
    out: list[tuple[float, float, str]] = []
    if result:
        for item in result[0].get("sentence_info") or []:
            text = (item.get("text") or "").strip()
            if text:
                out.append((float(item["start"]) / 1000.0, float(item["end"]) / 1000.0, text))
    return out


def process_folder(folder: Path, args: argparse.Namespace, backend: str, ffmpeg: str) -> dict:
    if not folder.is_dir():
        return {"folder": folder.name, "status": "failed", "reason": "topic_directory_missing"}
    video = find_primary_video(folder)
    if video is None:
        return {"folder": folder.name, "status": "failed", "reason": "video_missing"}
    existing = find_primary_subtitle(folder)
    if existing is not None and not args.overwrite:
        return {"folder": folder.name, "status": "ready", "reason": "subtitle_exists", "subtitle": existing.name}
    if args.dry_run:
        return {"folder": folder.name, "status": "pending", "reason": "dry_run"}
    try:
        with tempfile.TemporaryDirectory(prefix="autoyy-asr-") as tmp:
            wav = Path(tmp) / "audio.wav"
            extract_audio(video, wav, ffmpeg, args.ffmpeg_timeout)
            if backend == "funasr":
                segments = transcribe_funasr(wav, args.device)
            else:
                segments = transcribe_whisper(wav, args.model_size, args.language, args.device, args.vad, args.beam_size)
            write_srt_atomic(folder / "字幕.srt", segments)
            return {"folder": folder.name, "status": "ready", "reason": "transcribed", "segments": len(segments)}
    except OSError as exc:
        reason = "disk_or_file_error"
        return {"folder": folder.name, "status": "failed", "reason": reason, "error": str(exc)[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"folder": folder.name, "status": "failed", "reason": "transcription_failed", "error": str(exc)[:300]}


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoYY local ASR fallback: source video -> 字幕.srt")
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--backend", choices=["auto", "funasr", "whisper"], default="auto")
    parser.add_argument("--model-size", default="medium")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--ffmpeg-location")
    parser.add_argument("--ffmpeg-timeout", type=int, default=7200)
    parser.add_argument("--hf-endpoint")
    parser.add_argument("--vad", action="store_true", default=True)
    parser.add_argument("--no-vad", dest="vad", action="store_false")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--only-missing", action="store_true", help="Compatibility alias; missing-only is already the default")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = args.output_root.resolve()
    if not root.is_dir():
        print(f"Directory does not exist: {root}", file=sys.stderr)
        return EXIT_USAGE
    if args.hf_endpoint:
        os.environ["HF_ENDPOINT"] = args.hf_endpoint
    ffmpeg = shutil.which("ffmpeg")
    if args.ffmpeg_location:
        candidate = Path(args.ffmpeg_location) / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
        if candidate.is_file():
            ffmpeg = str(candidate)
    if not ffmpeg:
        print("ffmpeg not found; use --ffmpeg-location", file=sys.stderr)
        return EXIT_USAGE
    try:
        if args.manifest:
            rows = load_manifest(args.manifest, output_root=root)
            folders = [safe_child_path(root, row.folder_name) for row in rows]
        else:
            folders = discover_topics(root)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    if not folders:
        print("No topic directories found", file=sys.stderr)
        return EXIT_USAGE
    needs_backend = any(
        folder.is_dir()
        and find_primary_video(folder) is not None
        and (find_primary_subtitle(folder) is None or args.overwrite)
        for folder in folders
    )
    try:
        backend = "dry-run" if args.dry_run else (resolve_backend(args.backend) if needs_backend else args.backend)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    if args.dry_run:
        results = [process_folder(folder, args, backend, ffmpeg) for folder in folders]
        for item in results:
            print(f"{item['status']:8s} {item['folder']} {item['reason']}")
        print(f"summary pending={sum(item['status'] == 'pending' for item in results)} total={len(results)}")
        return EXIT_OK
    try:
        state = load_state(root, create=True)
        recover_running(state)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    results = []
    for folder in folders:
        result = process_folder(folder, args, backend, ffmpeg)
        results.append(result)
        video = find_primary_video(folder) if folder.is_dir() else None
        subtitle = find_primary_subtitle(folder) if folder.is_dir() else None
        if video:
            set_stage(state, folder.name, "source", "ready", fingerprint=file_fingerprint(video))
        if subtitle and result["status"] == "ready":
            set_stage(state, folder.name, "subtitle", "ready", fingerprint=file_fingerprint(subtitle))
        elif result["status"] == "failed":
            set_stage(state, folder.name, "subtitle", "failed", fingerprint="missing", reason=result.get("reason", "transcription_failed"))
        else:
            set_stage(state, folder.name, "subtitle", "blocked", fingerprint="missing", reason=result.get("reason", "subtitle unavailable"))
    save_state(root, state)
    for item in results:
        print(f"{item['status']:8s} {item['folder']} {item['reason']}")
    failed = sum(item["status"] != "ready" for item in results)
    ready = sum(item["status"] == "ready" for item in results)
    print(f"summary ready={ready} failed={failed} total={len(results)}")
    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
