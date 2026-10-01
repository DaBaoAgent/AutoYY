from __future__ import annotations

import argparse
import json
import shutil
from datetime import date
from pathlib import Path

from . import __version__
from .config import peer_library
from .deliverables import validate_root
from .doctor import run_doctor
from .download import DownloadOptions, run_download
from .io import configure_utf8_stdio
from .peer import load_library, pattern_stats
from .publication import validate_publication_file
from .state import (
    file_fingerprint,
    force_stage,
    load_state,
    recover_running,
    save_state,
    set_approved,
    set_stage,
)
from .transcribe import main as transcribe_main
from .voiceover import (
    attest_quality_file,
    create_quality_template,
    promote_candidate,
    validate_voiceover_batch,
)


def emit(payload: object, *, compact: bool = False) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=None if compact else 2))


def cmd_doctor(args: argparse.Namespace) -> int:
    result = run_doctor(Path(args.repo_root).resolve() if args.repo_root else None)
    emit(result, compact=args.json)
    return 0 if result["ok"] else 1


def cmd_validate(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        emit({"valid": False, "error": "root does not exist"})
        return 2
    folders = sorted(p for p in root.iterdir() if p.is_dir() and p.name[:2].isdigit() and p.name[2:3] == "-")
    if not folders and not args.allow_empty:
        result = validate_root(root, allow_empty=False, expected_count=args.expected_count, require_quality=not args.skip_quality_record, ffprobe=None)
        emit(result, compact=args.json)
        return 1
    ffprobe = args.ffprobe or shutil.which("ffprobe")
    if folders and not ffprobe:
        emit({"valid": False, "error": "ffprobe is required for final media validation"})
        return 2
    result = validate_root(root, allow_empty=args.allow_empty, expected_count=args.expected_count, require_quality=not args.skip_quality_record, ffprobe=ffprobe)
    state = load_state(root, create=True)
    for row in result.get("results", []):
        set_stage(state, row["folder"], "package", "ready" if row["complete"] else "failed", reason="" if row["complete"] else "; ".join(row["issues"][:3]))
    save_state(root, state)
    emit(result, compact=args.json)
    return 0 if result["valid"] else 1


def cmd_publication_validate(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        return 2
    files = sorted(root.rglob("发布信息.txt"))
    results = [{"file": str(path.relative_to(root)), **validate_publication_file(path)} for path in files]
    valid = bool(results) and all(item["valid"] for item in results)
    state = load_state(root, create=True)
    for path, result in zip(files, results, strict=True):
        topic = path.parent.name
        set_stage(state, topic, "publication", "ready" if result["valid"] else "failed", fingerprint=file_fingerprint(path), reason="" if result["valid"] else "; ".join(result["issues"][:3]))
    save_state(root, state)
    emit({"valid": valid, "file_count": len(results), "results": results}, compact=args.json)
    return 0 if valid else 1


def cmd_voiceover_scaffold(args: argparse.Namespace) -> int:
    try:
        path = create_quality_template(Path(args.topic).resolve(), voice_profile=args.voice_profile)
    except (OSError, ValueError) as exc:
        emit({"ok": False, "error": str(exc)})
        return 2
    emit({"ok": True, "quality_file": str(path)})
    return 0


def cmd_voiceover_attest(args: argparse.Namespace) -> int:
    try:
        path = attest_quality_file(
            Path(args.topic).resolve(),
            issuer=args.issuer,
            run_id=args.run_id,
        )
    except (OSError, ValueError) as exc:
        emit({"ok": False, "error": str(exc)})
        return 2
    emit({"ok": True, "quality_file": str(path), "attested": True})
    return 0


def cmd_voiceover_validate(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        return 2
    result = validate_voiceover_batch(root, require_quality=not args.skip_quality_record)
    state = load_state(root, create=True)
    for row in result.get("results", []):
        script = root / row["topic"] / "爆款口播稿.txt"
        stage_status = "ready" if row["valid"] else ("blocked" if row.get("status") == "blocked_quality" else "failed")
        reason = "" if row["valid"] else (("blocked_quality: " if stage_status == "blocked" else "") + "; ".join(row["issues"][:3]))
        set_stage(state, row["topic"], "voiceover", stage_status, fingerprint=file_fingerprint(script), reason=reason)
    save_state(root, state)
    emit(result, compact=args.json)
    return 0 if result["valid"] else 1


def cmd_voiceover_promote(args: argparse.Namespace) -> int:
    topic = Path(args.topic).resolve()
    result = promote_candidate(topic, force=args.force)
    if result.get("promoted"):
        root = topic.parent
        state = load_state(root, create=True)
        set_stage(state, topic.name, "voiceover", "ready", fingerprint=file_fingerprint(topic / "爆款口播稿.txt"))
        save_state(root, state)
    emit(result, compact=args.json)
    return 0 if result.get("promoted") else 1


def cmd_download(args: argparse.Namespace) -> int:
    options = DownloadOptions(
        manifest=Path(args.manifest),
        output_root=Path(args.output_root),
        yt_dlp=args.yt_dlp,
        ffmpeg_location=args.ffmpeg_location,
        cookies_from_browser=args.cookies_from_browser,
        proxy=args.proxy,
        min_height=args.min_height,
        max_height=args.max_height,
        plan_only=args.plan_only,
        parallel=args.parallel,
        use_aria2=args.use_aria2,
        aria2_connections=args.aria2_connections,
        trace=args.trace,
        js_runtimes=args.js_runtimes,
    )
    code, result = run_download(options)
    emit(result, compact=args.json)
    return code


def cmd_peer_patterns(args: argparse.Namespace) -> int:
    path = Path(args.library).resolve() if args.library else peer_library(Path.cwd())
    try:
        rows = load_library(path)
        since = date.fromisoformat(args.since) if args.since else None
        result = pattern_stats(rows, platform=args.platform, category=args.category, since=since)
    except ValueError as exc:
        emit({"ok": False, "error": str(exc)})
        return 2
    emit(result, compact=args.json)
    return 0


def cmd_state_show(args: argparse.Namespace) -> int:
    try:
        state = load_state(Path(args.root).resolve())
    except (OSError, ValueError) as exc:
        emit({"ok": False, "error": str(exc)})
        return 2
    emit(state, compact=args.json)
    return 0


def cmd_state_recover(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    try:
        state = load_state(root)
        count = recover_running(state)
        save_state(root, state)
    except (OSError, ValueError) as exc:
        emit({"ok": False, "error": str(exc)})
        return 2
    emit({"ok": True, "recovered": count})
    return 0


def cmd_state_approve(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    try:
        state = load_state(root)
        set_approved(state, args.topic, args.stage, approved=not args.revoke, reason=args.reason or "")
        save_state(root, state)
    except (OSError, ValueError) as exc:
        emit({"ok": False, "error": str(exc)})
        return 2
    emit({"ok": True, "topic": args.topic, "stage": args.stage, "approved": not args.revoke})
    return 0


def cmd_state_force(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    try:
        state = load_state(root)
        force_stage(state, args.topic, args.stage, reason=args.reason or "forced rerun")
        save_state(root, state)
    except (OSError, ValueError) as exc:
        emit({"ok": False, "error": str(exc)})
        return 2
    emit({"ok": True, "topic": args.topic, "stage": args.stage, "status": "pending"})
    return 0


def cmd_transcribe(args: argparse.Namespace) -> int:
    command = [args.root]
    if args.manifest:
        command += ["--manifest", args.manifest]
    command += ["--backend", args.backend]
    return transcribe_main(command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autoyy", description="AutoYY deterministic workflow and quality gates")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--json", action="store_true", help="compact JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--repo-root")
    doctor.set_defaults(func=cmd_doctor)

    validate = sub.add_parser("validate")
    validate.add_argument("root")
    validate.add_argument("--allow-empty", action="store_true")
    validate.add_argument("--expected-count", type=int)
    validate.add_argument("--skip-quality-record", action="store_true")
    validate.add_argument("--ffprobe")
    validate.set_defaults(func=cmd_validate)

    publication = sub.add_parser("publication")
    publication_sub = publication.add_subparsers(dest="publication_command", required=True)
    publication_validate = publication_sub.add_parser("validate")
    publication_validate.add_argument("root")
    publication_validate.set_defaults(func=cmd_publication_validate)

    voiceover = sub.add_parser("voiceover")
    voice_sub = voiceover.add_subparsers(dest="voice_command", required=True)
    scaffold = voice_sub.add_parser("scaffold")
    scaffold.add_argument("topic")
    scaffold.add_argument("--voice-profile", choices=["default", "laorou"], default="default")
    scaffold.set_defaults(func=cmd_voiceover_scaffold)
    attest = voice_sub.add_parser("attest")
    attest.add_argument("topic")
    attest.add_argument("--issuer", default="autoyy-independent-verifier")
    attest.add_argument("--run-id")
    attest.set_defaults(func=cmd_voiceover_attest)
    voice_validate = voice_sub.add_parser("validate")
    voice_validate.add_argument("root")
    voice_validate.add_argument("--skip-quality-record", action="store_true")
    voice_validate.set_defaults(func=cmd_voiceover_validate)
    promote = voice_sub.add_parser("promote")
    promote.add_argument("topic")
    promote.add_argument("--force", action="store_true")
    promote.set_defaults(func=cmd_voiceover_promote)

    download = sub.add_parser("download")
    download.add_argument("--manifest", required=True)
    download.add_argument("--output-root", required=True)
    download.add_argument("--yt-dlp", default="yt-dlp")
    download.add_argument("--ffmpeg-location")
    download.add_argument("--cookies-from-browser")
    download.add_argument("--proxy")
    download.add_argument("--min-height", type=int, default=720)
    download.add_argument("--max-height", type=int, default=1080)
    download.add_argument("--plan-only", action="store_true")
    download.add_argument("--parallel", type=int, default=1)
    download.add_argument("--use-aria2", action="store_true")
    download.add_argument("--aria2-connections", type=int, default=8)
    download.add_argument("--trace", action="store_true")
    download.add_argument("--js-runtimes")
    download.set_defaults(func=cmd_download)

    peer = sub.add_parser("peer")
    peer_sub = peer.add_subparsers(dest="peer_command", required=True)
    patterns = peer_sub.add_parser("patterns")
    patterns.add_argument("--library")
    patterns.add_argument("--platform")
    patterns.add_argument("--category")
    patterns.add_argument("--since")
    patterns.set_defaults(func=cmd_peer_patterns)

    state = sub.add_parser("state")
    state_sub = state.add_subparsers(dest="state_command", required=True)
    state_show = state_sub.add_parser("show")
    state_show.add_argument("root")
    state_show.set_defaults(func=cmd_state_show)
    state_recover = state_sub.add_parser("recover")
    state_recover.add_argument("root")
    state_recover.set_defaults(func=cmd_state_recover)
    state_approve = state_sub.add_parser("approve")
    state_approve.add_argument("root")
    state_approve.add_argument("topic")
    state_approve.add_argument("stage", choices=["source", "subtitle", "voiceover", "publication", "cover", "package"])
    state_approve.add_argument("--reason")
    state_approve.add_argument("--revoke", action="store_true")
    state_approve.set_defaults(func=cmd_state_approve)
    state_force = state_sub.add_parser("force")
    state_force.add_argument("root")
    state_force.add_argument("topic")
    state_force.add_argument("stage", choices=["source", "subtitle", "voiceover", "publication", "cover", "package"])
    state_force.add_argument("--reason")
    state_force.set_defaults(func=cmd_state_force)

    transcribe = sub.add_parser("transcribe")
    transcribe.add_argument("root")
    transcribe.add_argument("--manifest")
    transcribe.add_argument("--backend", choices=["auto", "funasr", "whisper"], default="auto")
    transcribe.set_defaults(func=cmd_transcribe)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
