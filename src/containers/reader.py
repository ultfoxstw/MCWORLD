"""Safe container readers.

Provides a unified WorldSource with:
  - .root: absolute path to the detected world root
  - .kind:  'java' | 'bedrock'
  - .cleanup(): remove temp extraction

All operations are read-only against the user's original file.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


class ContainerError(Exception):
    pass


@dataclass
class WorldSource:
    root: Path
    kind: str  # 'java' | 'bedrock' | 'unknown'
    _tempdir: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def cleanup(self) -> None:
        if self._tempdir and os.path.isdir(self._tempdir):
            shutil.rmtree(self._tempdir, ignore_errors=True)
            self._tempdir = None


def _safe_extract_zip(zf: zipfile.ZipFile, dest: str, max_bytes: int = 5 * 1024**3) -> List[str]:
    """Extract with path traversal protection and size limit."""
    written: List[str] = []
    total = 0
    dest_abs = os.path.abspath(dest)
    for member in zf.infolist():
        # sanitize name
        name = member.filename.replace("\\", "/")
        if name.endswith("/"):
            continue  # skip explicit dirs
        # strip leading slashes / drive letters
        while name.startswith(("/", "../")):
            name = name.lstrip("/")
            if name.startswith("../"):
                name = name[3:]
        parts = [p for p in name.split("/") if p not in ("", ".", "..")]
        if not parts:
            continue
        out_path = os.path.abspath(os.path.join(dest_abs, *parts))
        if not out_path.startswith(dest_abs + os.sep) and out_path != dest_abs:
            # path traversal attempt
            continue
        total += member.file_size
        if total > max_bytes:
            raise ContainerError(f"Extraction exceeded limit ({max_bytes} bytes)")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with zf.open(member, "r") as src, open(out_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        written.append(out_path)
    return written


def _find_world_root(base: Path) -> Optional[Path]:
    """Locate a plausible Minecraft world root inside an extracted tree."""
    # Direct hit
    if (base / "level.dat").exists():
        return base
    # BFS a few levels
    candidates: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        if "level.dat" in filenames:
            candidates.append(Path(dirpath))
        # Bedrock structure: has db/ directory alongside level.dat usually
    if candidates:
        # Prefer shallowest
        candidates.sort(key=lambda p: len(p.parts))
        return candidates[0]
    # Fall back: db/ folder means Bedrock world even without level.dat readable
    for dirpath, dirnames, filenames in os.walk(base):
        if "db" in dirnames and any(f.endswith((".ldb", ".log")) for f in os.listdir(os.path.join(dirpath, "db"))):
            return Path(dirpath)
    return None


def _classify_world(root: Path) -> str:
    if (root / "db").is_dir():
        return "bedrock"
    if (root / "region").is_dir() or (root / "playerdata").is_dir():
        return "java"
    if (root / "level.dat").exists():
        # Bedrock level.dat has 8-byte header, Java is gzip (1F 8B)
        try:
            with open(root / "level.dat", "rb") as f:
                head = f.read(2)
            return "java" if head == b"\x1f\x8b" else "bedrock"
        except OSError:
            return "unknown"
    return "unknown"


def open_zip_like(path: str, prefer_kind: Optional[str] = None) -> WorldSource:
    """Open .zip / .mcworld by extracting to a temp dir, then locating the world root."""
    if not zipfile.is_zipfile(path):
        raise ContainerError(f"Not a valid ZIP archive: {path}")
    tmp = tempfile.mkdtemp(prefix="mcx_")
    try:
        with zipfile.ZipFile(path, "r") as zf:
            _safe_extract_zip(zf, tmp)
    except zipfile.BadZipFile as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ContainerError(f"Corrupt ZIP: {e}") from e
    root = _find_world_root(Path(tmp))
    if root is None:
        # Some MCWORLDs are flat — treat tmp as root
        root = Path(tmp)
    kind = _classify_world(root) if prefer_kind is None else prefer_kind
    return WorldSource(root=root, kind=kind, _tempdir=tmp)


def open_directory(path: str) -> WorldSource:
    p = Path(path)
    root = _find_world_root(p) or p
    return WorldSource(root=root, kind=_classify_world(root))


def open_epk(path: str) -> WorldSource:
    """EPK containers are not universal.

    Strategy: inspect bytes. If the file (or contents past a header) is a ZIP,
    treat it as a wrapped ZIP world. Otherwise raise so the caller can report
    "unsupported EPK subformat" rather than fabricating data.
    """
    with open(path, "rb") as f:
        raw = f.read()
    # Try direct ZIP
    if raw[:2] == b"PK":
        # Write to temp and open as zip
        tmp = tempfile.mkdtemp(prefix="mcx_epk_")
        wrapped = os.path.join(tmp, "wrapped.zip")
        with open(wrapped, "wb") as f:
            f.write(raw)
        try:
            src = open_zip_like(wrapped)
            src._tempdir = tmp  # own the parent
            return src
        finally:
            pass
    # Scan for embedded ZIP local header
    idx = raw.find(b"PK\x03\x04")
    if idx > 0 and idx < 4096:  # header within the first 4KB is plausible
        tmp = tempfile.mkdtemp(prefix="mcx_epk_")
        wrapped = os.path.join(tmp, "wrapped.zip")
        with open(wrapped, "wb") as f:
            f.write(raw[idx:])
        try:
            src = open_zip_like(wrapped)
            src._tempdir = tmp
            src.warnings.append(f"EPK: skipped {idx} header bytes before ZIP payload")
            return src
        except ContainerError:
            shutil.rmtree(tmp, ignore_errors=True)
    raise ContainerError(
        "Unrecognized EPK internal format. No ZIP payload detected; refusing to "
        "guess. Please report this .epk sample so support can be added."
    )
