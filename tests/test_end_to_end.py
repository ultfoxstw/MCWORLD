"""End-to-end tests exercising extractor + exporter + validator on a
synthetic Java-like world folder built entirely in-memory.
"""
from __future__ import annotations

import gzip
import io
import os
import struct
from pathlib import Path

import pytest

from src.exporters.output import validate_utf8_text
from src.nbt.parser import (
    TAG_BYTE,
    TAG_COMPOUND,
    TAG_END,
    TAG_INT,
    TAG_LIST,
    TAG_LONG,
    TAG_STRING,
)
from src.pipeline import (
    extract_all,
    open_world,
    render_books,
    render_mobs,
    render_players,
)


def _s(text: str) -> bytes:
    b = text.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _build_player_nbt(name: str, playtime: int, books: list[dict]) -> bytes:
    """Build a minimal Java player .dat (gzip NBT big-endian)."""
    p = io.BytesIO()
    p.write(bytes([TAG_COMPOUND]))
    p.write(_s(""))  # root name

    # Name
    p.write(bytes([TAG_STRING]))
    p.write(_s("Name"))
    p.write(_s(name))

    # PlayTime (LONG)
    p.write(bytes([TAG_LONG]))
    p.write(_s("PlayTime"))
    p.write(struct.pack(">q", playtime))

    # Inventory (LIST<Compound>) with the books
    p.write(bytes([TAG_LIST]))
    p.write(_s("Inventory"))
    p.write(bytes([TAG_COMPOUND]))
    p.write(struct.pack(">i", len(books)))
    for slot, book in enumerate(books):
        # Slot (BYTE)
        p.write(bytes([TAG_BYTE]))
        p.write(_s("Slot"))
        p.write(struct.pack(">b", slot))
        # id (STRING)
        p.write(bytes([TAG_STRING]))
        p.write(_s("id"))
        p.write(_s("minecraft:written_book"))
        # Count (BYTE)
        p.write(bytes([TAG_BYTE]))
        p.write(_s("Count"))
        p.write(struct.pack(">b", 1))
        # tag { title, author, pages [STRING] }
        p.write(bytes([TAG_COMPOUND]))
        p.write(_s("tag"))
        p.write(bytes([TAG_STRING]))
        p.write(_s("title"))
        p.write(_s(book["title"]))
        p.write(bytes([TAG_STRING]))
        p.write(_s("author"))
        p.write(_s(book["author"]))
        p.write(bytes([TAG_LIST]))
        p.write(_s("pages"))
        p.write(bytes([TAG_STRING]))
        p.write(struct.pack(">i", len(book["pages"])))
        for page in book["pages"]:
            p.write(_s(page))
        p.write(bytes([TAG_END]))
        p.write(bytes([TAG_END]))

    p.write(bytes([TAG_END]))
    return gzip.compress(p.getvalue())


def _build_level_dat() -> bytes:
    p = io.BytesIO()
    p.write(bytes([TAG_COMPOUND]))
    p.write(_s(""))
    p.write(bytes([TAG_COMPOUND]))
    p.write(_s("Data"))
    p.write(bytes([TAG_STRING]))
    p.write(_s("LevelName"))
    p.write(_s("Test World"))
    p.write(bytes([TAG_END]))
    p.write(bytes([TAG_END]))
    return gzip.compress(p.getvalue())


def test_end_to_end_java_extraction(tmp_path):
    root = tmp_path / "world"
    root.mkdir()
    (root / "level.dat").write_bytes(_build_level_dat())
    pd = root / "playerdata"
    pd.mkdir()
    pd.joinpath("00000000-0000-0000-0000-000000000001.dat").write_bytes(
        _build_player_nbt(
            "Alice",
            20 * 60 * 5,  # 5 minutes
            [
                {"title": "Diary", "author": "Alice",
                 "pages": ["Day 1", "Day 2", "Day 3"]},
            ],
        )
    )
    pd.joinpath("00000000-0000-0000-0000-000000000002.dat").write_bytes(
        _build_player_nbt("Bob", 20 * 3600 * 2, [])  # 2 hours
    )

    src = open_world(str(root))
    result = extract_all(src)
    assert result.counters["players_parsed"] >= 2
    assert result.counters["books_parsed"] >= 1

    clean_p = render_players(result, raw=False)
    raw_p = render_players(result, raw=True)
    assert "Alice" in clean_p and "Bob" in clean_p
    assert "5 minutes" in clean_p
    assert "2 hours" in clean_p
    # Raw output preserves NBT hierarchy
    assert "Compound {" in raw_p

    clean_b = render_books(result, raw=False)
    assert "Diary" in clean_b
    assert "PAGE 1" in clean_b and "PAGE 3" in clean_b
    # FIVE blank lines rule applies only between multiple books; here 1 book
    # UTF-8 validation
    for text in (clean_p, raw_p, clean_b):
        assert validate_utf8_text(text) == []

    src.cleanup()


def test_validator_flags_binary_spill():
    bad = "hello\x00world"
    issues = validate_utf8_text(bad)
    assert any("NULL" in i for i in issues)

    bad2 = "before\x1f\x8bafter"
    issues2 = validate_utf8_text(bad2)
    assert any("gzip" in i for i in issues2)

    bad3 = "PK\x03\x04rest"
    issues3 = validate_utf8_text(bad3)
    assert any("ZIP" in i for i in issues3)


def test_mode_switch_uses_same_dataset(tmp_path):
    """Toggling raw/clean must never reparse."""
    root = tmp_path / "world"
    root.mkdir()
    (root / "level.dat").write_bytes(_build_level_dat())
    pd = root / "playerdata"
    pd.mkdir()
    pd.joinpath("p.dat").write_bytes(_build_player_nbt("Alice", 20, []))
    src = open_world(str(root))
    result = extract_all(src)
    id_before = id(result.players[0].raw)
    _ = render_players(result, raw=False)
    _ = render_players(result, raw=True)
    _ = render_players(result, raw=False)
    id_after = id(result.players[0].raw)
    assert id_before == id_after  # dataset object identity preserved
    src.cleanup()
