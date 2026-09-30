from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoyy.voiceover import (
    CANDIDATE_NAME,
    FINAL_NAME,
    create_quality_template,
    promote_candidate,
    quality_path,
    sha256_file,
    validate_batch_copy,
    validate_voiceover,
    validate_voiceover_batch,
)


def make_topic(root: Path, name: str, target_chars: int = 4500, fill: str = "甲", extra: str = "") -> Path:
    topic = root / name
    topic.mkdir(parents=True)
    quotes = [f"{fill}真实引语内容第{i}条" for i in range(1, 6)]
    cue_texts = [f"人物说：“{quote}”。这是对应的字幕事实。" for quote in quotes]
    blocks = [
        f"{i}\n00:00:{(i-1)*2:02d},000 --> 00:00:{(i-1)*2+1:02d},500\n{text}"
        for i, text in enumerate(cue_texts, 1)
    ]
    (topic / "字幕.srt").write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    quote_chunks = [f"“{quote}”" for quote in quotes]
    reserved = sum(len(chunk) for chunk in quote_chunks) + len(extra)
    assert reserved <= target_chars
    fill_total = target_chars - reserved
    base_gap, remainder = divmod(fill_total, len(quote_chunks) + 1)
    gap_sizes = [base_gap + (1 if i < remainder else 0) for i in range(len(quote_chunks) + 1)]
    pieces = [extra + fill * gap_sizes[0]]
    for index, chunk in enumerate(quote_chunks):
        pieces.append(chunk)
        pieces.append(fill * gap_sizes[index + 1])
    script = "".join(pieces)
    assert len(script) == target_chars
    final = topic / FINAL_NAME
    final.write_text(script, encoding="utf-8")
    create_quality_template(topic, final)
    record = json.loads(quality_path(topic).read_text(encoding="utf-8"))
    source_hash = sha256_file(topic / "字幕.srt")
    script_hash = sha256_file(final)
    record["srt_full_read"] = True
    record["humanizer"] = {"pass": True, "mode": "embedded", "script_sha256": script_hash}
    record["fact_check"] = {"pass": True, "source_sha256": source_hash, "script_sha256": script_hash, "unsupported_claims": [], "checked_claims": 5}
    record["reviewer"] = {"pass": True, "independent": True, "source_sha256": source_hash, "script_sha256": script_hash, "reason_codes": [], "checked_evidence": 5}
    record["evidence"] = [
        {"kind": "quote", "source_kind": "srt", "source_text": text, "script_excerpt": quote, "value": quote}
        for text, quote in zip(cue_texts, quotes, strict=True)
    ]
    quality_path(topic).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return topic


@pytest.mark.parametrize(("length", "valid"), [(4499, False), (4500, True), (5500, True), (5501, False)])
def test_voiceover_hard_char_boundaries(tmp_path: Path, length: int, valid: bool) -> None:
    topic = make_topic(tmp_path, "01-test", length, "甲")
    assert validate_voiceover(topic)["valid"] is valid


def test_ai_shell_and_unsupported_number_fail(tmp_path: Path) -> None:
    ai_topic = make_topic(tmp_path, "01-ai", 4500, "乙", extra="总的来说")
    assert not validate_voiceover(ai_topic)["valid"]
    number_topic = make_topic(tmp_path, "02-num", 4500, "丙", extra="这里出现9999这个数字")
    result = validate_voiceover(number_topic)
    assert not result["valid"]
    assert any("numbers absent from SRT" in issue for issue in result["issues"])


def test_stale_humanizer_hash_fails(tmp_path: Path) -> None:
    topic = make_topic(tmp_path, "01-test", 4500, "丁")
    record = json.loads(quality_path(topic).read_text(encoding="utf-8"))
    record["humanizer"]["script_sha256"] = "stale"
    quality_path(topic).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    result = validate_voiceover(topic)
    assert not result["valid"]
    assert any("humanizer script hash stale" in issue for issue in result["issues"])


