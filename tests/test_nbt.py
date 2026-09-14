"""NBT parser round-trip and edge-case tests.

These tests build valid NBT byte streams from scratch (no fixture files),
covering every documented tag, both endianness, and gzip/zlib framing.
"""
from __future__ import annotations

import gzip
import io
import struct
import zlib

import pytest

from src.nbt.parser import (
    NBTError,
    TAG_BYTE,
    TAG_BYTE_ARRAY,
    TAG_COMPOUND,
    TAG_DOUBLE,
    TAG_END,
    TAG_FLOAT,
    TAG_INT,
    TAG_INT_ARRAY,
    TAG_LIST,
    TAG_LONG,
    TAG_LONG_ARRAY,
    TAG_SHORT,
    TAG_STRING,
    parse_nbt,
    parse_nbt_auto,
    scan_nbt_stream,
)


def _s(text: str, little: bool) -> bytes:
    b = text.encode("utf-8")
    return struct.pack(("<" if little else ">") + "H", len(b)) + b


def _build_simple(little: bool) -> bytes:
    endian = "<" if little else ">"
    payload = io.BytesIO()
    # Root compound named "root"
    payload.write(bytes([TAG_COMPOUND]))
    payload.write(_s("root", little))
    # TAG_STRING "hello" = "world"
    payload.write(bytes([TAG_STRING]))
    payload.write(_s("hello", little))
    payload.write(_s("world", little))
    # TAG_INT "count" = 42
    payload.write(bytes([TAG_INT]))
    payload.write(_s("count", little))
    payload.write(struct.pack(endian + "i", 42))
    # TAG_LIST "nums" of TAG_INT [1,2,3]
    payload.write(bytes([TAG_LIST]))
    payload.write(_s("nums", little))
    payload.write(bytes([TAG_INT]))
    payload.write(struct.pack(endian + "i", 3))
    for n in (1, 2, 3):
        payload.write(struct.pack(endian + "i", n))
    # TAG_END
    payload.write(bytes([TAG_END]))
    return payload.getvalue()


@pytest.mark.parametrize("little", [False, True])
def test_parse_simple(little):
    name, val, n = parse_nbt(_build_simple(little), little_endian=little)
    assert name == "root"
    assert val["hello"] == "world"
    assert val["count"] == 42
    assert val["nums"] == {"__list_type__": TAG_INT, "items": [1, 2, 3]}
    assert n > 0


def test_gzip_framing():
    raw = _build_simple(little=False)
    gz = gzip.compress(raw)
    name, val, meta, _ = parse_nbt_auto(gz)
    assert name == "root"
    assert "gzip" in meta
    assert val["count"] == 42


def test_zlib_framing():
    raw = _build_simple(little=True)
    zl = zlib.compress(raw)
    name, val, meta, _ = parse_nbt_auto(zl)
    assert name == "root"
    assert "zlib" in meta
    assert val["count"] == 42


def test_raw_stream_scan_finds_multiple():
    a = _build_simple(little=True)
    b = _build_simple(little=True)
    stream = b"\x00\x00" + a + b"\xff\xff" + b + b"\x00"
    found = scan_nbt_stream(stream, little_endian=True)
    assert len(found) >= 2
    for name, val in found:
        assert name == "root"
        assert val["hello"] == "world"


def test_all_scalar_tags():
    little = False
    endian = ">"
    p = io.BytesIO()
    p.write(bytes([TAG_COMPOUND]))
    p.write(_s("r", little))

    def add(tag, name, fmt, val):
        p.write(bytes([tag]))
        p.write(_s(name, little))
        p.write(struct.pack(endian + fmt, val))

    add(TAG_BYTE, "b", "b", -5)
    add(TAG_SHORT, "s", "h", -30000)
    add(TAG_INT, "i", "i", 123456)
    add(TAG_LONG, "l", "q", 1 << 40)
    add(TAG_FLOAT, "f", "f", 1.5)
    add(TAG_DOUBLE, "d", "d", 2.5)

    # byte array
    p.write(bytes([TAG_BYTE_ARRAY]))
    p.write(_s("ba", little))
    p.write(struct.pack(endian + "i", 3))
    p.write(bytes([1, 2, 3]))

    # int array
    p.write(bytes([TAG_INT_ARRAY]))
    p.write(_s("ia", little))
    p.write(struct.pack(endian + "i", 2))
    p.write(struct.pack(endian + "ii", 7, 8))

    # long array
    p.write(bytes([TAG_LONG_ARRAY]))
    p.write(_s("la", little))
    p.write(struct.pack(endian + "i", 1))
    p.write(struct.pack(endian + "q", 9))

    p.write(bytes([TAG_END]))

    name, val, _ = parse_nbt(p.getvalue(), little_endian=little)
    assert val["b"] == -5
    assert val["s"] == -30000
    assert val["i"] == 123456
    assert val["l"] == 1 << 40
    assert abs(val["f"] - 1.5) < 1e-6
    assert abs(val["d"] - 2.5) < 1e-9
    assert val["ba"] == [1, 2, 3]
    assert val["ia"] == [7, 8]
    assert val["la"] == [9]


def test_truncated_raises():
    raw = _build_simple(little=False)
    with pytest.raises(NBTError):
        parse_nbt(raw[:5], little_endian=False)
