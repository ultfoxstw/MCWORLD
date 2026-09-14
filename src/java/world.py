"""Java Edition world reader.

Handles level.dat (gzip NBT), player .dat files (playerdata/), and region
files (.mca) at a best-effort level: chunk NBT is parsed and returned. We do
not attempt to decode block palettes — this app targets player/book/mob data.
"""
from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path
from typing import Iterable, List, Tuple

from ..nbt.parser import NBTError, parse_nbt, parse_nbt_auto


def parse_level_dat(root: Path) -> Tuple[dict | None, List[str]]:
    warnings: List[str] = []
    p = root / "level.dat"
    if not p.exists():
        return None, ["level.dat missing"]
    try:
        data = p.read_bytes()
        name, val, meta, _ = parse_nbt_auto(data)
        return {"__root__": name, "__meta__": meta, **(val if isinstance(val, dict) else {})}, warnings
    except Exception as e:
        warnings.append(f"level.dat unreadable: {e}")
        return None, warnings


def iter_playerdata(root: Path) -> Iterable[Tuple[str, dict, List[str]]]:
    """Yield (uuid_stem, compound, warnings) for every playerdata/*.dat."""
    d = root / "playerdata"
    if not d.is_dir():
        return
    for f in sorted(d.iterdir()):
        if f.suffix.lower() != ".dat":
            continue
        try:
            data = f.read_bytes()
            _, val, _, _ = parse_nbt_auto(data)
            if isinstance(val, dict):
                yield (f.stem, val, [])
            else:
                yield (f.stem, {}, [f"{f.name}: root is not compound"])
        except Exception as e:
            yield (f.stem, {}, [f"{f.name}: {e}"])


def iter_region_chunks(region_dir: Path) -> Iterable[Tuple[str, dict, List[str]]]:
    """Iterate chunk NBTs from .mca region files.

    Yields (source_label, chunk_compound, warnings).
    """
    if not region_dir.is_dir():
        return
    for f in sorted(region_dir.iterdir()):
        if f.suffix.lower() != ".mca":
            continue
        try:
            with open(f, "rb") as fh:
                header = fh.read(4096)
                if len(header) < 4096:
                    yield (f.name, {}, [f"{f.name}: truncated header"])
                    continue
                for i in range(1024):
                    entry = header[i * 4:i * 4 + 4]
                    offset = int.from_bytes(entry[:3], "big")
                    sectors = entry[3]
                    if offset == 0 and sectors == 0:
                        continue
                    fh.seek(offset * 4096)
                    length_bytes = fh.read(4)
                    if len(length_bytes) < 4:
                        continue
                    length = int.from_bytes(length_bytes, "big")
                    ctype_byte = fh.read(1)
                    if not ctype_byte:
                        continue
                    ctype = ctype_byte[0]
                    payload = fh.read(length - 1)
                    try:
                        if ctype == 1:  # gzip
                            import gzip
                            nbt_bytes = gzip.decompress(payload)
                        elif ctype == 2:  # zlib
                            nbt_bytes = zlib.decompress(payload)
                        elif ctype == 3:  # uncompressed
                            nbt_bytes = payload
                        else:
                            yield (f.name, {}, [f"{f.name}: unknown chunk compression {ctype}"])
                            continue
                        _, val, _ = parse_nbt(nbt_bytes, little_endian=False)
                        if isinstance(val, dict):
                            yield (f.name, val, [])
                    except Exception as e:
                        yield (f.name, {}, [f"{f.name} chunk {i}: {e}"])
        except OSError as e:
            yield (f.name, {}, [f"{f.name}: {e}"])


def iter_all_dimensions(root: Path) -> Iterable[Tuple[str, Path]]:
    """Yield (dimension_label, region_dir) for overworld / nether / end / custom dims."""
    if (root / "region").is_dir():
        yield ("Overworld", root / "region")
    if (root / "DIM-1" / "region").is_dir():
        yield ("Nether", root / "DIM-1" / "region")
    if (root / "DIM1" / "region").is_dir():
        yield ("End", root / "DIM1" / "region")
    # entities/ and poi/ live parallel to region/ in modern Java
    for entities_dir in (root / "entities",):
        if entities_dir.is_dir():
            yield ("Overworld/entities", entities_dir)
    for d in (root / "DIM-1" / "entities",):
        if d.is_dir():
            yield ("Nether/entities", d)
    for d in (root / "DIM1" / "entities",):
        if d.is_dir():
            yield ("End/entities", d)
    # custom dimensions (data pack)
    dims = root / "dimensions"
    if dims.is_dir():
        for ns in dims.iterdir():
            if not ns.is_dir():
                continue
            for dim in ns.iterdir():
                if (dim / "region").is_dir():
                    yield (f"{ns.name}:{dim.name}", dim / "region")
                if (dim / "entities").is_dir():
                    yield (f"{ns.name}:{dim.name}/entities", dim / "entities")
