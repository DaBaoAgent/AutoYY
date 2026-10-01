from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .io import configure_utf8_stdio
from .manifest import load_manifest
from .media import find_primary_subtitle, find_primary_video
from .paths import discover_topics, safe_child_path
from .result import EXIT_FAILED, EXIT_OK, EXIT_USAGE
from .state import file_fingerprint, recover_running, set_stage, update_state
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

    key = f"whisper:{model_size}:{device}"
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

    key = f"funasr:{device}"
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
    if not folder.is_dir():
        return {"folder": folder.name, "status": "failed", "reason": "topic_directory_missing"}
    video = find_primary_video(folder)
    if video is None:
        return {"folder": folder.name, "status": "failed", "reason": "video_missing"}
    existing = find_primary_subtitle(folder)
    invalid_existing: Path | None = None
    if existing is not None and not args.overwrite:
        if validate_srt(existing)["valid"]:
            return {"folder": folder.name, "status": "ready", "reason": "subtitle_exists", "subtitle": existing.name}
        if args.dry_run:
            return {"folder": folder.name, "status": "pending", "reason": "invalid_subtitle_repair"}
        invalid_existing = existing
    if args.dry_run:
        return {"folder": folder.name, "status": "pending", "reason": "dry_run"}
    if not ffmpeg:
        return {"folder": folder.name, "status": "failed", "reason": "ffmpeg_missing"}
    try:
        backend = resolve_backend(backend_name, language)
        if invalid_existing is not None:
            invalid_target = invalid_existing.with_name(invalid_existing.name + ".invalid")
            counter = 1
            while invalid_target.exists():
                invalid_target = invalid_existing.with_name(invalid_existing.name + f".invalid-{counter}")
                counter += 1
            invalid_existing.replace(invalid_target)
        with tempfile.TemporaryDirectory(prefix="autoyy-asr-") as tmp:
            wav = Path(tmp) / "audio.wav"
            extract_audio(video, wav, ffmpeg, args.ffmpeg_timeout)
            if backend == "funasr":
                segments = transcribe_funasr(wav, args.device)
            else:
                segments = transcribe_whisper(
                    wav, args.model_size, language, args.device, args.vad, args.beam_size
                )
            target = folder / "字幕.srt"
            write_srt_atomic(target, segments)
            report = validate_srt(target)
            if not report["valid"]:
                raise RuntimeError("generated subtitle failed SRT validation: " + "; ".join(report["issues"][:3]))
            return {
                "folder": folder.name, "status": "ready", "reason": "transcribed",
                "segments": len(segments), "backend": backend, "language": language or "auto",
            }
    except OSError as exc:
        return {"folder": folder.name, "status": "failed", "reason": "disk_or_file_error", "error": str(exc)[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"folder": folder.name, "status": "failed", "reason": "transcription_failed", "error": str(exc)[:300]}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AutoYY local ASR fallback: source video -> 字幕.srt")
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--backend", choices=["auto", "funasr", "whisper"], default="auto")
    parser.add_argument("--model-size", default="medium")
    parser.add_argument("--language", default="auto")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args(argv)
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
            language_by_folder = {
                row.folder_name: normalize_language(row.data.get("source_language"))
                for row in rows
            }
        else:
            folders = discover_topics(root)
    except (OSError, ValueError) as exc:
        print(str(exc))
        return EXIT_USAGE
    if not folders:
        print("No topic directories found")
        return EXIT_USAGE

    def needs_transcription(folder: Path) -> bool:
        if not folder.is_dir() or find_primary_video(folder) is None:
            return False
        subtitle = find_primary_subtitle(folder)
        return args.overwrite or subtitle is None or not validate_srt(subtitle)["valid"]

    needs_backend = any(needs_transcription(folder) for folder in folders)
    default_language = normalize_language(args.language)

    if args.dry_run:
        results = [
            process_folder(
                folder, args, args.backend, None,
                language_by_folder.get(folder.name) or default_language,
            )
            for folder in folders
        ]
        for item in results:
            print(f"{item['status']:8s} {item['folder']} {item['reason']}")
        failed = sum(item["status"] == "failed" for item in results)
        print(f"summary pending={sum(item['status'] == 'pending' for item in results)} failed={failed} total={len(results)}")
        return EXIT_FAILED if failed else EXIT_OK

    ffmpeg = resolve_ffmpeg(args.ffmpeg_location) if needs_backend else None
    if needs_backend and not ffmpeg:
        print("ffmpeg not found; use --ffmpeg-location")
        return EXIT_USAGE
    if needs_backend:
        if args.backend != "auto" and not _backend_available(args.backend):
            print(f"{args.backend} unavailable; install the matching ASR extra")
            return EXIT_USAGE
        if args.backend == "auto" and not (_backend_available("whisper") or _backend_available("funasr")):
            print("No ASR backend; install .[asr-whisper] or .[asr-funasr]")
            return EXIT_USAGE
    try:
        update_state(root, lambda state: recover_running(state), create=True)
    except (OSError, ValueError) as exc:
        print(str(exc))
        return EXIT_USAGE

    results: list[dict[str, object]] = []
    for folder in folders:
        language = language_by_folder.get(folder.name) or default_language
        result = process_folder(folder, args, args.backend, ffmpeg, language)
        results.append(result)
        video = find_primary_video(folder) if folder.is_dir() else None
        subtitle = find_primary_subtitle(folder) if folder.is_dir() else None

        def apply_result(state, *, folder=folder, result=result, video=video, subtitle=subtitle):
            if video:
                set_stage(state, folder.name, "source", "ready", fingerprint=file_fingerprint(video))
            if subtitle and result["status"] == "ready":
                set_stage(state, folder.name, "subtitle", "ready", fingerprint=file_fingerprint(subtitle))
            elif result["status"] == "failed":
                set_stage(
                    state, folder.name, "subtitle", "failed", fingerprint="missing",
                    reason=str(result.get("reason", "transcription_failed")),
                )
            else:
                set_stage(
                    state, folder.name, "subtitle", "blocked", fingerprint="missing",
                    reason=str(result.get("reason", "subtitle unavailable")),
                )

        try:
            update_state(root, apply_result, create=True)
        except OSError as exc:
            print(str(exc))
            return EXIT_USAGE
    for item in results:
        print(f"{item['status']:8s} {item['folder']} {item['reason']}")
    failed = sum(item["status"] != "ready" for item in results)
    ready = sum(item["status"] == "ready" for item in results)
    print(f"summary ready={ready} failed={failed} total={len(results)}")
    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
