"""High-level pipeline that stitches format detection -> container -> extractor.

Usage:
    from src.pipeline import open_world, extract_all, save_report
"""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path
from typing import Callable, Optional

from .containers.reader import (
    ContainerError,
    WorldSource,
    open_directory,
    open_epk,
    open_zip_like,
)
from .exporters.output import (
    render_books,
    render_mobs,
    render_players,
    write_output,
)
from .extractors.all import (
    ExtractionResult,
    extract_all,
    extract_books,
    extract_named_mobs,
    extract_players,
)
from .format_detection.detector import detect_format


ProgressFn = Callable[[str], None]


def _noop(msg: str) -> None:
    pass


def open_world(target: str, progress: ProgressFn = _noop) -> WorldSource:
    progress("Detecting format...")
    kind = detect_format(target)
    progress(f"Detected: {kind}")
    if kind == "unknown":
        raise ContainerError(
            "Could not identify this file as a supported Minecraft world "
            "(.epk, .zip, .mcworld, or extracted world folder). Refusing to guess."
        )
    if kind == "directory":
        return open_directory(target)
    if kind == "zip":
        return open_zip_like(target)
    if kind == "mcworld":
        return open_zip_like(target, prefer_kind="bedrock")
    if kind == "epk":
        return open_epk(target)
    raise ContainerError(f"Unhandled format: {kind}")


def downloads_dir() -> Path:
    # Windows: %USERPROFILE%\Downloads. Linux/Mac fallback: ~/Downloads.
    if os.name == "nt":
        base = os.environ.get("USERPROFILE") or str(Path.home())
    else:
        base = str(Path.home())
    d = Path(base) / "Downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _timestamped(name: str) -> str:
    stem, ext = os.path.splitext(name)
    ts = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"{stem}_{ts}{ext}"


def save_report(text: str, filename: str, overwrite: bool = False) -> tuple[Path, list[str]]:
    target = downloads_dir() / filename
    if target.exists() and not overwrite:
        target = downloads_dir() / _timestamped(filename)
    issues = write_output(str(target), text)
    return target, issues


__all__ = [
    "open_world",
    "extract_all",
    "extract_players",
    "extract_books",
    "extract_named_mobs",
    "render_players",
    "render_books",
    "render_mobs",
    "save_report",
    "downloads_dir",
    "ExtractionResult",
    "ContainerError",
]
