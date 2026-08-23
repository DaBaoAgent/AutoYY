#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同行爆款发布信息库：学习、累积、进化 AutoYY 的发布标题与标签生成。

思路（持续进化闭环）:
  1. 平时把看到/实测的同行爆款二创纪录片「标题+标签+数据」记进库（--add / --import）
  2. 生成某选题的 发布信息.txt 前，用 --patterns 看哪个钩子模式（hook_pattern）
     历史平均表现最好，优先模仿该模式写标题（SKILL.md Stage 7 流程）
  3. 自己发布后把实绩回流进库（--import 一行），库越大、模式排名越准 = 持续进化

用法:
  python scripts/peer_hit_library.py --list [--category 分类] [--top N]
  python scripts/peer_hit_library.py --patterns [--top N]        # 模式表现排名（进化信号）
  python scripts/peer_hit_library.py --add --platform douyin --title "…" --tags "#a #b" \
        --views 120000 --likes 8000 --category 核潜艇 --pattern 数字冲击 [--source url]
  python scripts/peer_hit_library.py --import 同行爆款.csv      # 批量导入（按 title+platform 去重合并）
  python scripts/peer_hit_library.py --template 输出模板.csv     # 导出空白导入模板

库文件: assets/peer-hit-library.csv（UTF-8；可随仓库 git 同步）
CSV 列: platform,title,hashtags,views,likes,comments,topic_category,hook_pattern,published_at,source
hook_pattern 建议取值: 数字冲击 / 对比反差 / 信息缺口设问 / 后果威胁 / 身份反转 / 反常识 / 悬念事件 / 情感共鸣
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LIB_PATH = Path(__file__).resolve().parent.parent / "assets" / "peer-hit-library.csv"
FIELDS = [
    "platform", "title", "hashtags", "views", "likes", "comments",
    "topic_category", "hook_pattern", "published_at", "source",
]
PATTERNS = [
    "数字冲击", "对比反差", "信息缺口设问", "后果威胁",
    "身份反转", "反常识", "悬念事件", "情感共鸣",
]


