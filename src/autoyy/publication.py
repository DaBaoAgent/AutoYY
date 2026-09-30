from __future__ import annotations

import re
from pathlib import Path

LEGACY_FIELDS = (
    "爆款标题：", "匹配标签：", "发布建议：", "版权提醒：", "封面正标题", "封面副标题",
)
EMOJI_RE = re.compile("[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF]")
HASHTAG_RE = re.compile(r"#[^\s#]+")


def validate_publication_text(text: str) -> dict:
    issues: list[str] = []
    physical = text.splitlines()
    if len(physical) != 2:
        issues.append(f"physical line count is {len(physical)}, expected 2")
    if any(not line.strip() for line in physical):
        issues.append("blank line found")
    title = physical[0].strip() if physical else ""
    tag_line = physical[1].strip() if len(physical) > 1 else ""
    if not title:
        issues.append("title missing")
    if len(title) > 25:
        issues.append(f"title length is {len(title)}, expected at most 25")
    if re.match(r"^(?:爆款)?标题[:：]", title):
        issues.append("title contains a field label")
    if EMOJI_RE.search(text):
        issues.append("emoji found")
    tokens = tag_line.split()
    tags = [token for token in tokens if HASHTAG_RE.fullmatch(token)]
    if len(tokens) != 5 or len(tags) != 5:
        issues.append(f"valid hashtag count is {len(tags)}, expected exactly 5")
    if len(set(tags)) != len(tags):
        issues.append("hashtags must be unique")
    legacy = [field for field in LEGACY_FIELDS if field in text]
    if legacy:
        issues.append("legacy fields: " + ", ".join(legacy))
    return {
        "valid": not issues,
        "title": title,
        "title_length": len(title),
        "hashtags": tags,
        "hashtag_count": len(tags),
        "issues": issues,
    }


def validate_publication_file(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size <= 0:
        return {"valid": False, "issues": ["publication file missing or empty"]}
    return validate_publication_text(path.read_text(encoding="utf-8-sig", errors="replace"))
