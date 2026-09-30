from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


@dataclass(slots=True)
class CheckResult:
    ok: bool
    code: str = "ok"
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def exit_code(*, usage_error: bool = False, failures: int = 0) -> int:
    if usage_error:
        return EXIT_USAGE
    return EXIT_FAILED if failures else EXIT_OK
