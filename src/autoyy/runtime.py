from __future__ import annotations

import contextlib
import io
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .deliverables import validate_topic
from .download import DownloadOptions, run_download
from .media import find_primary_subtitle, find_primary_video, probe_media
from .observability import RunRecorder
from .paths import discover_topics
from .profiling import inventory_root
from .resources import ResourcePlan, detect_resources
from .scheduler import scheduler_plan
from .state import file_fingerprint, load_state, media_fingerprint, set_stage, update_state
from .subtitles import validate_srt
from .transcribe import main as transcribe_main
from .work import list_leases

AUTO_STAGES = {"source", "subtitle", "package"}
EXTERNAL_STAGES = {"voiceover", "publication", "cover"}


@dataclass(slots=True)
class RuntimeOptions:
    root: Path
    manifest: Path | None = None
    yt_dlp: str = "yt-dlp"
    ffmpeg_location: str | None = None
    cookies_from_browser: str | None = None
    proxy: str | None = None
    use_aria2: bool = False
    aria2_connections: int = 8
    js_runtimes: str | None = None
    allow_legacy_ignored: bool = False
    max_passes: int | None = None
    dry_run: bool = False
    strategy: str = "finish-first"
    download_workers: int | None = None
    asr_workers: int | None = None
    asr_device: str | None = None
    asr_model_size: str = "medium"
    hf_endpoint: str | None = None
    rate_limit: str | None = None
    max_command_attempts: int = 3


def _state_revision(root: Path) -> int:
    try:
        return int(load_state(root).get("revision") or 0)
    except FileNotFoundError:
        return 0


def _resource_payload(plan: ResourcePlan) -> dict[str, Any]:
    return {
        "cpu_count": plan.cpu_count,
        "memory_gb": plan.memory_gb,
        "gpu_name": plan.gpu_name,
        "gpu_memory_gb": plan.gpu_memory_gb,
        "asr_device": plan.asr_device,
        "asr_workers": plan.asr_workers,
        "download_workers": plan.download_workers,
        "package_workers": plan.package_workers,
    }


def reconcile_existing(options: RuntimeOptions) -> dict[str, Any]:
    root = options.root.resolve()
    ffprobe = _ffprobe(options)
    current = load_state(root, create=True)
    changes: list[dict[str, str]] = []
    issues: list[dict[str, str]] = []

    def queue(topic: str, stage: str, current_stage: dict[str, Any], status: str, fingerprint: str, reason: str) -> None:
        if (
            current_stage.get("status") == status
            and current_stage.get("fingerprint", "") == fingerprint
            and current_stage.get("reason", "") == reason
        ):
            return
        changes.append({
            "topic": topic, "stage": stage, "status": status,
            "fingerprint": fingerprint, "reason": reason,
        })

    for folder in discover_topics(root):
        record = current.get("topics", {}).get(folder.name, {}).get("stages", {})
        source_current = record.get("source", {})
        subtitle_current = record.get("subtitle", {})
        video = find_primary_video(folder)
        source_ready = source_current.get("status") == "ready"
        if video is None:
            if source_ready:
                queue(folder.name, "source", source_current, "failed", "missing", "source artifact missing")
            source_ready = False
        elif not ffprobe:
            issues.append({"topic": folder.name, "code": "FFPROBE_NOT_FOUND", "message": "cannot reconcile existing video"})
            source_ready = False
        else:
            try:
                probe_media(video, ffprobe, timeout=60)
                fingerprint = media_fingerprint(video)
                source_ready = True
                queue(folder.name, "source", source_current, "ready", fingerprint, "")
            except RuntimeError as exc:
                source_ready = False
                queue(
                    folder.name, "source", source_current, "failed", media_fingerprint(video),
                    f"video unreadable: {exc}",
                )

        subtitle = find_primary_subtitle(folder)
        if subtitle is None:
            if subtitle_current.get("status") == "ready":
                queue(folder.name, "subtitle", subtitle_current, "failed", "missing", "subtitle artifact missing")
            continue
        report = validate_srt(subtitle)
        fingerprint = file_fingerprint(subtitle)
        if not report["valid"]:
            queue(
                folder.name, "subtitle", subtitle_current, "failed", fingerprint,
                "; ".join(report["issues"][:3]),
            )
        elif source_ready:
            queue(folder.name, "subtitle", subtitle_current, "ready", fingerprint, "")
        elif subtitle_current.get("status") == "ready":
            queue(folder.name, "subtitle", subtitle_current, "blocked", fingerprint, "source is not verified ready")

    if changes and not options.dry_run:
        def apply(state):
            for change in changes:
                set_stage(
                    state, change["topic"], change["stage"], change["status"],
                    fingerprint=change["fingerprint"], reason=change["reason"],
                )
        update_state(root, apply, create=True)
    return {
        "changed_count": len(changes), "changes": changes,
        "issues": issues, "dry_run": options.dry_run,
    }


