#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autoyy.config import peer_library
from autoyy.peer import (
    FIELDS,
    PATTERNS,
    load_library,
    number,
    pattern_stats,
    save_library,
    validate_row,
)


def resolve_library(value: Path | None) -> Path:
    return value.resolve() if value else peer_library(Path.cwd()).resolve()


def cmd_list(rows: list[dict[str, str]], args: argparse.Namespace) -> int:
    if args.category:
        rows = [row for row in rows if (row.get("topic_category") or "").strip() == args.category]
    if args.platform:
        rows = [row for row in rows if (row.get("platform") or "").strip() == args.platform]
    rows.sort(key=lambda row: number(row.get("views")), reverse=True)
    for row in rows[: args.top or len(rows)]:
        print(f"{row.get('platform','')}\t{row.get('hook_pattern','')}\t{number(row.get('views')):.0f}\t{row.get('title','')}")
    print(f"count={len(rows)}")
    return 0


def cmd_patterns(rows: list[dict[str, str]], args: argparse.Namespace) -> int:
    since = date.fromisoformat(args.since) if args.since else None
    stats = pattern_stats(rows, platform=args.platform, category=args.category, since=since)
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        for item in stats[: args.top or len(stats)]:
            print(f"{item['platform']}\t{item['pattern']}\tcount={item['count']}\tmedian={item['median_views']:.0f}\tmean={item['mean_views']:.0f}\tp75={item['p75_views']:.0f}\tpeak={item['peak_views']:.0f}\tconfidence={item['confidence']}")
    return 0


def merge_row(rows: list[dict[str, str]], incoming: dict[str, str]) -> tuple[str, list[dict[str, str]]]:
    key = ((incoming.get("platform") or "").strip(), (incoming.get("title") or "").strip())
    for old in rows:
        old_key = ((old.get("platform") or "").strip(), (old.get("title") or "").strip())
        if old_key == key:
            changed = False
            for field in FIELDS:
                value = (incoming.get(field) or "").strip()
                if value and value != (old.get(field) or ""):
                    old[field] = value
                    changed = True
            return ("update" if changed else "no-change"), rows
    rows.append({field: incoming.get(field, "") for field in FIELDS})
    return "add", rows


def cmd_add(rows: list[dict[str, str]], path: Path, args: argparse.Namespace) -> int:
    incoming = {
        "platform": args.platform or "",
        "title": args.title or "",
        "hashtags": args.tags or "",
        "views": str(args.views or ""),
        "likes": str(args.likes or ""),
        "comments": str(args.comments or ""),
        "topic_category": args.category or "",
        "hook_pattern": args.pattern or "",
        "published_at": args.published_at or date.today().isoformat(),
        "source": args.source or "",
    }
    issues = validate_row(incoming)
    hard = [item for item in issues if not item.startswith("unknown hook_pattern")]
    if hard:
        print("; ".join(issues), file=sys.stderr)
        return 2
    if issues:
        print("WARNING: " + "; ".join(issues), file=sys.stderr)
    action, rows = merge_row(rows, incoming)
    print(f"{action}: {incoming['title']}")
    if not args.dry_run:
        save_library(path, rows, backup=args.backup)
    return 0


def cmd_import(rows: list[dict[str, str]], path: Path, args: argparse.Namespace) -> int:
    source = Path(args.import_file)
    if not source.is_file():
        print(f"import file missing: {source}", file=sys.stderr)
        return 2
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or any(field not in reader.fieldnames for field in FIELDS):
            print("import CSV headers invalid", file=sys.stderr)
            return 2
        incoming_rows = list(reader)
    failures = 0
    changes = {"add": 0, "update": 0, "no-change": 0}
    for incoming in incoming_rows:
        issues = validate_row(incoming)
        hard = [item for item in issues if not item.startswith("unknown hook_pattern")]
        if hard:
            failures += 1
            print(f"SKIP {incoming.get('title','')}: {'; '.join(issues)}", file=sys.stderr)
            continue
        if issues:
            print(f"WARNING {incoming.get('title','')}: {'; '.join(issues)}", file=sys.stderr)
        action, rows = merge_row(rows, incoming)
        changes[action] += 1
    print(json.dumps({"changes": changes, "failures": failures}, ensure_ascii=False))
    if not args.dry_run and not failures:
        save_library(path, rows, backup=args.backup)
    return 1 if failures else 0


def cmd_template(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.DictWriter(handle, fieldnames=FIELDS).writeheader()
    print(path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoYY peer-hit evidence library")
    parser.add_argument("--library", type=Path, help="Dynamic library path; default ./.autoyy/peer-hit-library.csv")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--patterns", action="store_true")
    parser.add_argument("--add", action="store_true")
    parser.add_argument("--import", dest="import_file")
    parser.add_argument("--template", type=Path)
    parser.add_argument("--platform")
    parser.add_argument("--title")
    parser.add_argument("--tags")
    parser.add_argument("--views", type=float)
    parser.add_argument("--likes", type=float)
    parser.add_argument("--comments", type=float)
    parser.add_argument("--category")
    parser.add_argument("--pattern", help="Suggested: " + " / ".join(sorted(PATTERNS)))
    parser.add_argument("--published-at")
    parser.add_argument("--source")
    parser.add_argument("--since", help="YYYY-MM-DD")
    parser.add_argument("--top", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backup", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.template:
        return cmd_template(args.template)
    path = resolve_library(args.library)
    try:
        rows = load_library(path)
        if args.patterns:
            return cmd_patterns(rows, args)
        if args.add:
            return cmd_add(rows, path, args)
        if args.import_file:
            return cmd_import(rows, path, args)
        return cmd_list(rows, args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
