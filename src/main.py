"""Entry point for both the GUI and the CLI.

CLI usage:
    python -m src.main <path-to-file-or-folder> [players|books|mobs|all] [--raw]

GUI usage:
    python -m src.main
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .containers.reader import ContainerError
from .pipeline import (
    extract_all,
    open_world,
    render_books,
    render_mobs,
    render_players,
    save_report,
)


def _cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Minecraft World Data Extractor CLI")
    p.add_argument("input", help="Path to .epk / .zip / .mcworld / world folder")
    p.add_argument("mode", nargs="?", default="all",
                   choices=["players", "books", "mobs", "all"])
    p.add_argument("--raw", action="store_true", help="Raw parsed NBT export")
    args = p.parse_args(argv)

    def prog(msg: str):
        print(msg)

    try:
        src = open_world(args.input, progress=prog)
    except ContainerError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    result = extract_all(src)
    print(f"Counters: {result.counters}")
    if result.warnings:
        print(f"Warnings: {len(result.warnings)} (first 10 shown)")
        for w in result.warnings[:10]:
            print(f"  - {w}")

    if args.mode in ("players", "all"):
        out, issues = save_report(render_players(result, args.raw), "player_data.txt")
        print(f"Saved: {out}")
        for i in issues:
            print(f"  validation: {i}")
    if args.mode in ("books", "all"):
        out, issues = save_report(render_books(result, args.raw), "book_data.txt")
        print(f"Saved: {out}")
        for i in issues:
            print(f"  validation: {i}")
    if args.mode in ("mobs", "all"):
        out, issues = save_report(render_mobs(result, args.raw), "named_mobs_data.txt")
        print(f"Saved: {out}")
        for i in issues:
            print(f"  validation: {i}")

    src.cleanup()
    return 0


def main() -> int:
    if len(sys.argv) <= 1:
        # GUI
        from .gui.app import run
        run()
        return 0
    return _cli(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
