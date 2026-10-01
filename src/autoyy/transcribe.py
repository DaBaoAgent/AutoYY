from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .io import configure_utf8_stdio
from .manifest import load_manifest
from .media import find_primary_subtitle, find_primary_video
from .observability import RunRecorder, record_event
from .paths import discover_topics, safe_child_path
from .resources import detect_resources
from .result import EXIT_FAILED, EXIT_OK, EXIT_USAGE
from .state import file_fingerprint, media_fingerprint, recover_running, set_stage, update_state
from .subtitles import validate_srt

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
    blocks: list[str] = []
    for index, (start, end, text) in enumerate(segments, 1):
        body = "\n".join(split_long_line(text))
        if body:
            blocks.append(f"{index}\n{fmt_ts(start)} --> {fmt_ts(end)}\n{body}")
    if not blocks:
        raise RuntimeError("ASR returned no subtitle blocks")
    tmp = path.with_suffix(".srt.tmp")
    tmp.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def normalize_language(value: str | None) -> str | None:
    normalized = (value or "").strip().lower().replace("_", "-")
    if normalized in {"", "auto", "und", "unknown"}:
        return None
    if normalized.startswith("zh"):
        return "zh"
    return normalized.split("-", 1)[0]


def backend_preference(language: str | None) -> tuple[str, str]:
    return ("funasr", "whisper") if language == "zh" else ("whisper", "funasr")


def _backend_available(name: str) -> bool:
    try:
        if name == "funasr":
            import funasr  # noqa: F401
        else:
            import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def resolve_backend(name: str, language: str | None = None) -> str:
    if name in {"funasr", "whisper"}:
        if not _backend_available(name):
            extra = "asr-funasr" if name == "funasr" else "asr-whisper"
            raise RuntimeError(f"{name} unavailable; install .[{extra}]")
        if name == "funasr" and language not in {None, "zh"}:
            raise RuntimeError(f"FunASR paraformer-zh cannot transcribe source language: {language}")
        return name
    for candidate in backend_preference(language):
        if candidate == "funasr" and language not in {None, "zh"}:
            continue
        if _backend_available(candidate):
            return candidate
    raise RuntimeError("No compatible ASR backend; install .[asr-whisper] or .[asr-funasr]")


def resolve_ffmpeg(location: str | None) -> str | None:
    if location:
        base = Path(location)
        candidate = base / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if candidate.is_file():
            return str(candidate)
        return None
    return shutil.which("ffmpeg")


def extract_audio(video: Path, wav_path: Path, ffmpeg: str, timeout: int) -> None:
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"ffmpeg unavailable/timeout: {exc}") from exc
    if proc.returncode != 0 or not wav_path.is_file() or wav_path.stat().st_size <= 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {proc.stderr.strip()[:300]}")


def transcribe_whisper(
    wav_path: Path,
    model_size: str,
    language: str | None,
    device: str,
    vad: bool,
    beam_size: int,
) -> list[tuple[float, float, str]]:
    from faster_whisper import WhisperModel

    key = f"whisper:{model_size}:{device}:{threading.get_ident()}"
    if key not in _BACKEND_CACHE:
        compute_type = "int8" if device == "cpu" else "float16"
        _BACKEND_CACHE[key] = WhisperModel(model_size, device=device, compute_type=compute_type)
    model = _BACKEND_CACHE[key]
    segments, _ = model.transcribe(
        str(wav_path),
        language=language,
        beam_size=beam_size,
        vad_filter=vad,
        without_timestamps=False,
    )
    return [
        (float(segment.start), float(segment.end), (segment.text or "").strip())
        for segment in segments
        if (segment.text or "").strip()
    ]


def transcribe_funasr(wav_path: Path, device: str) -> list[tuple[float, float, str]]:
    from funasr import AutoModel

    key = f"funasr:{device}:{threading.get_ident()}"
    if key not in _BACKEND_CACHE:
        _BACKEND_CACHE[key] = AutoModel(
            model="paraformer-zh",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            device=device,
            disable_update=True,
        )
    result = _BACKEND_CACHE[key].generate(input=str(wav_path), batch_size_s=300)
    out: list[tuple[float, float, str]] = []
    if result:
        for item in result[0].get("sentence_info") or []:
            text = (item.get("text") or "").strip()
            if text:
                out.append((float(item["start"]) / 1000.0, float(item["end"]) / 1000.0, text))
    return out


