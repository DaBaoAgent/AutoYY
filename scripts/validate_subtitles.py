#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autoyy.manifest import load_manifest
from autoyy.media import find_primary_subtitle, find_primary_video, probe_media
from autoyy.paths import discover_topics, safe_child_path
from autoyy.result import EXIT_FAILED, EXIT_OK, EXIT_USAGE
from autoyy.subtitles import validate_srt


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoYY subtitle validation: syntax, timing, overlap and media coverage")
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--max-drift-pct", type=float, default=5.0)
    parser.add_argument("--overlap-tolerance", type=float, default=0.15)
    parser.add_argument("--ffprobe-location")
    parser.add_argument("--ffprobe-timeout", type=int, default=120)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--expected-count", type=int)
    args = parser.parse_args()
    root = args.output_root.resolve()
    if not root.is_dir():
        print(f"Directory does not exist: {root}", file=sys.stderr)
        return EXIT_USAGE
    ffprobe = shutil.which("ffprobe")
    if args.ffprobe_location:
        candidate = Path(args.ffprobe_location) / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        if candidate.is_file():
            ffprobe = str(candidate)
    if not ffprobe:
        print("ffprobe not found; use --ffprobe-location", file=sys.stderr)
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
    global_issues: list[str] = []
    if not folders and not args.allow_empty:
        global_issues.append("no topic directories")
    if args.expected_count is not None and len(folders) != args.expected_count:
        global_issues.append(f"topic count {len(folders)}, expected {args.expected_count}")
    results = []
    for folder in folders:
        issues: list[str] = []
        if not folder.is_dir():
            results.append({"folder": folder.name, "valid": False, "issues": ["topic directory missing"]})
            continue
        video = find_primary_video(folder)
        subtitle = find_primary_subtitle(folder)
        if video is None:
            issues.append("video missing/empty")
        if subtitle is None:
            issues.append("subtitle missing/empty")
            results.append({"folder": folder.name, "valid": False, "issues": issues})
            continue
        duration = None
        if video:
            try:
                duration = probe_media(video, ffprobe, timeout=args.ffprobe_timeout)["duration"]
            except RuntimeError as exc:
                issues.append(str(exc))
        report = validate_srt(subtitle, video_duration=duration, max_drift_pct=args.max_drift_pct, overlap_tolerance=args.overlap_tolerance)
        report["issues"] = issues + report["issues"]
        report["valid"] = not report["issues"]
        results.append({"folder": folder.name, **report})
    invalid = sum(not item["valid"] for item in results)
    summary = {"root": str(root), "folder_count": len(results), "valid_count": len(results) - invalid, "invalid_count": invalid, "issues": global_issues, "results": results}
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.write_text(rendered, encoding="utf-8")
    print(rendered)
    return EXIT_OK if not global_issues and invalid == 0 else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
