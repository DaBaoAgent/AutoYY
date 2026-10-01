from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from difflib import SequenceMatcher
from pathlib import Path

from .attestation import attest_quality_record, verify_quality_attestation
from .subtitles import srt_to_text

QUALITY_RELATIVE = Path(".autoyy") / "voiceover-quality.json"
CANDIDATE_NAME = "爆款口播稿.candidate.txt"
FINAL_NAME = "爆款口播稿.txt"
LAOROU_SIGNATURE = "（我是艾伦，遛狗去了，拜了个拜）"
BANNED_PATTERNS = [
    r"【开场钩子】", r"【核心悬念】", r"【推进", r"【结尾升华】", r"【补充叙事】",
    r"镜头切到", r"画面来到", r"回看整条因果链", r"前面三条线索共同指向",
    r"值得注意的是", r"不可否认的是", r"总的来说", r"综上所述", r"本质上",
    r"真正重要的是", r"核心在于", r"底层逻辑", r"你觉得呢[？?]", r"是不是很震撼",
]
BANNED_PATTERNS += [r"\u6211\u4eec\u53ef\u4ee5\u770b\u5230", r"\u5212\u91cd\u70b9", r"\u63a5\u4e0b\u6765(?:\u6211\u4eec|\u518d\u6765|\u6765\u770b)", r"\u4e0d\u662f.{1,24}\u800c\u662f.{1,24}", r"\u4e0d\u53ea\u662f.{1,24}(?:\u66f4\u662f|\u8fd8\u662f).{1,24}"]
DIRECT_QUOTE_RE = re.compile(r"[“「\"]([^”」\"]{4,120})[”」\"]")
NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?%?")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_text(text: str) -> str:
    text = text.replace(LAOROU_SIGNATURE, "")
    return re.sub(r"\s+", "", text)


def duplicate_paragraphs(text: str) -> list[str]:
    paragraphs = [re.sub(r"\s+", " ", p.strip()) for p in re.split(r"\r?\n\s*\r?\n", text) if p.strip()]
    seen: set[str] = set()
    duplicates: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) >= 50 and paragraph in seen:
            duplicates.append(paragraph[:100])
        seen.add(paragraph)
    return duplicates


def quality_path(topic: Path) -> Path:
    return topic / QUALITY_RELATIVE


