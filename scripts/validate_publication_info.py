#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autoyy.publication import validate_publication_file
from autoyy.result import EXIT_FAILED, EXIT_OK, EXIT_USAGE


def main() -> int:
    parser = argparse.ArgumentParser(description="Recursively validate AutoYY publication information files.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--expected-count", type=int)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        print(f"Root does not exist: {root}", file=sys.stderr)
        return EXIT_USAGE
    files = sorted(root.rglob("发布信息.txt"))
    issues: list[str] = []
    if not files and not args.allow_empty:
        issues.append("no 发布信息.txt files found")
    if args.expected_count is not None and len(files) != args.expected_count:
        issues.append(f"file count {len(files)}, expected {args.expected_count}")
    results = []
    for path in files:
        result = validate_publication_file(path)
        results.append({"file": str(path.relative_to(root)), **result})
    invalid = sum(not item.get("valid", False) for item in results)
    summary = {"root": str(root), "file_count": len(results), "valid_count": len(results) - invalid, "invalid_count": invalid, "issues": issues, "results": results}
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.write_text(rendered, encoding="utf-8")
    print(rendered)
    return EXIT_OK if not issues and invalid == 0 else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
