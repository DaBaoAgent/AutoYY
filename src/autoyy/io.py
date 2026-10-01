from __future__ import annotations

import sys
from typing import TextIO


def _reconfigure(stream: TextIO) -> None:
    try:
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        return


def configure_utf8_stdio() -> None:
    """Prefer deterministic UTF-8 output without crashing on legacy Windows locales."""
    _reconfigure(sys.stdout)
    _reconfigure(sys.stderr)
