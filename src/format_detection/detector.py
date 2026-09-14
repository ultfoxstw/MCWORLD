"""File signature based format detection.

Determines actual container format independent of extension.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

FormatKind = Literal["zip", "mcworld", "epk", "directory", "unknown"]


def _read_head(path: str, n: int = 32) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _is_zip_signature(head: bytes) -> bool:
    # PK\x03\x04 (local file header), PK\x05\x06 (empty), PK\x07\x08 (spanned)
    return head[:2] == b"PK" and (
        head[2:4] == b"\x03\x04"
        or head[2:4] == b"\x05\x06"
        or head[2:4] == b"\x07\x08"
    )


def _looks_like_world_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    # Java hints
    if (path / "level.dat").exists():
        return True
    # Bedrock hints
    if (path / "db").is_dir() and (path / "level.dat").exists():
        return True
    # Some Bedrock exports have levelname.txt
    if (path / "levelname.txt").exists():
        return True
    # Nested one level (some ZIPs extract with parent folder)
    for child in path.iterdir():
        if child.is_dir():
            if (child / "level.dat").exists() or (child / "db").is_dir():
                return True
    return False


def detect_format(target: str) -> FormatKind:
    p = Path(target)
    if not p.exists():
        return "unknown"
    if p.is_dir():
        return "directory" if _looks_like_world_dir(p) else "unknown"

    head = _read_head(str(p), 64)
    ext = p.suffix.lower()

    # EPK: extension is the strongest signal since EPK has no universal magic.
    # We still inspect bytes to route correctly if the EPK internally wraps a ZIP.
    if ext == ".epk":
        return "epk"

    if _is_zip_signature(head):
        # .mcworld files are ZIPs with Bedrock world contents; extension tells us.
        if ext == ".mcworld":
            return "mcworld"
        return "zip"

    # Extension mismatch but not a ZIP: unknown
    if ext in (".zip", ".mcworld"):
        return "unknown"

    return "unknown"


def describe_head(target: str) -> str:
    """Return a hex preview of the first bytes for diagnostics/logs."""
    head = _read_head(target, 16)
    return " ".join(f"{b:02x}" for b in head)
