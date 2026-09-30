#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autoyy.paths import safe_child_path
from autoyy.state import file_fingerprint, load_state, recover_running, save_state, set_stage

HEAD_3X4 = "电影级写实纪录片封面，3:4竖版构图。画面："
HEAD_4X3 = "电影级写实纪录片封面，4:3横版构图。画面："
TEXT_3X4 = "画面正上方约三分之一处居中排列两行书法大字，文字醒目、笔画清晰、无变形无错字：第一行主标题「{m}」——特大号金黄色粗毛笔书法字，黑色细描边，柔和黑色投影；第二行副标题「{s}」——中号白色毛笔书法字，宽度约为主标题三分之二。文字有安全边距，除这两行外无其他文字、英文、logo或水印。背景高清写实、主体突出。"
TEXT_4X3 = "画面上部居中排列两行书法大字，文字醒目、笔画清晰、无变形无错字：第一行主标题「{m}」——特大号金黄色粗毛笔书法字，黑色细描边，柔和黑色投影；第二行副标题「{s}」——中号白色毛笔书法字，宽度约为主标题三分之二。文字有安全边距，除这两行外无其他文字、英文、logo或水印。背景高清写实、主体突出。"
STANDARD_COVERS = {"封面-3比4.png", "封面-3比4.jpg", "封面-3比4.jpeg", "封面-4比3.png", "封面-4比3.jpg", "封面-4比3.jpeg"}


def build(main: str, sub: str, scene_v: str, scene_h: str) -> str:
    return HEAD_3X4 + scene_v + TEXT_3X4.format(m=main, s=sub) + "\n\n" + HEAD_4X3 + scene_h + TEXT_4X3.format(m=main, s=sub)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate strict Jimeng cover prompt files")
    parser.add_argument("root", type=Path)
    parser.add_argument("--csv", type=Path, default=Path("jimeng-cover-input.csv"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir() or not args.csv.is_file():
        print("root directory or CSV missing", file=sys.stderr)
        return 2
    with args.csv.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"目录名", "主标题", "副标题", "竖版画面", "横版画面"}
    if not rows or not required.issubset(rows[0].keys()):
        print("CSV empty or missing required headers", file=sys.stderr)
        return 2
    try:
        state = load_state(root, create=True)
        recover_running(state)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    failures = 0
    written = 0
    for line_no, row in enumerate(rows, 2):
        name = (row.get("目录名") or "").strip()
        main_title = (row.get("主标题") or "").strip()
        subtitle = (row.get("副标题") or "").strip()
        scene_v = (row.get("竖版画面") or "").strip()
        scene_h = (row.get("横版画面") or "").strip()
        problems = []
        if not all((name, main_title, subtitle, scene_v, scene_h)):
            problems.append("required field missing")
        if len(main_title) != 6:
            problems.append(f"main title length {len(main_title)}, expected 6")
        if len(subtitle) != 8:
            problems.append(f"subtitle length {len(subtitle)}, expected 8")
        try:
            target = safe_child_path(root, name)
        except ValueError as exc:
            target = root
            problems.append(str(exc))
        if not target.is_dir():
            problems.append("topic directory missing")
        if problems:
            failures += 1
            print(f"FAIL row {line_no} {name}: {'; '.join(problems)}", file=sys.stderr)
            continue
        prompt_path = target / "封面提示词-即梦.txt"
        has_standard_cover = any((target / cover).is_file() and (target / cover).stat().st_size > 0 for cover in STANDARD_COVERS)
        prompt_exists = prompt_path.is_file() and prompt_path.stat().st_size > 0
        if not args.force and (prompt_exists or has_standard_cover):
            print(f"SKIP {name}: approved/standard prompt or cover already exists")
            continue
        content = build(main_title, subtitle, scene_v, scene_h)
        if args.dry_run:
            print(f"DRY {prompt_path}")
            written += 1
            continue
        tmp = prompt_path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, prompt_path)
        set_stage(state, name, "cover", "pending", fingerprint=file_fingerprint(prompt_path), reason="prompt generated; awaiting covers")
        written += 1
        print(f"OK {prompt_path}")
    if not args.dry_run:
        save_state(root, state)
    print(f"summary written={written} failed={failures} total={len(rows)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