def runtime_plan(options: RuntimeOptions) -> dict[str, Any]:
    root = options.root.resolve()
    inventory = inventory_root(root)
    resources = detect_resources()
    active_leases = list_leases(root)
    schedule = scheduler_plan(
        root,
        active_leases=active_leases,
        strategy=options.strategy,
    )
    automated = [item for item in schedule["candidates"] if item["stage"] in AUTO_STAGES]
    external = [item for item in schedule["candidates"] if item["stage"] in EXTERNAL_STAGES]
    blockers: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    blocked_stages: list[dict[str, str]] = []
    try:
        current_state = load_state(root)
    except FileNotFoundError:
        current_state = {"topics": {}}
    for topic in discover_topics(root):
        stages = current_state.get("topics", {}).get(topic.name, {}).get("stages", {})
        for stage, item in stages.items():
            if item.get("status") == "blocked":
                blocked_stages.append({
                    "topic": topic.name,
                    "stage": stage,
                    "reason": str(item.get("reason") or ""),
                })
    if inventory["legacy_candidate_count"] and not options.allow_legacy_ignored:
        blockers.append({
            "code": "LEGACY_TOPIC_NAMES_IGNORED",
            "count": inventory["legacy_candidate_count"],
            "hint": "Migrate/rename legacy topic directories or pass --allow-legacy-ignored explicitly.",
        })
    if any(item["stage"] == "source" for item in automated) and options.manifest is None:
        deferred.append({"code": "MANIFEST_REQUIRED", "hint": "Source-pending topics need --manifest; other runnable stages may continue."})
    return {
        "root": str(root),
        "inventory": inventory,
        "resources": _resource_payload(resources),
        "schedule": schedule,
        "active_leases": active_leases,
        "automated_candidates": automated,
        "external_candidates": external,
        "blockers": blockers,
        "deferred": deferred,
        "blocked_stages": blocked_stages,
        "runnable": not blockers,
    }


def _run_source(options: RuntimeOptions, resources: ResourcePlan, run_id: str) -> dict[str, Any]:
    if options.manifest is None:
        return {"code": 1, "stage": "source", "status": "waiting", "error_code": "MANIFEST_REQUIRED"}
    workers = options.download_workers or resources.download_workers
    code, result = run_download(DownloadOptions(
        manifest=options.manifest,
        output_root=options.root,
        yt_dlp=options.yt_dlp,
        ffmpeg_location=options.ffmpeg_location,
        cookies_from_browser=options.cookies_from_browser,
        proxy=options.proxy,
        parallel=workers,
        use_aria2=options.use_aria2,
        aria2_connections=options.aria2_connections,
        js_runtimes=options.js_runtimes,
        rate_limit=options.rate_limit,
        max_command_attempts=options.max_command_attempts,
        run_id=run_id,
        plan_only=options.dry_run,
    ))
    return {
        "code": code,
        "stage": "source",
        "status": "ready" if code == 0 else "failed",
        "error_code": str(result.get("error_code", "")),
        "result": result,
    }


def _transcribe_args(options: RuntimeOptions, resources: ResourcePlan, run_id: str, *, device: str | None = None, topic: str | None = None) -> list[str]:
    workers = options.asr_workers or resources.asr_workers
    chosen_device = device or options.asr_device or resources.asr_device
    args = [
        str(options.root), "--backend", "auto", "--language", "auto",
        "--model-size", options.asr_model_size,
        "--device", chosen_device, "--workers", str(workers), "--run-id", run_id,
    ]
    if options.manifest:
        args += ["--manifest", str(options.manifest)]
    if topic:
        args += ["--topic", topic]
    if options.ffmpeg_location:
        args += ["--ffmpeg-location", options.ffmpeg_location]
    if options.hf_endpoint:
        args += ["--hf-endpoint", options.hf_endpoint]
    if options.dry_run:
        args.append("--dry-run")
    return args


