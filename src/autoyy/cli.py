from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .batch import batch_status, stage_plan
from .config import peer_library
from .deliverables import validate_root
from .diagnostics import diagnose
from .doctor import run_doctor
from .download import DownloadOptions, run_download
from .io import configure_utf8_stdio
from .peer import load_library, pattern_stats
from .profiling import inventory_root, profile_workload
from .publication import validate_publication_file
from .resources import resource_plan_dict
from .runtime import RuntimeOptions, reconcile_existing, runtime_plan, runtime_run, runtime_tick
from .scheduler import STRATEGIES, parse_capabilities, scheduler_plan
from .soak import run_control_plane_soak
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
from .work import claim_work, heartbeat_work, release_work, require_active_lease, work_status


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
    result = validate_root(root, allow_empty=args.allow_empty, expected_count=args.expected_count, require_quality=not args.skip_quality_record, ffprobe=ffprobe, workers=args.workers)
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
    topic = Path(args.topic).resolve()
    try:
        siblings = [p for p in topic.parent.iterdir() if p.is_dir() and p.name[:2].isdigit() and p.name[2:3] == "-"]
        lease = None
        if len(siblings) >= 2:
            if not args.lease_token:
                raise ValueError("multi-topic voiceover scaffold requires --lease-token from autoyy work claim")
            lease = require_active_lease(topic.parent, args.lease_token, topic=topic.name, stage="voiceover")
        path = create_quality_template(
            topic, voice_profile=args.voice_profile,
            writer_lease_token=args.lease_token,
            writer_id=args.writer_id or (str(lease.get("worker_id")) if lease else None),
        )
    except (OSError, ValueError, RuntimeError) as exc:
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
        max_command_attempts=args.max_command_attempts,
        retry_base_seconds=args.retry_base_seconds,
        retry_max_seconds=args.retry_max_seconds,
        rate_limit=args.rate_limit,
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
    result = validate_voiceover_batch(root, require_quality=not args.skip_quality_record, workers=args.workers)
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
        overlap_assets=not args.no_overlap_assets,
        max_command_attempts=args.max_command_attempts,
        retry_base_seconds=args.retry_base_seconds,
        retry_max_seconds=args.retry_max_seconds,
        rate_limit=args.rate_limit,
        adaptive_parallel=not args.fixed_workers,
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


