from __future__ import annotations

import json
import struct
from pathlib import Path

from autoyy.attestation import ATTESTATION_ENV, attest_quality_record
from autoyy.deliverables import validate_root
from autoyy.voiceover import FINAL_NAME, create_quality_template, quality_path, sha256_file


def make_png(path: Path, width: int, height: int) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", width, height) + b"\x00" * 8)


def make_complete_topic(root: Path) -> Path:
    topic = root / "01-complete"
    topic.mkdir()
    (topic / "高清源视频.mp4").write_bytes(b"video-bytes")
    quotes = [f"真实引语第{i}条内容" for i in range(1, 6)]
    blocks = []
    for i, quote in enumerate(quotes, 1):
        start = (i - 1) * 2
        blocks.append(f"{i}\n00:00:{start:02d},000 --> 00:00:{start + 1:02d},900\n人物说：{quote}")
    (topic / "字幕.srt").write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    chunks = [f"“{quote}”" for quote in quotes]
    fill_total = 4500 - sum(map(len, chunks))
    gap, rem = divmod(fill_total, 6)
    parts = []
    for index in range(6):
        parts.append("甲" * (gap + (1 if index < rem else 0)))
        if index < len(chunks):
            parts.append(chunks[index])
    final = topic / FINAL_NAME
    final.write_text("".join(parts), encoding="utf-8")
    create_quality_template(topic, final)
    record = json.loads(quality_path(topic).read_text(encoding="utf-8"))
    source_hash = sha256_file(topic / "字幕.srt")
    script_hash = sha256_file(final)
    record["srt_full_read"] = True
    record["humanizer"] = {"pass": True, "mode": "embedded", "script_sha256": script_hash}
    record["fact_check"] = {
        "pass": True, "source_sha256": source_hash, "script_sha256": script_hash,
        "unsupported_claims": [], "checked_claims": 5,
    }
    record["reviewer"] = {
        "pass": True, "independent": True, "run_id": "package-reviewer", "source_sha256": source_hash,
        "script_sha256": script_hash, "reason_codes": [], "checked_evidence": 5,
    }
    record["evidence"] = [
        {"kind": "quote", "source_kind": "srt", "source_text": f"人物说：{quote}", "script_excerpt": quote, "value": quote}
        for quote in quotes
    ]
    attest_quality_record(record, key="package-test-key", issuer="pytest-verifier", run_id="package-verifier")
    quality_path(topic).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    (topic / "发布信息.txt").write_text("完整测试标题\n#纪录片 #测试 #事实 #字幕 #质量", encoding="utf-8")
    make_png(topic / "封面-3比4.png", 1200, 1600)
    make_png(topic / "封面-4比3.png", 1600, 1200)
    return topic


def test_full_package_gate_passes_complete_topic(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENV, "package-test-key")
    make_complete_topic(tmp_path)
    monkeypatch.setattr("autoyy.deliverables.probe_media", lambda *_a, **_k: {"duration": 10.0})
    result = validate_root(tmp_path, ffprobe="fake", workers=2)
    assert result["valid"]
    assert result["complete_count"] == 1


def test_full_package_gate_rejects_bad_subtitle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ATTESTATION_ENV, "package-test-key")
    topic = make_complete_topic(tmp_path)
    subtitle = topic / "字幕.srt"
    subtitle.write_text(subtitle.read_text(encoding="utf-8").replace("\n2\n", "\n3\n", 1), encoding="utf-8")
    monkeypatch.setattr("autoyy.deliverables.probe_media", lambda *_a, **_k: {"duration": 10.0})
    result = validate_root(tmp_path, ffprobe="fake")
    assert not result["valid"]
    assert any("index" in issue or "stale" in issue for issue in result["results"][0]["issues"])