def test_cross_copy_block_and_ratio_fail() -> None:
    common = "共同复制的正文内容" * 12
    scripts = {"01-a": "甲" * 500 + common + "甲" * 500, "02-b": "乙" * 500 + common + "乙" * 500}
    result = validate_batch_copy(scripts)
    assert not result["01-a"]["valid"]
    assert not result["02-b"]["valid"]


def test_three_topic_batch_one_failure_is_incomplete(tmp_path: Path) -> None:
    make_topic(tmp_path, "01-pass", 4500, "甲")
    make_topic(tmp_path, "02-pass", 4500, "乙")
    make_topic(tmp_path, "03-fail", 4499, "丙")
    result = validate_voiceover_batch(tmp_path)
    assert not result["valid"]
    assert result["status"] == "INCOMPLETE"
    assert result["failed_count"] >= 1
    assert any(row["topic"] == "03-fail" and row["status"] == "failed" for row in result["results"])


def test_reviewer_failure_blocks_promotion(tmp_path: Path) -> None:
    topic = make_topic(tmp_path, "01-test", 4500, "戊")
    final = topic / FINAL_NAME
    candidate = topic / CANDIDATE_NAME
    final.replace(candidate)
    record = json.loads(quality_path(topic).read_text(encoding="utf-8"))
    record["reviewer"]["pass"] = False
    quality_path(topic).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    result = promote_candidate(topic)
    assert not result["promoted"]
    assert not final.exists()


def test_all_quality_gates_allow_atomic_promotion(tmp_path: Path) -> None:
    topic = make_topic(tmp_path, "01-test", 4500, "己")
    final = topic / FINAL_NAME
    candidate = topic / CANDIDATE_NAME
    final.replace(candidate)
    result = promote_candidate(topic)
    assert result["promoted"]
    assert final.is_file()
    assert sha256_file(final) == sha256_file(candidate)


def test_evidence_anchors_must_cover_script(tmp_path: Path) -> None:
    topic = make_topic(tmp_path, "01-test", 4500, "庚")
    record = json.loads(quality_path(topic).read_text(encoding="utf-8"))
    first = record["evidence"][0]
    record["evidence"] = [first] * 5
    quality_path(topic).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    result = validate_voiceover(topic)
    assert not result["valid"]
    assert any("evidence anchor max gap" in issue for issue in result["issues"])


def test_quality_metadata_requires_embedded_humanizer_and_independent_reviewer(tmp_path: Path) -> None:
    topic = make_topic(tmp_path, "01-meta", 4500, "辛")
    record = json.loads(quality_path(topic).read_text(encoding="utf-8"))
    record["humanizer"]["mode"] = "other"
    record["reviewer"]["independent"] = False
    quality_path(topic).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    result = validate_voiceover(topic)
    assert not result["valid"]
    assert any("humanizer mode" in issue for issue in result["issues"])
    assert any("reviewer must be independent" in issue for issue in result["issues"])


def test_verified_external_evidence_can_support_numeric_claim(tmp_path: Path) -> None:
    phrase = "官方核验来源记录9999个样本"
    topic = make_topic(tmp_path, "01-source", 4500, "壬", extra=phrase)
    record = json.loads(quality_path(topic).read_text(encoding="utf-8"))
    record["evidence"].append({
        "kind": "number",
        "source_kind": "verified_source",
        "source_ref": "https://example.com/report",
        "verified_at": "2026-10-01",
        "source_text": "官方报告记录9999个样本。",
        "script_excerpt": phrase,
        "value": "9999",
    })
    record["fact_check"]["checked_claims"] = len(record["evidence"])
    record["reviewer"]["checked_evidence"] = len(record["evidence"])
    quality_path(topic).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    assert validate_voiceover(topic)["valid"]