def cmd_batch_status(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        emit({"ok": False, "error": "root does not exist"}, compact=args.json)
        return 2
    emit(batch_status(root), compact=args.json)
    return 0


def cmd_batch_plan(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        emit({"ok": False, "error": "root does not exist"}, compact=args.json)
        return 2
    try:
        result = stage_plan(root, args.stage)
    except ValueError as exc:
        emit({"ok": False, "error": str(exc)}, compact=args.json)
        return 2
    emit(result, compact=args.json)
    return 0


def cmd_batch_inventory(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    try:
        result = inventory_root(root)
    except FileNotFoundError:
        emit({"ok": False, "error_code": "ROOT_NOT_FOUND", "error": "root does not exist"}, compact=args.json)
        return 2
    result["ok"] = result["legacy_candidate_count"] == 0
    emit(result, compact=args.json)
    return 0 if result["ok"] else 1


def cmd_profile(args: argparse.Namespace) -> int:
    try:
        result = profile_workload(Path(args.root).resolve())
    except FileNotFoundError:
        emit({"ok": False, "error_code": "ROOT_NOT_FOUND", "error": "root does not exist"}, compact=args.json)
        return 2
    emit(result, compact=args.json)
    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    try:
        result = diagnose(Path(args.root).resolve(), event_limit=args.event_limit)
    except FileNotFoundError:
        emit({"ok": False, "error_code": "ROOT_NOT_FOUND", "error": "root does not exist"}, compact=args.json)
        return 2
    emit(result, compact=args.json)
    return 0 if result["ok"] else 1


def cmd_schedule_plan(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        emit({"ok": False, "error_code": "ROOT_NOT_FOUND", "error": "root does not exist"}, compact=args.json)
        return 2
    try:
        result = scheduler_plan(root, capabilities=parse_capabilities(args.capabilities), active_leases=work_status(root)["leases"], strategy=args.strategy)
    except ValueError as exc:
        emit({"ok": False, "error_code": "INVALID_CAPABILITIES", "error": str(exc)}, compact=args.json)
        return 2
    emit(result, compact=args.json)
    return 0

def cmd_resources(args: argparse.Namespace) -> int:
    emit(resource_plan_dict(), compact=args.json)
    return 0


def _runtime_options(args: argparse.Namespace) -> RuntimeOptions:
    return RuntimeOptions(
        root=Path(args.root),
        manifest=Path(args.manifest) if args.manifest else None,
        yt_dlp=args.yt_dlp,
        ffmpeg_location=args.ffmpeg_location,
        cookies_from_browser=args.cookies_from_browser,
        proxy=args.proxy,
        use_aria2=args.use_aria2,
        aria2_connections=args.aria2_connections,
        js_runtimes=args.js_runtimes,
        allow_legacy_ignored=args.allow_legacy_ignored,
        max_passes=getattr(args, "max_passes", None),
        dry_run=args.dry_run,
        strategy=args.strategy,
        download_workers=args.download_workers,
        asr_workers=args.asr_workers,
        asr_device=args.asr_device,
        asr_model_size=args.asr_model_size,
        hf_endpoint=args.hf_endpoint,
        rate_limit=args.rate_limit,
        max_command_attempts=args.max_command_attempts,
    )


def cmd_runtime_reconcile(args: argparse.Namespace) -> int:
    if not Path(args.root).resolve().is_dir():
        emit({"ok": False, "error_code": "ROOT_NOT_FOUND", "error": "root does not exist"}, compact=args.json)
        return 2
    result = reconcile_existing(_runtime_options(args))
    emit(result, compact=args.json)
    return 1 if result["issues"] else 0


def cmd_runtime_plan(args: argparse.Namespace) -> int:
    if not Path(args.root).resolve().is_dir():
        emit({"ok": False, "error_code": "ROOT_NOT_FOUND", "error": "root does not exist"}, compact=args.json)
        return 2
    result = runtime_plan(_runtime_options(args))
    emit(result, compact=args.json)
    return 0 if result["runnable"] else 1


def cmd_runtime_tick(args: argparse.Namespace) -> int:
    if not Path(args.root).resolve().is_dir():
        emit({"ok": False, "error_code": "ROOT_NOT_FOUND", "error": "root does not exist"}, compact=args.json)
        return 2
    code, result = runtime_tick(_runtime_options(args))
    emit(result, compact=args.json)
    return code


def cmd_runtime_run(args: argparse.Namespace) -> int:
    if not Path(args.root).resolve().is_dir():
        emit({"ok": False, "error_code": "ROOT_NOT_FOUND", "error": "root does not exist"}, compact=args.json)
        return 2
    code, result = runtime_run(_runtime_options(args))
    emit(result, compact=args.json)
    return code


def cmd_runtime_soak(args: argparse.Namespace) -> int:
    try:
        result = run_control_plane_soak(
            topics=args.topics, operations=args.operations, workers=args.workers,
            fault_rate=args.fault_rate, crash_rate=args.crash_rate, seed=args.seed,
        )
    except ValueError as exc:
        emit({"ok": False, "error_code": "INVALID_SOAK_OPTIONS", "error": str(exc)}, compact=args.json)
        return 2
    emit(result, compact=args.json)
    return 0 if result["invariants_ok"] else 1


def cmd_work_claim(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    try:
        result = claim_work(
            root, worker_id=args.worker_id, stage=args.stage,
            lease_seconds=args.lease_seconds, topic=args.topic,
            capabilities=parse_capabilities(args.capabilities) if args.stage == "auto" else None,
            strategy=args.strategy,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        emit({"ok": False, "error": str(exc)}, compact=args.json)
        return 2
    emit(result, compact=args.json)
    return 0 if result.get("ok") else 1


def cmd_work_heartbeat(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        emit({"ok": False, "error": "root does not exist"}, compact=args.json)
        return 2
    result = heartbeat_work(root, args.token, lease_seconds=args.lease_seconds)
    emit(result, compact=args.json)
    return 0 if result.get("ok") else 1


def cmd_work_release(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        emit({"ok": False, "error": "root does not exist"}, compact=args.json)
        return 2
    result = release_work(root, args.token)
    emit(result, compact=args.json)
    return 0 if result.get("ok") else 1


def cmd_work_status(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        emit({"ok": False, "error": "root does not exist"}, compact=args.json)
        return 2
    emit(work_status(root), compact=args.json)
    return 0


def cmd_transcribe(args: argparse.Namespace) -> int:
    command = [args.root]
    if args.manifest:
        command += ["--manifest", args.manifest]
    for topic in args.topics or []:
        command += ["--topic", topic]
    command += [
        "--backend", args.backend, "--language", args.language,
        "--model-size", args.model_size, "--device", args.device,
        "--beam-size", str(args.beam_size), "--ffmpeg-timeout", str(args.ffmpeg_timeout),
        "--workers", str(args.workers),
    ]
    if args.ffmpeg_location:
        command += ["--ffmpeg-location", args.ffmpeg_location]
    if args.hf_endpoint:
        command += ["--hf-endpoint", args.hf_endpoint]
    if args.no_vad:
        command.append("--no-vad")
    if args.overwrite:
        command.append("--overwrite")
    if args.dry_run:
        command.append("--dry-run")
    return transcribe_main(command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autoyy", description="AutoYY deterministic workflow and quality gates")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--json", action="store_true", help="compact JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--repo-root")
    doctor.set_defaults(func=cmd_doctor)

    resources = sub.add_parser("resources")
    resources.set_defaults(func=cmd_resources)

    runtime = sub.add_parser("runtime")
    runtime_sub = runtime.add_subparsers(dest="runtime_command", required=True)
    def add_runtime_common(target):
        target.add_argument("root")
        target.add_argument("--manifest")
        target.add_argument("--yt-dlp", default="yt-dlp")
        target.add_argument("--ffmpeg-location")
        target.add_argument("--cookies-from-browser")
        target.add_argument("--proxy")
        target.add_argument("--use-aria2", action="store_true")
        target.add_argument("--aria2-connections", type=int, choices=range(1, 17), default=8)
        target.add_argument("--js-runtimes")
        target.add_argument("--allow-legacy-ignored", action="store_true")
        target.add_argument("--strategy", choices=sorted(STRATEGIES), default="finish-first")
        target.add_argument("--download-workers", type=int, choices=range(1, 9))
        target.add_argument("--asr-workers", type=int, choices=range(1, 5))
        target.add_argument("--asr-device", choices=["cpu", "cuda"])
        target.add_argument("--asr-model-size", default="medium")
        target.add_argument("--hf-endpoint")
        target.add_argument("--rate-limit")
        target.add_argument("--max-command-attempts", type=int, choices=range(1, 7), default=3)
        target.add_argument("--dry-run", action="store_true")
    runtime_reconcile = runtime_sub.add_parser("reconcile")
    add_runtime_common(runtime_reconcile)
    runtime_reconcile.set_defaults(func=cmd_runtime_reconcile)
    runtime_plan_parser = runtime_sub.add_parser("plan")
    add_runtime_common(runtime_plan_parser)
    runtime_plan_parser.set_defaults(func=cmd_runtime_plan)
    runtime_tick_parser = runtime_sub.add_parser("tick")
    add_runtime_common(runtime_tick_parser)
    runtime_tick_parser.set_defaults(func=cmd_runtime_tick)
    runtime_run_parser = runtime_sub.add_parser("run")
    add_runtime_common(runtime_run_parser)
    runtime_run_parser.add_argument("--max-passes", type=int, choices=range(1, 10001))
    runtime_run_parser.set_defaults(func=cmd_runtime_run)
    runtime_soak = runtime_sub.add_parser("soak")
    runtime_soak.add_argument("--topics", type=int, default=80)
    runtime_soak.add_argument("--operations", type=int, default=1000)
    runtime_soak.add_argument("--workers", type=int, default=6)
    runtime_soak.add_argument("--fault-rate", type=float, default=0.05)
    runtime_soak.add_argument("--crash-rate", type=float, default=0.02)
    runtime_soak.add_argument("--seed", type=int, default=1)
    runtime_soak.set_defaults(func=cmd_runtime_soak)

    profile = sub.add_parser("profile")
    profile.add_argument("root")
    profile.set_defaults(func=cmd_profile)

    diagnose_parser = sub.add_parser("diagnose")
    diagnose_parser.add_argument("root")
    diagnose_parser.add_argument("--event-limit", type=int, default=50)
    diagnose_parser.set_defaults(func=cmd_diagnose)

    schedule = sub.add_parser("schedule")
    schedule.add_argument("root")
    schedule.add_argument("--capabilities", default="source,subtitle,voiceover,publication,cover,package")
    schedule.add_argument("--strategy", choices=sorted(STRATEGIES), default="finish-first")
    schedule.set_defaults(func=cmd_schedule_plan)

    validate = sub.add_parser("validate")
    validate.add_argument("root")
    validate.add_argument("--allow-empty", action="store_true")
    validate.add_argument("--expected-count", type=int)
    validate.add_argument("--skip-quality-record", action="store_true")
    validate.add_argument("--ffprobe")
    validate.add_argument("--workers", type=int, choices=range(1, 17), default=4)
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
    scaffold.add_argument("--lease-token")
    scaffold.add_argument("--writer-id")
    scaffold.set_defaults(func=cmd_voiceover_scaffold)
    attest = voice_sub.add_parser("attest")
    attest.add_argument("topic")
    attest.add_argument("--issuer", default="autoyy-independent-verifier")
    attest.add_argument("--run-id")
    attest.set_defaults(func=cmd_voiceover_attest)
    voice_validate = voice_sub.add_parser("validate")
    voice_validate.add_argument("root")
    voice_validate.add_argument("--skip-quality-record", action="store_true")
    voice_validate.add_argument("--workers", type=int, choices=range(1, 17), default=1)
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
    download.add_argument("--parallel", "--workers", dest="parallel", type=int, default=1)
    download.add_argument("--use-aria2", action="store_true")
    download.add_argument("--aria2-connections", type=int, default=8)
    download.add_argument("--trace", action="store_true")
    download.add_argument("--no-overlap-assets", action="store_true")
    download.add_argument("--js-runtimes")
    download.add_argument("--rate-limit")
    download.add_argument("--max-command-attempts", type=int, choices=range(1, 7), default=3)
    download.add_argument("--retry-base-seconds", type=float, default=1.0)
    download.add_argument("--retry-max-seconds", type=float, default=15.0)
    download.add_argument("--fixed-workers", action="store_true", help="disable adaptive concurrency changes")
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

    batch = sub.add_parser("batch")
    batch_sub = batch.add_subparsers(dest="batch_command", required=True)
    batch_status_parser = batch_sub.add_parser("status")
    batch_status_parser.add_argument("root")
    batch_status_parser.set_defaults(func=cmd_batch_status)
    batch_plan_parser = batch_sub.add_parser("plan")
    batch_plan_parser.add_argument("root")
    batch_plan_parser.add_argument("stage", choices=["source", "subtitle", "voiceover", "publication", "cover", "package"])
    batch_plan_parser.set_defaults(func=cmd_batch_plan)
    batch_inventory_parser = batch_sub.add_parser("inventory")
    batch_inventory_parser.add_argument("root")
    batch_inventory_parser.set_defaults(func=cmd_batch_inventory)

    work = sub.add_parser("work")
    work_sub = work.add_subparsers(dest="work_command", required=True)
    work_claim = work_sub.add_parser("claim")
    work_claim.add_argument("root")
    work_claim.add_argument("--worker-id", required=True)
    work_claim.add_argument("--stage", choices=["auto", "source", "subtitle", "voiceover", "publication", "cover", "package"], default="voiceover")
    work_claim.add_argument("--capabilities", default="source,subtitle,voiceover,publication,cover,package")
    work_claim.add_argument("--strategy", choices=sorted(STRATEGIES), default="finish-first")
    work_claim.add_argument("--topic")
    work_claim.add_argument("--lease-seconds", type=int, default=1800)
    work_claim.set_defaults(func=cmd_work_claim)
    work_heartbeat = work_sub.add_parser("heartbeat")
    work_heartbeat.add_argument("root")
    work_heartbeat.add_argument("token")
    work_heartbeat.add_argument("--lease-seconds", type=int, default=1800)
    work_heartbeat.set_defaults(func=cmd_work_heartbeat)
    work_release = work_sub.add_parser("release")
    work_release.add_argument("root")
    work_release.add_argument("token")
    work_release.set_defaults(func=cmd_work_release)
    work_status_parser = work_sub.add_parser("status")
    work_status_parser.add_argument("root")
    work_status_parser.set_defaults(func=cmd_work_status)

    transcribe = sub.add_parser("transcribe")
    transcribe.add_argument("root")
    transcribe.add_argument("--manifest")
    transcribe.add_argument("--topic", action="append", dest="topics")
    transcribe.add_argument("--backend", choices=["auto", "funasr", "whisper"], default="auto")
    transcribe.add_argument("--language", default="auto")
    transcribe.add_argument("--model-size", default="medium")
    transcribe.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    transcribe.add_argument("--beam-size", type=int, default=5)
    transcribe.add_argument("--ffmpeg-location")
    transcribe.add_argument("--ffmpeg-timeout", type=int, default=7200)
    transcribe.add_argument("--hf-endpoint")
    transcribe.add_argument("--no-vad", action="store_true")
    transcribe.add_argument("--overwrite", action="store_true")
    transcribe.add_argument("--dry-run", action="store_true")
    transcribe.add_argument("--workers", type=int, choices=range(1, 5), default=1)
    transcribe.set_defaults(func=cmd_transcribe)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = build_parser()
    values = list(sys.argv[1:] if argv is None else argv)
    if "--json" in values and values[0:1] != ["--json"]:
        values.remove("--json")
        values.insert(0, "--json")
    args = parser.parse_args(values)
    return args.func(args)