def _run_subtitle(options: RuntimeOptions, resources: ResourcePlan, run_id: str, topic: str | None = None) -> dict[str, Any]:
    capture = io.StringIO()
    chosen_device = options.asr_device or resources.asr_device
    with contextlib.redirect_stdout(capture):
        code = transcribe_main(_transcribe_args(options, resources, run_id, topic=topic))
    output = capture.getvalue()
    fallback = False
    lowered = output.lower()
    if code != 0 and chosen_device == "cuda" and any(token in lowered for token in ("cuda", "cudnn", "out of memory", "oom")):
        fallback = True
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            code = transcribe_main(_transcribe_args(options, resources, run_id + "-cpu", device="cpu", topic=topic))
        output += "\n[cpu-fallback]\n" + capture.getvalue()
    return {
        "code": code, "stage": "subtitle", "status": "ready" if code == 0 else "failed",
        "device": "cpu" if fallback else chosen_device, "cpu_fallback": fallback,
        "output_tail": output.splitlines()[-20:],
    }


def _ffprobe(options: RuntimeOptions) -> str | None:
    if options.ffmpeg_location:
        candidate = Path(options.ffmpeg_location) / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffprobe")


def _run_package(options: RuntimeOptions, resources: ResourcePlan, topics: list[str]) -> dict[str, Any]:
    ffprobe = _ffprobe(options)
    if not ffprobe:
        return {"code": 2, "stage": "package", "status": "error", "error_code": "FFPROBE_NOT_FOUND"}
    from concurrent.futures import ThreadPoolExecutor

    folders = [options.root / topic for topic in topics]
    workers = min(resources.package_workers, max(1, len(folders)))
    if workers == 1:
        results = [validate_topic(folder, require_quality=True, ffprobe=ffprobe) for folder in folders]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="autoyy-runtime-package") as pool:
            results = list(pool.map(
                lambda folder: validate_topic(folder, require_quality=True, ffprobe=ffprobe), folders
            ))
    if not options.dry_run:
        def apply(state):
            for item in results:
                set_stage(
                    state, item["folder"], "package", "ready" if item["complete"] else "failed",
                    reason="" if item["complete"] else "; ".join(item["issues"][:3]),
                )
        update_state(options.root, apply, create=True)
    failed = sum(not item["complete"] for item in results)
    return {
        "code": 0 if failed == 0 else 1,
        "stage": "package", "status": "ready" if failed == 0 else "failed",
        "error_code": "" if failed == 0 else "PACKAGE_GATE_FAILED",
        "complete_count": len(results) - failed, "failed_count": failed,
        "results": results,
    }


def runtime_tick(options: RuntimeOptions, *, run_id: str = "", reconcile: bool = True, skip_candidates: set[tuple[str, str]] | None = None) -> tuple[int, dict[str, Any]]:
    options.root = options.root.resolve()
    reconciliation = reconcile_existing(options) if reconcile else {"changed_count": 0, "changes": [], "issues": [], "skipped": True}
    plan = runtime_plan(options)
    if plan["blockers"]:
        return 2, {"status": "blocked", "reconciliation": reconciliation, **plan}
    if options.dry_run:
        return 0, {"status": "planned", "reconciliation": reconciliation, **plan}
    resources = detect_resources()
    skipped = skip_candidates or set()
    automated = [item for item in plan["automated_candidates"] if (item["topic"], item["stage"]) not in skipped]
    if not automated:
        if plan["blocked_stages"]:
            status, code = "blocked_work", 1
        elif plan["active_leases"]:
            status, code = "waiting_active", 1
        elif skipped and plan["automated_candidates"]:
            status, code = "deferred_failures", 1
        elif plan["external_candidates"]:
            status, code = "waiting_external", 1
        elif plan["deferred"]:
            status, code = "waiting_input", 1
        else:
            status, code = "idle", 0
        return code, {"status": status, "reconciliation": reconciliation, "skipped_candidates": sorted(skipped), **plan}
    candidate = automated[0]
    stage = candidate["stage"]
    before = _state_revision(options.root)
    if stage == "source":
        action = _run_source(options, resources, run_id)
    elif stage == "subtitle":
        action = _run_subtitle(options, resources, run_id, candidate["topic"])
    else:
        package_topics = [item["topic"] for item in automated if item["stage"] == "package"]
        action = _run_package(options, resources, package_topics)
    after = _state_revision(options.root)
    refreshed = runtime_plan(options)
    result = {
        "status": "progress" if after != before else action["status"],
        "action": action, "candidate": candidate, "reconciliation": reconciliation, "state_revision_before": before,
        "state_revision_after": after, "next": refreshed["schedule"]["next"],
        "external_candidates": refreshed["external_candidates"],
        "blockers": refreshed["blockers"],
        "deferred": refreshed["deferred"],
    }
    return int(action["code"]), result