def process_folder(folder: Path, args: argparse.Namespace, backend_name: str, ffmpeg: str | None, language: str | None = None) -> dict[str, object]:
    started = time.perf_counter()
    if not folder.is_dir():
        return {"folder": folder.name, "status": "failed", "reason": "topic_directory_missing", "error_code": "TOPIC_DIRECTORY_MISSING"}
    video = find_primary_video(folder)
    if video is None:
        return {"folder": folder.name, "status": "failed", "reason": "video_missing", "error_code": "VIDEO_MISSING"}
    existing = find_primary_subtitle(folder)
    invalid_existing: Path | None = None
    if existing is not None and not args.overwrite:
        if validate_srt(existing)["valid"]:
            return {"folder": folder.name, "status": "ready", "reason": "subtitle_exists", "subtitle": existing.name, "error_code": "", "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3)}
        if args.dry_run:
            return {"folder": folder.name, "status": "pending", "reason": "invalid_subtitle_repair", "error_code": "INVALID_SUBTITLE", "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3)}
        invalid_existing = existing
    if args.dry_run:
        return {"folder": folder.name, "status": "pending", "reason": "dry_run", "error_code": "", "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3)}
    try:
        backend = resolve_backend(backend_name, language)
        if backend == "funasr" and not ffmpeg:
            return {"folder": folder.name, "status": "failed", "reason": "ffmpeg_missing", "error_code": "FFMPEG_MISSING", "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3)}
        if invalid_existing is not None:
            invalid_target = invalid_existing.with_name(invalid_existing.name + ".invalid")
            counter = 1
            while invalid_target.exists():
                invalid_target = invalid_existing.with_name(invalid_existing.name + f".invalid-{counter}")
                counter += 1
            invalid_existing.replace(invalid_target)
        cpu_fallback = False
        if backend == "whisper":
            try:
                segments = transcribe_whisper(video, args.model_size, language, args.device, args.vad, args.beam_size)
            except Exception as exc:  # noqa: BLE001
                lowered = str(exc).lower()
                if args.device != "cuda" or not any(token in lowered for token in ("out of memory", "cuda", "cudnn", "cublas")):
                    raise
                segments = transcribe_whisper(video, args.model_size, language, "cpu", args.vad, args.beam_size)
                cpu_fallback = True
        else:
            with tempfile.TemporaryDirectory(prefix="autoyy-asr-") as tmp:
                wav = Path(tmp) / "audio.wav"
                extract_audio(video, wav, ffmpeg, args.ffmpeg_timeout)
                try:
                    segments = transcribe_funasr(wav, args.device)
                except Exception as exc:  # noqa: BLE001
                    lowered = str(exc).lower()
                    if args.device != "cuda" or not any(token in lowered for token in ("out of memory", "cuda", "cudnn", "cublas")):
                        raise
                    segments = transcribe_funasr(wav, "cpu")
                    cpu_fallback = True
        target = folder / "字幕.srt"
        write_srt_atomic(target, segments)
        report = validate_srt(target)
        if not report["valid"]:
            raise RuntimeError("generated subtitle failed SRT validation: " + "; ".join(report["issues"][:3]))
        result = {"folder": folder.name, "status": "ready", "reason": "transcribed", "segments": len(segments), "backend": backend, "language": language or "auto", "device": "cpu" if cpu_fallback else args.device, "cpu_fallback": cpu_fallback, "error_code": ""}
    except OSError as exc:
        result = {"folder": folder.name, "status": "failed", "reason": "disk_or_file_error", "error_code": "DISK_OR_FILE_ERROR", "error": str(exc)[:300]}
    except Exception as exc:  # noqa: BLE001
        result = {"folder": folder.name, "status": "failed", "reason": "transcription_failed", "error_code": "ASR_TRANSCRIPTION_FAILED", "error": str(exc)[:300]}
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    return result

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AutoYY local ASR fallback: source video -> 字幕.srt")
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--topic", action="append", dest="topics", help="process only this exact topic folder; repeatable")
    parser.add_argument("--backend", choices=["auto", "funasr", "whisper"], default="auto")
    parser.add_argument("--model-size", default="medium")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--ffmpeg-location")
    parser.add_argument("--ffmpeg-timeout", type=int, default=7200)
    parser.add_argument("--hf-endpoint")
    parser.add_argument("--vad", action="store_true", default=True)
    parser.add_argument("--no-vad", dest="vad", action="store_false")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--only-missing", action="store_true", help="Compatibility alias; missing-only is already the default")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--run-id", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    if args.device == "auto":
        args.device = detect_resources().asr_device
    root = args.output_root.resolve()
    if not root.is_dir():
        print(f"Directory does not exist: {root}")
        return EXIT_USAGE
    if args.hf_endpoint:
        os.environ["HF_ENDPOINT"] = args.hf_endpoint
    language_by_folder: dict[str, str | None] = {}
    try:
        if args.manifest:
            rows = load_manifest(args.manifest, output_root=root)
            folders = [safe_child_path(root, row.folder_name) for row in rows]
            language_by_folder = {row.folder_name: normalize_language(row.data.get("source_language")) for row in rows}
        else:
            folders = discover_topics(root)
    except (OSError, ValueError) as exc:
        print(str(exc))
        return EXIT_USAGE
    if args.topics:
        requested = list(dict.fromkeys(args.topics))
        by_name = {folder.name: folder for folder in folders}
        missing = [name for name in requested if name not in by_name]
        if missing:
            print("Requested topic directories not found: " + ", ".join(missing))
            return EXIT_USAGE
        folders = [by_name[name] for name in requested]
    if not folders:
        print("No topic directories found")
        return EXIT_USAGE

    def needs_transcription(folder: Path) -> bool:
        if not folder.is_dir() or find_primary_video(folder) is None:
            return False
        subtitle = find_primary_subtitle(folder)
        return args.overwrite or subtitle is None or not validate_srt(subtitle)["valid"]

    default_language = normalize_language(args.language)
    if args.dry_run:
        results = [process_folder(folder, args, args.backend, None, language_by_folder.get(folder.name) or default_language) for folder in folders]
        for item in results:
            print(f"{item['status']:8s} {item['folder']} {item['reason']}")
        failed = sum(item["status"] == "failed" for item in results)
        print(f"summary pending={sum(item['status'] == 'pending' for item in results)} failed={failed} total={len(results)}")
        return EXIT_FAILED if failed else EXIT_OK

    backend_by_folder: dict[str, str] = {}
    try:
        for folder in folders:
            if needs_transcription(folder):
                language = language_by_folder.get(folder.name) or default_language
                backend_by_folder[folder.name] = resolve_backend(args.backend, language)
    except RuntimeError as exc:
        print(str(exc))
        return EXIT_USAGE
    ffmpeg = resolve_ffmpeg(args.ffmpeg_location)
    if any(value == "funasr" for value in backend_by_folder.values()) and not ffmpeg:
        print("ffmpeg not found; FunASR requires --ffmpeg-location or ffmpeg on PATH")
        return EXIT_USAGE
    try:
        update_state(root, lambda state: recover_running(state), create=True)
    except (OSError, ValueError) as exc:
        print(str(exc))
        return EXIT_USAGE

    recorder = RunRecorder(root, "transcribe", run_id=args.run_id or None)
    args.run_id = recorder.run_id
    results: list[dict[str, object]] = []

    def execute(folder: Path) -> tuple[Path, dict[str, object]]:
        language = language_by_folder.get(folder.name) or default_language
        backend = backend_by_folder.get(folder.name, args.backend)
        return folder, process_folder(folder, args, backend, ffmpeg, language)

    def checkpoint(folder: Path, result: dict[str, object]) -> None:
        video = find_primary_video(folder) if folder.is_dir() else None
        subtitle = find_primary_subtitle(folder) if folder.is_dir() else None
        def apply_result(state):
            if video:
                set_stage(state, folder.name, "source", "ready", fingerprint=media_fingerprint(video))
            if subtitle and result["status"] == "ready":
                set_stage(state, folder.name, "subtitle", "ready", fingerprint=file_fingerprint(subtitle))
            elif result["status"] == "failed":
                set_stage(state, folder.name, "subtitle", "failed", fingerprint="missing", reason=str(result.get("reason", "transcription_failed")))
            else:
                set_stage(state, folder.name, "subtitle", "blocked", fingerprint="missing", reason=str(result.get("reason", "subtitle unavailable")))
        update_state(root, apply_result, create=True)
        record_event(root, "topic_finish", run_id=recorder.run_id, command="transcribe", topic=folder.name, stage="subtitle", status=str(result["status"]), code=str(result.get("error_code", "")), message=str(result.get("error", result.get("reason", ""))), elapsed_ms=float(result.get("elapsed_ms") or 0), details={"backend": result.get("backend", backend_by_folder.get(folder.name, "")), "language": result.get("language", language_by_folder.get(folder.name) or default_language or "auto")})

    try:
        if args.workers == 1:
            for folder in folders:
                done_folder, result = execute(folder)
                results.append(result)
                checkpoint(done_folder, result)
        else:
            with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="autoyy-asr") as pool:
                futures = {pool.submit(execute, folder): folder for folder in folders}
                for future in as_completed(futures):
                    done_folder, result = future.result()
                    results.append(result)
                    checkpoint(done_folder, result)
    except OSError as exc:
        recorder.finish(status="error", code="STATE_UPDATE_FAILED", message=str(exc))
        print(str(exc))
        return EXIT_USAGE
    results.sort(key=lambda item: str(item.get("folder", "")))
    for item in results:
        detail = str(item.get("error") or item["reason"])
        print(f"{item['status']:8s} {item['folder']} {detail}")
    failed = sum(item["status"] != "ready" for item in results)
    ready = sum(item["status"] == "ready" for item in results)
    recorder.finish(status="success" if not failed else "failed", code="" if not failed else "ASR_BATCH_INCOMPLETE", details={"ready": ready, "failed": failed, "workers": args.workers})
    print(f"summary run_id={recorder.run_id} ready={ready} failed={failed} total={len(results)} workers={args.workers}")
    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