def load_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def save_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def num(value) -> float:
    try:
        return float(str(value or 0).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def cmd_list(args: argparse.Namespace) -> int:
    rows = load_rows(LIB_PATH)
    if not rows:
        print(f"库为空：{LIB_PATH}\n先用 --add / --import 录入同行爆款，或 --template 拿导入模板。")
        return 0
    if args.category:
        rows = [r for r in rows if (r.get("topic_category") or "").strip() == args.category]
    rows.sort(key=lambda r: num(r.get("views")), reverse=True)
    limit = args.top or len(rows)
    h_p, h_pat, h_v, h_t, h_c = "平台", "钩子模式", "播放", "标题", "分类"
    print(f"{h_p:<8}{h_pat:<12}{h_v:>10}  {h_t}  [{h_c}]")
    print("-" * 100)
    for row in rows[:limit]:
        print(
            f"{(row.get('platform') or ''):<8}"
            f"{(row.get('hook_pattern') or ''):<12}"
            f"{num(row.get('views')):>10.0f}  "
            f"{(row.get('title') or '')[:40]}  [{row.get('topic_category') or ''}]"
        )
    print(f"\n共 {len(rows)} 条（库文件 {LIB_PATH}）")
    return 0


def cmd_patterns(args: argparse.Namespace) -> int:
    """按钩子模式聚合平均播放/点赞，输出表现排名 = 进化信号。"""
    rows = load_rows(LIB_PATH)
    if not rows:
        print(f"库为空：{LIB_PATH}")
        return 0
    stats: dict[str, list[float]] = {}
    for row in rows:
        pattern = (row.get("hook_pattern") or "未分类").strip()
        stats.setdefault(pattern, []).append(num(row.get("views")))
    ranked = sorted(
        ((p, len(v), sum(v) / len(v), max(v)) for p, v in stats.items()),
        key=lambda item: item[2], reverse=True,
    )
    h_r, h_p, h_n, h_avg, h_peak = "排名", "钩子模式", "样本", "平均播放", "最高播放"
    print(f"{h_r:<4}{h_p:<14}{h_n:>5}{h_avg:>12}{h_peak:>12}")
    print("-" * 60)
    for idx, (pattern, count, avg, peak) in enumerate(ranked, start=1):
        print(f"{idx:<4}{pattern:<14}{count:>5}{avg:>12.0f}{peak:>12.0f}")
    print("\n生成发布信息时优先参考排名靠前的钩子模式（库持续录入后排名自动更新）。")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    if not args.title:
        print("--title 必填", file=sys.stderr)
        return 2
    rows = load_rows(LIB_PATH)
    row = {
        "platform": args.platform or "",
        "title": args.title.strip(),
        "hashtags": (args.tags or "").strip(),
        "views": args.views or "",
        "likes": args.likes or "",
        "comments": args.comments or "",
        "topic_category": (args.category or "").strip(),
        "hook_pattern": (args.pattern or "").strip(),
        "published_at": args.published_at or date.today().isoformat(),
        "source": (args.source or "").strip(),
    }
    # 按 platform+title 去重合并（更新旧记录）
    key = (row["platform"], row["title"])
    rows = [r for r in rows if (r.get("platform", ""), r.get("title", "")) != key]
    rows.append(row)
    rows.sort(key=lambda r: num(r.get("views")), reverse=True)
    save_rows(LIB_PATH, rows)
    print(f"已入库 {len(rows)} 条（{LIB_PATH}）")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    src = Path(args.import_file)
    if not src.is_file():
        print(f"导入文件不存在: {src}", file=sys.stderr)
        return 2
    with open(src, encoding="utf-8-sig", newline="") as handle:
        incoming = [dict(row) for row in csv.DictReader(handle)]
    if not incoming:
        print("导入文件无数据行", file=sys.stderr)
        return 2
    rows = load_rows(LIB_PATH)
    existing = {(r.get("platform", ""), r.get("title", "")) for r in rows}
    added = 0
    updated = 0
    for row in incoming:
        if not (row.get("title") or "").strip():
            continue
        key = (row.get("platform", ""), row.get("title", "").strip())
        if key in existing:
            # 已有记录：保留库中旧值，仅当新行有 views 且更大时合并表现数据
            for old in rows:
                if (old.get("platform", ""), old.get("title", "")) == key:
                    if num(row.get("views")) > num(old.get("views")):
                        old["views"] = row.get("views", old.get("views", ""))
                        old["likes"] = row.get("likes", old.get("likes", ""))
                        old["comments"] = row.get("comments", old.get("comments", ""))
                    if not old.get("hook_pattern") and row.get("hook_pattern"):
                        old["hook_pattern"] = row["hook_pattern"]
                    if not old.get("topic_category") and row.get("topic_category"):
                        old["topic_category"] = row["topic_category"]
                    updated += 1
                    break
        else:
            row["title"] = row["title"].strip()
            rows.append(row)
            existing.add(key)
            added += 1
    rows.sort(key=lambda r: num(r.get("views")), reverse=True)
    save_rows(LIB_PATH, rows)
    print(f"导入完成: 新增 {added}，合并更新 {updated}，库共 {len(rows)} 条")
    return 0


def cmd_template(args: argparse.Namespace) -> int:
    out = Path(args.template_file)
    with open(out, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow({
            "platform": "douyin", "title": "在零下60度的核潜艇里，士兵凭什么一待就是90天？",
            "hashtags": "#核潜艇 #军事 #纪录片 #冷知识 #涨知识",
            "views": "1500000", "likes": "98000", "comments": "3200",
            "topic_category": "军事装备", "hook_pattern": "数字冲击",
            "published_at": "2026-08-01", "source": "https://www.douyin.com/video/xxxx",
        })
    print(f"模板已写出: {out}")
    print("hook_pattern 建议取值: " + " / ".join(PATTERNS))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AutoYY 同行爆款发布信息库（学习+进化）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--list", action="store_true", help="按播放量列出库内爆款")
    parser.add_argument("--patterns", action="store_true", help="钩子模式表现排名（进化信号）")
    parser.add_argument("--add", action="store_true", help="添加一条")
    parser.add_argument("--import", dest="import_file", metavar="CSV", help="从 CSV 批量导入")
    parser.add_argument("--template", dest="template_file", metavar="CSV", help="导出导入模板")
    parser.add_argument("--platform", help="平台 douyin/kuaishou/bilibili 等")
    parser.add_argument("--title", help="爆款标题")
    parser.add_argument("--tags", help="标签串，如 '#a #b #c'")
    parser.add_argument("--views", type=float, help="播放量")
    parser.add_argument("--likes", type=float, help="点赞数")
    parser.add_argument("--comments", type=float, help="评论数")
    parser.add_argument("--category", help="选题分类（如 军事装备/深海/航空）")
    parser.add_argument("--pattern", help="钩子模式：" + " / ".join(PATTERNS))
    parser.add_argument("--published-at", help="发布日期 YYYY-MM-DD")
    parser.add_argument("--source", help="来源链接")
    parser.add_argument("--top", type=int, help="只显示前 N 条")
    args = parser.parse_args()

    if args.add:
        return cmd_add(args)
    if args.import_file:
        return cmd_import(args)
    if args.template_file:
        return cmd_template(args)
    if args.patterns:
        return cmd_patterns(args)
    return cmd_list(args)


if __name__ == "__main__":
    raise SystemExit(main())
