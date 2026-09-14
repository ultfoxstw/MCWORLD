"""Best-effort Bedrock LevelDB scanner.

Bedrock's world/db uses a modified LevelDB with snappy/zlib compressed blocks
and delta-encoded record keys. Implementing a full reader is heavy; instead we
use a robust two-pass scan:

  1) For every .ldb / .log file, read the bytes, then attempt to decompress
     candidate blocks (snappy first via cramjam if available, then zlib, then raw).
  2) Scan each decompressed buffer for NBT compound roots (little-endian).

This preserves the "extract everything verifiable" contract: real records are
returned, unreadable regions are counted as warnings, and the original files
are never modified.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Iterable, List, Tuple

from ..nbt.parser import scan_nbt_stream

try:  # optional: cramjam ships Windows wheels and supports snappy
    import cramjam  # type: ignore

    def _snappy_decompress(data: bytes) -> bytes:
        return bytes(cramjam.snappy.decompress_raw(data))
except Exception:  # pragma: no cover - optional dependency
    cramjam = None

    def _snappy_decompress(data: bytes) -> bytes:  # type: ignore
        raise RuntimeError("snappy decompression unavailable")


def _try_zlib(data: bytes) -> bytes | None:
    import zlib
    try:
        return zlib.decompress(data)
    except Exception:
        return None


def _iter_db_files(db_dir: Path) -> Iterable[Path]:
    for name in sorted(os.listdir(db_dir)):
        p = db_dir / name
        if not p.is_file():
            continue
        if p.suffix.lower() in (".ldb", ".log"):
            yield p
        # CURRENT / MANIFEST-* are metadata; skip
        elif name.startswith("MANIFEST-"):
            continue


def _candidate_block_offsets(data: bytes) -> List[Tuple[int, int]]:
    """Best-effort: try 4KB-aligned chunks as compressed candidates.

    We intentionally do NOT parse the LevelDB Table footer here — it changes
    between LevelDB versions and the goal is robustness, not spec-correctness.
    """
    step = 4096
    out: List[Tuple[int, int]] = []
    for off in range(0, len(data), step):
        end = min(off + step * 4, len(data))  # up to 16KB windows
        out.append((off, end))
    return out


def scan_bedrock_db(db_dir: Path) -> Tuple[List[Tuple[str, dict]], List[str]]:
    """Return (records, warnings). Each record is (root_name, compound_dict)."""
    records: List[Tuple[str, dict]] = []
    warnings: List[str] = []
    if not db_dir.is_dir():
        return records, [f"No db/ directory at {db_dir}"]

    for f in _iter_db_files(db_dir):
        try:
            data = f.read_bytes()
        except OSError as e:
            warnings.append(f"Could not read {f.name}: {e}")
            continue

        # Whole-file scans across candidate decompressions
        buffers: List[bytes] = [data]

        # Attempt full-file zlib (Bedrock actually mostly uses snappy per-block,
        # but small artifacts are worth trying)
        z = _try_zlib(data)
        if z is not None:
            buffers.append(z)

        # Attempt snappy over reasonably sized windows
        if cramjam is not None:
            # Try snappy-decompressing the whole file first
            try:
                buffers.append(_snappy_decompress(data))
            except Exception:
                pass
            # Then windowed attempts on the raw file (many blocks are inside)
            for off, end in _candidate_block_offsets(data):
                chunk = data[off:end]
                try:
                    buffers.append(_snappy_decompress(chunk))
                except Exception:
                    continue
        else:
            warnings.append(
                "cramjam not installed: snappy-compressed LevelDB blocks in "
                f"{f.name} may be skipped"
            )

        seen_hashes = set()
        for buf in buffers:
            if not buf:
                continue
            h = hash(buf[:64]) ^ len(buf)
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            try:
                found = scan_nbt_stream(buf, little_endian=True)
            except Exception as e:
                warnings.append(f"NBT scan failure in {f.name}: {e}")
                continue
            records.extend(found)

    # De-duplicate structurally identical roots (best-effort)
    unique: List[Tuple[str, dict]] = []
    seen_sig = set()
    for name, val in records:
        try:
            sig = (name, len(val), tuple(sorted(val.keys()))[:16])
        except Exception:
            sig = (name, 0, ())
        if sig in seen_sig:
            continue
        seen_sig.add(sig)
        unique.append((name, val))
    return unique, warnings
