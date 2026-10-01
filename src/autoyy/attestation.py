from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

ATTESTATION_ENV = "AUTOYY_QUALITY_ATTESTATION_KEY"
ATTESTATION_SCHEMA = 1


def _key(value: str | None = None) -> bytes:
    raw = value if value is not None else os.environ.get(ATTESTATION_ENV, "")
    if not raw:
        raise ValueError(f"{ATTESTATION_ENV} is required to attest/verify quality records")
    return raw.encode("utf-8")


def _canonical_record(record: dict[str, Any]) -> bytes:
    payload = {key: value for key, value in record.items() if key != "attestation"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _message(record: dict[str, Any], meta: dict[str, Any]) -> bytes:
    stable_meta = {
        "schema_version": meta["schema_version"],
        "algorithm": meta["algorithm"],
        "issuer": meta["issuer"],
        "run_id": meta["run_id"],
        "created_at": meta["created_at"],
    }
    return _canonical_record(record) + b"\n" + json.dumps(
        stable_meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def attest_quality_record(
    record: dict[str, Any],
    *,
    key: str | None = None,
    issuer: str = "autoyy-independent-verifier",
    run_id: str | None = None,
) -> dict[str, Any]:
    if not issuer.strip():
        raise ValueError("attestation issuer is required")
    meta: dict[str, Any] = {
        "schema_version": ATTESTATION_SCHEMA,
        "algorithm": "hmac-sha256",
        "issuer": issuer.strip(),
        "run_id": run_id or str(uuid.uuid4()),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    meta["signature"] = hmac.new(_key(key), _message(record, meta), hashlib.sha256).hexdigest()
    record["attestation"] = meta
    return record


def verify_quality_attestation(record: dict[str, Any], *, key: str | None = None) -> list[str]:
    meta = record.get("attestation")
    if not isinstance(meta, dict):
        return ["quality attestation missing"]
    required = ("schema_version", "algorithm", "issuer", "run_id", "created_at", "signature")
    missing = [name for name in required if not str(meta.get(name, "")).strip()]
    if missing:
        return ["quality attestation missing fields: " + ", ".join(missing)]
    if meta.get("schema_version") != ATTESTATION_SCHEMA or meta.get("algorithm") != "hmac-sha256":
        return ["unsupported quality attestation schema/algorithm"]
    try:
        expected = hmac.new(_key(key), _message(record, meta), hashlib.sha256).hexdigest()
    except ValueError as exc:
        return [str(exc)]
    if not hmac.compare_digest(expected, str(meta.get("signature"))):
        return ["quality attestation signature invalid"]
    writer_run = str((record.get("writer") or {}).get("run_id") or "")
    reviewer_run = str((record.get("reviewer") or {}).get("run_id") or "")
    verifier_run = str(meta.get("run_id") or "")
    if not writer_run or not reviewer_run:
        return ["writer/reviewer run_id required"]
    if writer_run == reviewer_run:
        return ["reviewer run_id must differ from writer run_id"]
    if verifier_run in {writer_run, reviewer_run}:
        return ["verifier run_id must differ from writer/reviewer run_id"]
    return []
