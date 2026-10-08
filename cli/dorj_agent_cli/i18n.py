"""Small locale helper. Persian is selected only when the POSIX locale starts with ``fa``."""

from __future__ import annotations

import os


def _locale_name() -> str:
    for key in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(key, "").strip()
        if value:
            return value.lower()
    return ""


def is_fa() -> bool:
    return _locale_name().startswith("fa")


def tr(en: str, fa: str) -> str:
    """Return the English string by default, or the Persian string for fa locales."""
    return fa if is_fa() else en
