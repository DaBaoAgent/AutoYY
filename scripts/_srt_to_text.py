#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autoyy.media import find_primary_subtitle
from autoyy.paths import discover_topics
from autoyy.subtitles import srt_to_text


def main() -> int:
    parser = argparse.ArgumentParser(description="Compatibility helper: convert topic SRT files to plain transcripts")
    parser.add_argument("root", type=Path)
    parser.add_argument("--output-name", default="_transcript.txt")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        return 2
    failures = 0
    for topic in discover_topics(root):
        subtitle = find_primary_subtitle(topic)
        if subtitle is None:
            print(f"SKIP {topic.name}: no SRT")
            continue
        output = topic / args.output_name
        if output.exists() and not args.force:
            print(f"SKIP {topic.name}: {output.name} exists")
            continue
        output.write_text(srt_to_text(subtitle), encoding="utf-8")
        print(f"OK {topic.name}: {output.name}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