def test_attempt_above_three_is_hard_failure(tmp_path: Path) -> None:
    topic = make_topic(tmp_path, "01-attempt", 4500, "癸")
    record = json.loads(quality_path(topic).read_text(encoding="utf-8"))
    record["attempt"] = 4
    quality_path(topic).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    result = validate_voiceover(topic)
    assert not result["valid"]
    assert any("attempt must be" in issue for issue in result["issues"])


def test_laorou_profile_requires_exact_signature(tmp_path: Path) -> None:
    topic = make_topic(tmp_path, "01-laorou", 4500, "子")
    final = topic / FINAL_NAME
    text = final.read_text(encoding="utf-8")
    signature = "（我是艾伦，遛狗去了，拜了个拜）"
    final.write_text(text[:-len(signature)] + "\n" + signature, encoding="utf-8")
    record = json.loads(quality_path(topic).read_text(encoding="utf-8"))
    script_hash = sha256_file(final)
    record["script_sha256"] = script_hash
    record["voice_profile"] = "laorou"
    record["humanizer"]["script_sha256"] = script_hash
    record["fact_check"]["script_sha256"] = script_hash
    record["reviewer"]["script_sha256"] = script_hash
    quality_path(topic).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    assert validate_voiceover(topic)["valid"]
    final.write_text(final.read_text(encoding="utf-8").replace(signature, "（我是艾伦，今天先到这里）"), encoding="utf-8")
    record["script_sha256"] = sha256_file(final)
    record["humanizer"]["script_sha256"] = record["script_sha256"]
    record["fact_check"]["script_sha256"] = record["script_sha256"]
    record["reviewer"]["script_sha256"] = record["script_sha256"]
    quality_path(topic).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    assert not validate_voiceover(topic)["valid"]


def test_third_failed_attempt_marks_blocked_quality(tmp_path: Path) -> None:
    topic = make_topic(tmp_path, "01-blocked", 4499, "丑")
    record = json.loads(quality_path(topic).read_text(encoding="utf-8"))
    record["attempt"] = 3
    quality_path(topic).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    result = validate_voiceover_batch(tmp_path)
    assert not result["valid"]
    row = result["results"][0]
    assert row["status"] == "blocked_quality"
    assert row["attempt"] == 3


def test_voiceover_missing_files_and_corrupt_quality_fail(tmp_path: Path) -> None:
    topic = tmp_path / "01-missing"
    topic.mkdir()
    assert not validate_voiceover(topic)["valid"]
    (topic / FINAL_NAME).write_text("甲" * 4500, encoding="utf-8")
    assert not validate_voiceover(topic)["valid"]
    (topic / "字幕.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n甲\n", encoding="utf-8")
    quality_path(topic).parent.mkdir(parents=True, exist_ok=True)
    quality_path(topic).write_text("{broken", encoding="utf-8")
    result = validate_voiceover(topic)
    assert not result["valid"]
    assert any("quality record" in issue for issue in result["issues"])


def test_direct_quote_not_in_srt_is_hard_failure(tmp_path: Path) -> None:
    topic = make_topic(tmp_path, "01-quote", 4500, "寅")
    final = topic / FINAL_NAME
    text = final.read_text(encoding="utf-8")
    final.write_text(text.replace("真实引语内容第1条", "完全不存在的引语内容", 1), encoding="utf-8")
    record = json.loads(quality_path(topic).read_text(encoding="utf-8"))
    new_hash = sha256_file(final)
    record["script_sha256"] = new_hash
    record["humanizer"]["script_sha256"] = new_hash
    record["fact_check"]["script_sha256"] = new_hash
    record["reviewer"]["script_sha256"] = new_hash
    quality_path(topic).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    result = validate_voiceover(topic)
    assert not result["valid"]
    assert any("direct quotes not found" in issue for issue in result["issues"])


def test_default_profile_rejects_first_person_narrator(tmp_path: Path) -> None:
    topic = make_topic(tmp_path, "01-first", 4500, "卯", extra="。我们要继续看看后面发生了什么。")
    result = validate_voiceover(topic)
    assert not result["valid"]
    assert any("first-person narrator" in issue for issue in result["issues"])
