"""Format detection tests using synthetic files (no external fixtures)."""
from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

import pytest

from src.format_detection.detector import detect_format


def _write(p: Path, data: bytes) -> None:
    p.write_bytes(data)


def test_zip_signature(tmp_path):
    p = tmp_path / "world.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("level.dat", b"\x1f\x8b\x08\x00")
    assert detect_format(str(p)) == "zip"


def test_mcworld_extension_with_zip_bytes(tmp_path):
    p = tmp_path / "world.mcworld"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("level.dat", b"stuff")
    assert detect_format(str(p)) == "mcworld"


def test_epk_extension(tmp_path):
    p = tmp_path / "world.epk"
    _write(p, b"EPK1" + b"\x00" * 100)
    assert detect_format(str(p)) == "epk"


def test_directory_java(tmp_path):
    (tmp_path / "level.dat").write_bytes(b"\x1f\x8b")
    assert detect_format(str(tmp_path)) == "directory"


def test_directory_bedrock(tmp_path):
    (tmp_path / "level.dat").write_bytes(b"\x08\x00\x00\x00")
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "000001.ldb").write_bytes(b"")
    assert detect_format(str(tmp_path)) == "directory"


def test_unknown_file(tmp_path):
    p = tmp_path / "garbage.bin"
    _write(p, b"random bytes")
    assert detect_format(str(p)) == "unknown"


def test_zip_extension_but_not_zip(tmp_path):
    """Extension lies -> we refuse to accept it as ZIP."""
    p = tmp_path / "fake.zip"
    _write(p, b"not-a-zip")
    assert detect_format(str(p)) == "unknown"


def test_nonexistent(tmp_path):
    assert detect_format(str(tmp_path / "nope")) == "unknown"
