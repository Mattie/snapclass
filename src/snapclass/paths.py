from __future__ import annotations

import os
from typing import Any


_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def safe_path_placeholder(name: str, value: Any) -> str:
    text = os.fspath(value) if isinstance(value, os.PathLike) else str(value)
    if not text:
        raise ValueError(f"Path placeholder '{name}' cannot be empty")
    if text in {".", ".."}:
        raise ValueError(f"Unsafe path placeholder '{name}': {text!r}")
    if "/" in text or "\\" in text:
        raise ValueError(f"Path placeholder '{name}' cannot contain path separators: {text!r}")
    if any(char in text for char in '<>:"|?*'):
        raise ValueError(f"Path placeholder '{name}' contains path-sensitive characters: {text!r}")
    if "\x00" in text:
        raise ValueError(f"Path placeholder '{name}' cannot contain NUL bytes")
    if text.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"Path placeholder '{name}' uses a reserved filename: {text!r}")
    return text
