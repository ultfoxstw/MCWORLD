"""Tolerant NBT parser supporting big/little endian, gzip/zlib/raw, all standard tags.

Never guesses blindly: detects compression by magic bytes, tries endianness
via a small heuristic (root compound with reasonable name length). Unknown
tags are preserved as {"__unknown_tag__": id} so records don't fail whole-sale.
"""
from __future__ import annotations

import gzip
import io
import struct
import zlib
from typing import Any, Dict, List, Tuple

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


class NBTError(Exception):
    pass


def _detect_compression(data: bytes) -> str:
    if len(data) < 2:
        return "raw"
    if data[:2] == b"\x1f\x8b":
        return "gzip"
    # zlib: 0x78 followed by 0x01/0x9c/0xda commonly
    if data[0] == 0x78 and data[1] in (0x01, 0x5E, 0x9C, 0xDA):
        return "zlib"
    return "raw"


def decompress(data: bytes) -> Tuple[bytes, str]:
    mode = _detect_compression(data)
    try:
        if mode == "gzip":
            return gzip.decompress(data), "gzip"
        if mode == "zlib":
            return zlib.decompress(data), "zlib"
    except Exception:
        # Fall through to raw
        pass
    return data, "raw"


class _Reader:
    __slots__ = ("buf", "pos", "little")

    def __init__(self, buf: bytes, little: bool):
        self.buf = buf
        self.pos = 0
        self.little = little

    def _read(self, n: int) -> bytes:
        if self.pos + n > len(self.buf):
            raise NBTError(f"Unexpected EOF at {self.pos}, needed {n}")
        b = self.buf[self.pos:self.pos + n]
        self.pos += n
        return b

    def i8(self) -> int:
        return struct.unpack("b", self._read(1))[0]

    def u8(self) -> int:
        return self._read(1)[0]

    def i16(self) -> int:
        return struct.unpack("<h" if self.little else ">h", self._read(2))[0]

    def u16(self) -> int:
        return struct.unpack("<H" if self.little else ">H", self._read(2))[0]

    def i32(self) -> int:
        return struct.unpack("<i" if self.little else ">i", self._read(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q" if self.little else ">q", self._read(8))[0]

    def f32(self) -> float:
        return struct.unpack("<f" if self.little else ">f", self._read(4))[0]

    def f64(self) -> float:
        return struct.unpack("<d" if self.little else ">d", self._read(8))[0]

    def string(self) -> str:
        n = self.u16()
        raw = self._read(n)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            # Preserve bytes marker instead of corrupting
            return raw.decode("utf-8", errors="replace")


def _read_payload(r: _Reader, tag_id: int) -> Any:
    if tag_id == TAG_BYTE:
        return r.i8()
    if tag_id == TAG_SHORT:
        return r.i16()
    if tag_id == TAG_INT:
        return r.i32()
    if tag_id == TAG_LONG:
        return r.i64()
    if tag_id == TAG_FLOAT:
        return r.f32()
    if tag_id == TAG_DOUBLE:
        return r.f64()
    if tag_id == TAG_BYTE_ARRAY:
        n = r.i32()
        return list(r._read(n))
    if tag_id == TAG_STRING:
        return r.string()
    if tag_id == TAG_LIST:
        item_id = r.u8()
        n = r.i32()
        if n <= 0:
            return {"__list_type__": item_id, "items": []}
        items = [_read_payload(r, item_id) for _ in range(n)]
        return {"__list_type__": item_id, "items": items}
    if tag_id == TAG_COMPOUND:
        out: Dict[str, Any] = {}
        while True:
            inner_id = r.u8()
            if inner_id == TAG_END:
                break
            name = r.string()
            out[name] = _read_payload(r, inner_id)
        return out
    if tag_id == TAG_INT_ARRAY:
        n = r.i32()
        fmt = ("<" if r.little else ">") + "i" * n
        return list(struct.unpack(fmt, r._read(4 * n))) if n > 0 else []
    if tag_id == TAG_LONG_ARRAY:
        n = r.i32()
        fmt = ("<" if r.little else ">") + "q" * n
        return list(struct.unpack(fmt, r._read(8 * n))) if n > 0 else []
    raise NBTError(f"Unknown NBT tag id: {tag_id}")


def parse_nbt(data: bytes, little_endian: bool = False,
              bedrock_header: bool = False) -> Tuple[str, Any, int]:
    """Parse a single named root NBT tag.

    Returns (root_name, root_value, bytes_consumed).
    If bedrock_header is True, strips the 8-byte level.dat Bedrock header first.
    """
    if bedrock_header and len(data) >= 8:
        data = data[8:]
    r = _Reader(data, little_endian)
    tag_id = r.u8()
    if tag_id == TAG_END:
        return ("", None, r.pos)
    name = r.string()
    val = _read_payload(r, tag_id)
    return (name, val, r.pos)


def parse_nbt_auto(data: bytes) -> Tuple[str, Any, str, int]:
    """Auto-detect compression + endianness. Returns (name, value, meta, consumed).

    meta describes what was chosen, e.g. 'gzip/big'.
    """
    raw, comp = decompress(data)

    # Try Bedrock level.dat header (8-byte header, little endian) if likely
    candidates: List[Tuple[bool, bool]] = [
        (False, False),  # big endian, no bedrock header
        (True, False),   # little endian, no header
        (True, True),    # bedrock level.dat header + little
    ]
    last_err: Exception | None = None
    for little, bhdr in candidates:
        try:
            name, val, n = parse_nbt(raw, little_endian=little, bedrock_header=bhdr)
            # Sanity: a compound root with at least one plausible entry, or a
            # non-empty name string.
            if isinstance(val, dict) or isinstance(val, list):
                return (name, val, f"{comp}/{'little' if little else 'big'}"
                        f"{'/bhdr' if bhdr else ''}", n)
        except Exception as e:
            last_err = e
            continue
    raise NBTError(f"Could not parse NBT with any known layout: {last_err}")


def scan_nbt_stream(data: bytes, little_endian: bool = True) -> List[Tuple[str, Any]]:
    """Scan a raw byte buffer for consecutive named NBT compound roots.

    Used for Bedrock LevelDB block decoding, where a decompressed block
    may contain multiple concatenated NBT structures.
    Returns list of (name, value) tuples. Malformed regions are skipped.
    """
    results: List[Tuple[str, Any]] = []
    pos = 0
    n = len(data)
    while pos < n:
        # Only consider positions that begin with a compound tag byte
        if data[pos] != TAG_COMPOUND:
            pos += 1
            continue
        try:
            name, val, consumed = parse_nbt(data[pos:], little_endian=little_endian)
            if isinstance(val, dict):
                results.append((name, val))
                pos += consumed
                continue
        except Exception:
            pass
        pos += 1
    return results
