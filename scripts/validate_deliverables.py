#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autoyy.deliverables import validate_root
from autoyy.paths import discover_topics
from autoyy.result import EXIT_FAILED, EXIT_OK, EXIT_USAGE


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate documentary topic deliverable folders.")
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--ffprobe-location")
    parser.add_argument("--workers", type=int, choices=range(1, 17), default=1)
    parser.add_argument("--skip-quality-record", action="store_true", help="Compatibility only; release validation must not use this")
    args = parser.parse_args()
    root = args.output_root.resolve()
    if not root.is_dir():
        print(f"Output root does not exist: {root}", file=sys.stderr)
        return EXIT_USAGE

    folders = discover_topics(root)
    ffprobe = None
    if folders:
        ffprobe = shutil.which("ffprobe")
        if args.ffprobe_location:
            candidate = Path(args.ffprobe_location) / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
            if candidate.is_file():
                ffprobe = str(candidate)
        if not ffprobe:
            print("ffprobe is required for final media validation", file=sys.stderr)
            return EXIT_USAGE

    summary = validate_root(
        root,
        allow_empty=args.allow_empty,
        expected_count=args.expected_count,
        require_quality=not args.skip_quality_record,
        ffprobe=ffprobe,
        workers=args.workers,
    )
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered, encoding="utf-8")
    print(rendered)
    return EXIT_OK if summary["valid"] else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