def runtime_run(options: RuntimeOptions) -> tuple[int, dict[str, Any]]:
    options.root = options.root.resolve()
    recorder = RunRecorder(options.root, "runtime")
    passes: list[dict[str, Any]] = []
    failed_this_run: set[tuple[str, str]] = set()
    initial = runtime_plan(options)
    auto_pass_limit = max(20, int(initial["inventory"]["recognized_topic_count"]) * 3 + 10)
    pass_limit = options.max_passes or auto_pass_limit
    if pass_limit < 1:
        return 2, {"run_id": recorder.run_id, "status": "invalid", "error_code": "INVALID_MAX_PASSES"}
    final_code = 0
    for index in range(1, pass_limit + 1):
        code, result = runtime_tick(
            options,
            run_id=recorder.run_id,
            reconcile=index == 1,
            skip_candidates=failed_this_run,
        )
        result["pass"] = index
        passes.append(result)
        recorder.event(
            "runtime_pass", stage=str(result.get("action", {}).get("stage", "")),
            status=str(result.get("status", "")), code=str(result.get("action", {}).get("error_code", "")),
            details={"pass": index, "code": code},
        )
        if options.dry_run:
            final_code = 0
            break
        if code == 2:
            final_code = 2
            break
        candidate = result.get("candidate") or {}
        if code == 1 and candidate.get("stage") == "subtitle":
            failed_this_run.add((str(candidate.get("topic")), "subtitle"))
        refreshed = runtime_plan(options)
        if refreshed["blockers"]:
            final_code = 2
            break
        remaining_auto = [
            item for item in refreshed["automated_candidates"]
            if (item["topic"], item["stage"]) not in failed_this_run
        ]
        if not remaining_auto:
            final_code = 1 if (refreshed["external_candidates"] or failed_this_run or refreshed["deferred"] or refreshed["active_leases"] or refreshed["blocked_stages"]) else 0
            break
        if code == 1:
            action = result.get("action", {})
            if action.get("stage") == "source":
                rows = action.get("result", {}).get("results", [])
                if any(row.get("video") is True for row in rows):
                    continue
            if candidate.get("stage") == "subtitle":
                continue
            final_code = 1
            break
        if result.get("state_revision_after") == result.get("state_revision_before"):
            final_code = 1
            break
    else:
        final_code = 1
    final = runtime_plan(options)
    if options.dry_run and final_code != 2:
        status = "planned"
        final_code = 0
    elif final["blocked_stages"]:
        status = "blocked_work"
        final_code = 1
    elif final["active_leases"]:
        status = "waiting_active"
        final_code = 1
    elif failed_this_run:
        status = "deferred_failures"
        final_code = 1
    elif not final["automated_candidates"] and not final["external_candidates"] and not final["deferred"]:
        status = "complete"
    elif not final["automated_candidates"] and final["external_candidates"]:
        status = "waiting_external"
        final_code = 1
    elif final["deferred"]:
        status = "waiting_input"
        final_code = 1
    else:
        status = "incomplete"
        final_code = 1
    recorder.finish(
        status="success" if final_code == 0 else "blocked" if final_code == 1 else "error",
        code="" if final_code == 0 else "RUNTIME_INCOMPLETE" if final_code == 1 else "RUNTIME_CONFIG_ERROR",
        details={"passes": len(passes), "status": status, "deferred_failures": len(failed_this_run)},
    )
    return final_code, {
        "run_id": recorder.run_id,
        "status": status,
        "pass_count": len(passes),
        "pass_limit": pass_limit,
        "deferred_failures": sorted(failed_this_run),
        "passes": passes,
        "final": final,
    }