def load_quality(topic: Path) -> dict:
    path = quality_path(topic)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def create_quality_template(topic: Path, script_path: Path | None = None, *, voice_profile: str = "default") -> Path:
    if voice_profile not in {"default", "laorou"}:
        raise ValueError(f"unsupported voice_profile: {voice_profile}")
    subtitle = topic / "字幕.srt"
    script = script_path or topic / CANDIDATE_NAME
    if not subtitle.is_file() or not script.is_file():
        raise FileNotFoundError("字幕.srt and candidate script are required")
    source_hash = sha256_file(subtitle)
    script_hash = sha256_file(script)
    payload = {
        "schema_version": 1,
        "source_sha256": source_hash,
        "script_sha256": script_hash,
        "srt_full_read": False,
        "voice_profile": voice_profile,
        "attempt": 1,
        "writer": {"run_id": str(uuid.uuid4())},
        "humanizer": {"pass": False, "mode": "embedded", "script_sha256": script_hash},
        "fact_check": {
            "pass": False,
            "source_sha256": source_hash,
            "script_sha256": script_hash,
            "unsupported_claims": [],
            "checked_claims": 0,
        },
        "reviewer": {
            "pass": False,
            "independent": False,
            "run_id": "",
            "source_sha256": source_hash,
            "script_sha256": script_hash,
            "reason_codes": [],
            "checked_evidence": 0,
        },
        "evidence": [],
    }
    path = quality_path(topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def attest_quality_file(
    topic: Path,
    *,
    issuer: str = "autoyy-independent-verifier",
    run_id: str | None = None,
) -> Path:
    path = quality_path(topic)
    record = load_quality(topic)
    if not record:
        raise FileNotFoundError("structured quality record missing or invalid")
    attest_quality_record(record, issuer=issuer, run_id=run_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _hash_gate(record: dict, name: str, source_hash: str, script_hash: str) -> list[str]:
    gate = record.get(name) or {}
    issues: list[str] = []
    if gate.get("pass") is not True:
        issues.append(f"{name} not passed")
    if gate.get("script_sha256") != script_hash:
        issues.append(f"{name} script hash stale")
    if name in {"fact_check", "reviewer"} and gate.get("source_sha256") != source_hash:
        issues.append(f"{name} source hash stale")
    if name == "humanizer" and gate.get("mode") != "embedded":
        issues.append("humanizer mode must be embedded")
    if name == "fact_check" and gate.get("unsupported_claims"):
        issues.append("fact_check contains unsupported claims")
    if name == "reviewer" and gate.get("independent") is not True:
        issues.append("reviewer must be independent")
    return issues


def validate_voiceover(
    topic: Path,
    *,
    script_path: Path | None = None,
    min_chars: int = 4500,
    max_chars: int = 5500,
    min_quotes: int = 5,
    require_quality: bool = True,
) -> dict:
    script = script_path or topic / FINAL_NAME
    subtitle = topic / "字幕.srt"
    issues: list[str] = []
    if not script.is_file() or script.stat().st_size <= 0:
        return {"valid": False, "issues": ["script missing or empty"], "chars": 0}
    if not subtitle.is_file() or subtitle.stat().st_size <= 0:
        return {"valid": False, "issues": ["字幕.srt missing or empty"], "chars": 0}
    text = script.read_text(encoding="utf-8-sig", errors="replace")
    compact = normalized_text(text)
    chars = len(re.sub(r"\s+", "", text))
    if not min_chars <= chars <= max_chars:
        issues.append(f"script chars {chars}, expected {min_chars}-{max_chars}")
    banned = [pattern for pattern in BANNED_PATTERNS if re.search(pattern, text)]
    if banned:
        issues.append("banned AI/template patterns: " + ", ".join(banned))
    duplicates = duplicate_paragraphs(text)
    if duplicates:
        issues.append(f"duplicate long paragraphs: {len(duplicates)}")

    transcript = srt_to_text(subtitle)
    transcript_compact = normalized_text(transcript)
    quotes = DIRECT_QUOTE_RE.findall(text)
    missing_quotes = [quote for quote in quotes if normalized_text(quote) not in transcript_compact]
    if missing_quotes:
        issues.append(f"direct quotes not found in source: {len(missing_quotes)}")
    if len(quotes) < min_quotes:
        issues.append(f"direct quote count {len(quotes)}, expected at least {min_quotes}")
    record = load_quality(topic)
    source_numbers = set(NUMBER_RE.findall(transcript_compact))
    verified_external_numbers: set[str] = set()
    for item in (record.get("evidence") or []) if record else []:
        if item.get("source_kind") == "verified_source":
            source_ref = str(item.get("source_ref") or "").strip()
            source_text = str(item.get("source_text") or "").strip()
            verified_at = str(item.get("verified_at") or "").strip()
            if source_ref and source_text and verified_at:
                verified_external_numbers.update(NUMBER_RE.findall(normalized_text(source_text)))
    script_numbers = set(NUMBER_RE.findall(compact))
    unsupported_numbers = sorted(script_numbers - source_numbers - verified_external_numbers)
    if unsupported_numbers:
        issues.append("numbers absent from SRT/verified evidence: " + ", ".join(unsupported_numbers[:20]))

    source_hash = sha256_file(subtitle)
    script_hash = sha256_file(script)
    evidence_max_gap: int | None = None
    if require_quality:
        if not record:
            issues.append("structured quality record missing")
        else:
            if record.get("schema_version") != 1:
                issues.append("unsupported quality schema")
            if record.get("source_sha256") != source_hash:
                issues.append("quality source hash stale")
            if record.get("script_sha256") != script_hash:
                issues.append("quality script hash stale")
            issues.extend(verify_quality_attestation(record))
            if record.get("srt_full_read") is not True:
                issues.append("srt_full_read is not true")
            attempt = record.get("attempt")
            if not isinstance(attempt, int) or not 1 <= attempt <= 3:
                issues.append("attempt must be an integer from 1 to 3")
            profile = str(record.get("voice_profile") or "default")
            if profile not in {"default", "laorou"}:
                issues.append(f"unsupported voice_profile: {profile}")
            for gate in ("humanizer", "fact_check", "reviewer"):
                issues.extend(_hash_gate(record, gate, source_hash, script_hash))
            evidence = record.get("evidence") or []
            if len(evidence) < min_quotes:
                issues.append(f"evidence items {len(evidence)}, expected at least {min_quotes}")
            fact_checked = (record.get("fact_check") or {}).get("checked_claims")
            reviewer_checked = (record.get("reviewer") or {}).get("checked_evidence")
            if not isinstance(fact_checked, int) or fact_checked < len(evidence):
                issues.append("fact_check checked_claims is below evidence count")
            if not isinstance(reviewer_checked, int) or reviewer_checked < len(evidence):
                issues.append("reviewer checked_evidence is below evidence count")
            anchor_positions: list[int] = []
            for item in evidence:
                source_kind = str(item.get("source_kind") or "srt")
                source_text = normalized_text(str(item.get("source_text") or ""))
                script_excerpt = normalized_text(str(item.get("script_excerpt") or ""))
                if source_kind == "srt":
                    if not source_text or source_text not in transcript_compact:
                        issues.append("evidence source_text not found in SRT")
                        continue
                elif source_kind == "verified_source":
                    if not source_text or not str(item.get("source_ref") or "").strip() or not str(item.get("verified_at") or "").strip():
                        issues.append("verified_source evidence requires source_text/source_ref/verified_at")
                        continue
                else:
                    issues.append(f"unknown evidence source_kind: {source_kind}")
                    continue
                if len(script_excerpt) < 4 or script_excerpt not in compact:
                    issues.append("evidence script_excerpt not found in script")
                    continue
                anchor_positions.append(compact.find(script_excerpt))
            if anchor_positions:
                points = [0, *sorted(set(anchor_positions)), len(compact)]
                evidence_max_gap = max(b - a for a, b in zip(points, points[1:], strict=False))
                if evidence_max_gap > 1300:
                    issues.append(f"evidence anchor max gap {evidence_max_gap} > 1300 chars")
            elif record:
                issues.append("no valid evidence anchors")
            if profile == "laorou":
                if text.rstrip().splitlines()[-1].strip() != LAOROU_SIGNATURE:
                    issues.append("laorou profile requires exact final signature")
                body_without_quotes = DIRECT_QUOTE_RE.sub("", text.replace(LAOROU_SIGNATURE, ""))
                if re.search(r"[A-Za-z]", body_without_quotes):
                    issues.append("laorou profile contains English letters outside direct quotes")
                for token in ("这会儿", "那会儿", "待会儿", "事儿", "活儿", "玩意儿", "大伙儿", "哥们儿", "爷们儿"):
                    if token in body_without_quotes:
                        issues.append(f"laorou forbidden erhua: {token}")
            elif profile == "default":
                body_without_quotes = DIRECT_QUOTE_RE.sub("", text)
                if re.search(r"(?:^|[\u3002\uff01\uff1f\n])\s*(?:\u6211|\u6211\u4eec|\u54b1|\u54b1\u4eec)(?:\u8981|\u6765|\u80fd|\u4f1a|\u53ef\u4ee5|\u5148|\u518d|\u770b|\u77e5\u9053|\u8bf4)", body_without_quotes):
                    issues.append("default profile contains first-person narrator voice")
    return {
        "valid": not issues,
        "issues": issues,
        "chars": chars,
        "quote_count": len(quotes),
        "evidence_items": len(record.get("evidence") or []) if record else 0,
        "evidence_max_gap": evidence_max_gap,
        "source_sha256": source_hash,
        "script_sha256": script_hash,
        "attempt": record.get("attempt") if record else None,
    }


def _pair_copy_issues(a: str, b: str) -> tuple[list[str], float]:
    na, nb = normalized_text(a), normalized_text(b)
    if not na or not nb:
        return [], 0.0
    matcher = SequenceMatcher(None, na, nb, autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size >= 30]
    longest = max((block.size for block in blocks), default=0)
    shared = sum(block.size for block in blocks)
    ratio = shared / min(len(na), len(nb))
    issues: list[str] = []
    if longest >= 80:
        issues.append(f"shared contiguous block {longest} chars >= 80")
    if ratio > 0.08:
        issues.append(f"shared matching-block ratio {ratio:.3f} > 0.08")
    head_a, head_b = na[:200], nb[:200]
    tail_a, tail_b = na[-200:], nb[-200:]
    if min(len(head_a), len(head_b)) >= 100 and SequenceMatcher(None, head_a, head_b, autojunk=False).ratio() > 0.72:
        issues.append("opening similarity > 0.72")
    if min(len(tail_a), len(tail_b)) >= 100 and SequenceMatcher(None, tail_a, tail_b, autojunk=False).ratio() > 0.72:
        issues.append("ending similarity > 0.72")
    paras_a = [normalized_text(p) for p in re.split(r"\r?\n\s*\r?\n", a) if len(normalized_text(p)) >= 80]
    paras_b = [normalized_text(p) for p in re.split(r"\r?\n\s*\r?\n", b) if len(normalized_text(p)) >= 80]
    for pa in paras_a:
        if any(SequenceMatcher(None, pa, pb, autojunk=False).ratio() >= 0.88 for pb in paras_b):
            issues.append("paragraph similarity >= 0.88")
            break
    return issues, ratio


def validate_batch_copy(scripts: dict[str, str]) -> dict[str, dict]:
    result = {name: {"valid": True, "issues": [], "max_cross_copy_pct": 0.0} for name in scripts}
    names = sorted(scripts)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            issues, ratio = _pair_copy_issues(scripts[left], scripts[right])
            pct = round(ratio * 100, 2)
            result[left]["max_cross_copy_pct"] = max(result[left]["max_cross_copy_pct"], pct)
            result[right]["max_cross_copy_pct"] = max(result[right]["max_cross_copy_pct"], pct)
            if issues:
                message = f"cross-copy with {right if left != right else left}: " + "; ".join(issues)
                result[left]["issues"].append(message)
                result[right]["issues"].append(f"cross-copy with {left}: " + "; ".join(issues))
    for item in result.values():
        item["valid"] = not item["issues"]
    return result


def promote_candidate(topic: Path, *, force: bool = False) -> dict:
    candidate = topic / CANDIDATE_NAME
    final = topic / FINAL_NAME
    if final.exists() and not force:
        return {"promoted": False, "issues": ["final script already exists; use --force"]}
    result = validate_voiceover(topic, script_path=candidate)
    if not result["valid"]:
        return {"promoted": False, "issues": result["issues"], "validation": result}
    scripts = {topic.name: candidate.read_text(encoding="utf-8-sig", errors="replace")}
    for sibling in topic.parent.iterdir():
        if sibling == topic or not sibling.is_dir() or not re.match(r"^\d{2}-", sibling.name):
            continue
        sibling_final = sibling / FINAL_NAME
        if sibling_final.is_file() and sibling_final.stat().st_size > 0:
            scripts[sibling.name] = sibling_final.read_text(encoding="utf-8-sig", errors="replace")
    copy_result = validate_batch_copy(scripts).get(topic.name, {"valid": True, "issues": []})
    if not copy_result["valid"]:
        return {"promoted": False, "issues": copy_result["issues"], "validation": result}
    tmp = final.with_suffix(".tmp")
    shutil.copyfile(candidate, tmp)
    tmp.replace(final)
    return {"promoted": True, "issues": [], "validation": result}


def validate_voiceover_batch(root: Path, *, require_quality: bool = True) -> dict:
    topics = sorted(p for p in root.iterdir() if p.is_dir() and re.match(r"^\d{2}-", p.name))
    if not topics:
        return {"valid": False, "topic_count": 0, "issues": ["no topic directories"], "results": []}
    rows: list[dict] = []
    scripts: dict[str, str] = {}
    for topic in topics:
        final = topic / FINAL_NAME
        result = validate_voiceover(topic, require_quality=require_quality)
        row = {"topic": topic.name, **result}
        rows.append(row)
        if final.is_file() and final.stat().st_size > 0:
            scripts[topic.name] = final.read_text(encoding="utf-8-sig", errors="replace")
    copy_results = validate_batch_copy(scripts)
    for row in rows:
        copy_result = copy_results.get(row["topic"], {"valid": True, "issues": [], "max_cross_copy_pct": 0.0})
        row["cross_copy_pct"] = copy_result["max_cross_copy_pct"]
        row["issues"].extend(copy_result["issues"])
        row["valid"] = not row["issues"]
        record = load_quality(root / row["topic"])
        row["srt_full_read"] = record.get("srt_full_read") is True
        row["humanizer_pass"] = (record.get("humanizer") or {}).get("pass") is True
        row["fact_check"] = "pass" if (record.get("fact_check") or {}).get("pass") is True else "fail"
        row["reviewer"] = "pass" if (record.get("reviewer") or {}).get("pass") is True else "fail"
        attempt = record.get("attempt") if record else None
        row["attempt"] = attempt
        row["status"] = "complete" if row["valid"] else ("blocked_quality" if attempt == 3 else "failed")
    failed = sum(not row["valid"] for row in rows)
    return {
        "valid": failed == 0,
        "topic_count": len(rows),
        "complete_count": len(rows) - failed,
        "failed_count": failed,
        "status": "COMPLETE" if failed == 0 else "INCOMPLETE",
        "results": rows,
    }
