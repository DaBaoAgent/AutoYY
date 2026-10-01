from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")

RETRYABLE_CODES = {
    "NETWORK_TIMEOUT",
    "NETWORK_TRANSIENT",
    "RATE_LIMITED",
    "REMOTE_5XX",
    "PROCESS_TIMEOUT",
    "TEMPORARY_IO",
}

NON_RETRYABLE_CODES = {
    "AUTH_REQUIRED",
    "UNSUPPORTED_URL",
    "DEPENDENCY_MISSING",
    "INVALID_INPUT",
    "QUALITY_GATE_FAILED",
}


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_seconds: float = 1.0
    max_seconds: float = 15.0
    jitter_ratio: float = 0.15

    def delay(self, attempt: int, *, seed: str = "") -> float:
        raw = min(self.max_seconds, self.base_seconds * (2 ** max(0, attempt - 1)))
        if self.jitter_ratio <= 0:
            return raw
        rng = random.Random(f"{seed}:{attempt}")
        spread = raw * self.jitter_ratio
        return max(0.0, raw + rng.uniform(-spread, spread))


def classify_failure(*, returncode: int | None = None, stderr: str = "", error: BaseException | None = None) -> str:
    text = f"{stderr} {error or ''}".lower()
    if "429" in text or "too many requests" in text or "rate limit" in text:
        return "RATE_LIMITED"
    if "timed out" in text or "timeout" in text:
        return "NETWORK_TIMEOUT" if "http" in text or "network" in text else "PROCESS_TIMEOUT"
    if any(token in text for token in ("connection reset", "connection aborted", "temporary failure", "network is unreachable")):
        return "NETWORK_TRANSIENT"
    if any(token in text for token in ("401", "403", "sign in", "cookies")):
        return "AUTH_REQUIRED"
    if any(token in text for token in ("http error 500", "http error 502", "http error 503", "http error 504", " 500 ", " 502 ", " 503 ", " 504 ")):
        return "REMOTE_5XX"
    if isinstance(error, OSError):
        return "TEMPORARY_IO"
    return "UNKNOWN_FAILURE"


def is_retryable(code: str) -> bool:
    return code in RETRYABLE_CODES


def run_with_retry(
    operation: Callable[[int], T],
    *,
    policy: RetryPolicy,
    classify: Callable[[BaseException], str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    seed: str = "",
) -> tuple[T, dict[str, Any]]:
    last_error: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            value = operation(attempt)
            return value, {"attempts": attempt, "retried": attempt > 1}
        except BaseException as exc:  # noqa: BLE001
            last_error = exc
            code = classify(exc) if classify else classify_failure(error=exc)
            if attempt >= policy.max_attempts or not is_retryable(code):
                raise
            sleep(policy.delay(attempt, seed=seed))
    raise RuntimeError("retry loop exhausted") from last_error
