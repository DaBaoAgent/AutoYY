from __future__ import annotations

from datetime import date

from autoyy.peer import pattern_stats, validate_row


def row(platform: str, pattern: str, views: str, category: str = "监狱", published: str = "2026-09-01") -> dict[str, str]:
    return {
        "platform": platform,
        "title": f"{pattern}-{views}",
        "hashtags": "#a #b #c #d #e",
        "views": views,
        "likes": "10",
        "comments": "2",
        "topic_category": category,
        "hook_pattern": pattern,
        "published_at": published,
        "source": "",
    }


def test_pattern_stats_filter_platform_and_report_confidence() -> None:
    rows = [row("douyin", "数字冲击", "100"), row("douyin", "数字冲击", "300"), row("bilibili", "数字冲击", "9999"), row("douyin", "反常识", "200")]
    stats = pattern_stats(rows, platform="douyin", category="监狱")
    number = next(item for item in stats if item["pattern"] == "数字冲击")
    assert number["count"] == 2
    assert number["median_views"] == 200
    assert number["confidence"] == "low"


def test_pattern_stats_since_filter() -> None:
    rows = [row("douyin", "数字冲击", "100", published="2026-08-01"), row("douyin", "数字冲击", "300", published="2026-09-20")]
    stats = pattern_stats(rows, since=date(2026, 9, 1))
    assert stats[0]["count"] == 1
    assert stats[0]["median_views"] == 300


def test_peer_row_rejects_negative_and_bad_date_but_warns_unknown_pattern() -> None:
    bad = row("douyin", "未知模式", "-1", published="not-a-date")
    issues = validate_row(bad)
    assert any("non-negative" in issue for issue in issues)
    assert any("published_at" in issue for issue in issues)
    assert any("unknown hook_pattern" in issue for issue in issues)


def test_pattern_stats_never_mix_platforms_by_default() -> None:
    rows = [
        row("douyin", "数字冲击", "100"),
        row("bilibili", "数字冲击", "900"),
    ]
    stats = pattern_stats(rows)
    assert len(stats) == 2
    assert {item["platform"] for item in stats} == {"douyin", "bilibili"}
    assert {item["median_views"] for item in stats} == {100, 900}
