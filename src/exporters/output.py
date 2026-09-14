"""Clean & raw exporters and UTF-8 output validation."""
from __future__ import annotations

import json
from typing import Any, Iterable, List

from ..extractors.all import BookRecord, ExtractionResult, NamedMobRecord, PlayerRecord

# ---------- validation ----------


def validate_utf8_text(text: str) -> List[str]:
    """Return a list of issues found. Empty list == OK."""
    issues: List[str] = []
    try:
        text.encode("utf-8").decode("utf-8")
    except Exception as e:
        issues.append(f"Not valid UTF-8: {e}")

    if "\uFFFD" in text:
        issues.append("Contains Unicode replacement characters (\uFFFD)")
    # NULL bytes
    if "\x00" in text:
        issues.append("Contains NULL bytes")
    # ZIP local header signature accidentally embedded
    if "PK\x03\x04" in text or "PK\x05\x06" in text:
        issues.append("Contains ZIP archive signature - binary spill")
    # Gzip magic
    if "\x1f\x8b" in text:
        issues.append("Contains gzip magic bytes - binary spill")
    return issues


def _write_validated(path: str, text: str) -> List[str]:
    issues = validate_utf8_text(text)
    if issues:
        # Do not write corrupted output silently — write with a warning header.
        text = "[OUTPUT VALIDATION FAILED - see warnings]\n\n" + text
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return issues


# ---------- helpers ----------


def _fmt_playtime(ticks: int | None) -> str:
    if ticks is None:
        return "Unknown / Not stored"
    seconds = ticks / 20.0  # Minecraft tick = 1/20s
    if seconds < 60:
        return f"{seconds:.0f} seconds"
    minutes = int(seconds // 60)
    rem_seconds = int(seconds - minutes * 60)
    if minutes < 60:
        return f"{minutes} minutes"
    hours = minutes // 60
    rem_minutes = minutes % 60
    if hours < 24:
        return f"{hours} hours {rem_minutes} minutes"
    days = hours // 24
    rem_hours = hours % 24
    return f"{days} days {rem_hours} hours {rem_minutes} minutes"


def _fmt_pos(pos) -> str:
    if not pos:
        return "Unknown / Not stored"
    x, y, z = pos
    return f"X: {x:.2f}\nY: {y:.2f}\nZ: {z:.2f}"


# ---------- CLEAN EXPORTERS ----------


def clean_players(players: List[PlayerRecord], counters: dict, warnings: List[str]) -> str:
    lines: List[str] = []
    lines.append("MINECRAFT PLAYER DATA")
    lines.append("=" * 50)
    lines.append(f"Players discovered: {counters.get('players_discovered', len(players))}")
    lines.append(f"Players successfully parsed: {counters.get('players_parsed', len(players))}")
    if warnings:
        lines.append(f"Warnings: {len(warnings)}")
    lines.append("")

    for p in players:
        lines.append("-" * 50)
        lines.append(f"## PLAYER: {p.name or p.identifier}")
        lines.append("")
        lines.append("Playtime:")
        lines.append(_fmt_playtime(p.playtime_ticks))
        lines.append("")
        if p.position:
            lines.append("Position:")
            lines.append(_fmt_pos(p.position))
            lines.append("")
        if p.dimension:
            lines.append(f"Dimension: {p.dimension}")
            lines.append("")
        lines.append("Inventory:")
        if not p.inventory:
            lines.append("(empty / not stored)")
        for it in p.inventory:
            slot = it.get("slot")
            slot_label = f"Slot {slot}: " if slot is not None else "Slot ?: "
            item_id = str(it.get("id", "unknown")).split(":")[-1].replace("_", " ").title()
            count = it.get("count", 1)
            line = f"{slot_label}{item_id} x{count}"
            if it.get("custom_name"):
                line += f'  (Named: "{it["custom_name"]}")'
            if it.get("enchantments"):
                line += "  [Enchanted]"
            lines.append(line)
        lines.append("")
    return "\n".join(lines) + "\n"


def clean_books(books: List[BookRecord], counters: dict, warnings: List[str]) -> str:
    lines: List[str] = []
    lines.append("MINECRAFT BOOK DATA")
    lines.append("=" * 50)
    lines.append(f"Books discovered: {counters.get('books_discovered', len(books))}")
    lines.append(f"Books successfully parsed: {counters.get('books_parsed', len(books))}")
    lines.append("")

    first = True
    for b in books:
        if not first:
            lines.extend([""] * 5)  # FIVE blank lines between books
        first = False
        lines.append("=" * 50)
        lines.append("BOOK")
        lines.append("====")
        lines.append("")
        lines.append("Book name:")
        lines.append(b.title if b.title else "Untitled")
        lines.append("")
        lines.append("Author:")
        lines.append(b.author if b.author else "Unsigned / No author stored")
        lines.append("")
        lines.append("Source:")
        lines.append(b.source or "Unknown")
        lines.append("")
        if b.location:
            lines.append("Location:")
            lines.append(_fmt_pos(b.location))
            lines.append("")
        if b.dimension:
            lines.append(f"Dimension: {b.dimension}")
            lines.append("")
        for i, page in enumerate(b.pages, 1):
            if i > 1:
                lines.append("")  # ONE blank line between pages
            lines.append(f"PAGE {i}")
            lines.append("")
            lines.append(page if page else "")
    return "\n".join(lines) + "\n"


def clean_mobs(mobs: List[NamedMobRecord], counters: dict, warnings: List[str]) -> str:
    lines: List[str] = []
    lines.append("MINECRAFT NAMED MOB DATA")
    lines.append("=" * 50)
    lines.append(f"Named mobs discovered: {counters.get('mobs_discovered', len(mobs))}")
    lines.append(f"Named mobs successfully parsed: {counters.get('mobs_parsed', len(mobs))}")
    lines.append("")

    for m in mobs:
        lines.append("=" * 50)
        lines.append("NAMED MOB")
        lines.append("=========")
        lines.append("")
        lines.append(f"Name:\n{m.name}\n")
        lines.append(f"Mob type:\n{m.mob_type or 'Unknown / Not stored'}\n")
        lines.append(f"Health:\n{m.health if m.health is not None else 'Unknown / Not stored'}\n")
        lines.append(f"Maximum health:\n{m.max_health if m.max_health is not None else 'Unknown / Not stored'}\n")
        lines.append("Position:")
        lines.append(_fmt_pos(m.position))
        lines.append("")
        lines.append(f"Dimension:\n{m.dimension or 'Unknown / Not stored'}\n")
        lines.append("Created:\nUnknown / Not stored\n")
        lines.append(f"Age:\n{m.age if m.age is not None else 'Unknown / Not stored'}\n")
    return "\n".join(lines) + "\n"


# ---------- RAW EXPORTERS ----------


def _raw_nbt_pretty(value: Any, indent: int = 0) -> str:
    """Preserve NBT hierarchy as indented Compound { Tag { Value } } style."""
    pad = "  " * indent
    if isinstance(value, dict):
        # NBT list wrapper
        if "__list_type__" in value:
            item_id = value.get("__list_type__")
            items = value.get("items", [])
            lines = [f"{pad}List<{item_id}> ["]
            for it in items:
                lines.append(_raw_nbt_pretty(it, indent + 1))
            lines.append(f"{pad}]")
            return "\n".join(lines)
        # Compound
        lines = [f"{pad}Compound {{"]
        for k, v in value.items():
            lines.append(f"{pad}  {k}:")
            lines.append(_raw_nbt_pretty(v, indent + 2))
        lines.append(f"{pad}}}")
        return "\n".join(lines)
    if isinstance(value, list):
        # Byte arrays / int arrays / long arrays land here
        preview = value if len(value) <= 32 else value[:32] + ["... (truncated)"]
        return f"{pad}Array({len(value)}): {preview}"
    if isinstance(value, str):
        # Sanitize: if the string has NULL bytes it isn't real text
        if "\x00" in value:
            return f'{pad}String[binary; length={len(value)}]: "[BINARY DATA - NOT TEXT EXPORTED]"'
        return f'{pad}String: {json.dumps(value, ensure_ascii=False)}'
    if isinstance(value, bool):
        return f"{pad}Byte: {int(value)}"
    if isinstance(value, int):
        return f"{pad}Int/Long: {value}"
    if isinstance(value, float):
        return f"{pad}Float/Double: {value}"
    if value is None:
        return f"{pad}End"
    return f"{pad}{type(value).__name__}: {value!r}"


def raw_players(players: List[PlayerRecord], counters: dict, warnings: List[str]) -> str:
    parts = ["MINECRAFT PLAYER DATA (RAW PARSED NBT)",
             "=" * 50,
             f"Players discovered: {counters.get('players_discovered', len(players))}",
             f"Players successfully parsed: {counters.get('players_parsed', len(players))}",
             ""]
    for p in players:
        parts.append("-" * 50)
        parts.append(f"## PLAYER: {p.name or p.identifier}")
        parts.append(_raw_nbt_pretty(p.raw))
        parts.append("")
    return "\n".join(parts) + "\n"


def raw_books(books: List[BookRecord], counters: dict, warnings: List[str]) -> str:
    parts = ["MINECRAFT BOOK DATA (RAW PARSED NBT)",
             "=" * 50,
             f"Books discovered: {counters.get('books_discovered', len(books))}",
             f"Books successfully parsed: {counters.get('books_parsed', len(books))}",
             ""]
    for b in books:
        parts.append("=" * 50)
        parts.append(f"BOOK source={b.source} location={b.location} dim={b.dimension}")
        parts.append(_raw_nbt_pretty(b.raw))
        parts.append("")
    return "\n".join(parts) + "\n"


def raw_mobs(mobs: List[NamedMobRecord], counters: dict, warnings: List[str]) -> str:
    parts = ["MINECRAFT NAMED MOB DATA (RAW PARSED NBT)",
             "=" * 50,
             f"Mobs discovered: {counters.get('mobs_discovered', len(mobs))}",
             f"Mobs successfully parsed: {counters.get('mobs_parsed', len(mobs))}",
             ""]
    for m in mobs:
        parts.append("=" * 50)
        parts.append(f"NAMED MOB name={m.name} type={m.mob_type}")
        parts.append(_raw_nbt_pretty(m.raw))
        parts.append("")
    return "\n".join(parts) + "\n"


# ---------- top-level ----------


def render_players(result: ExtractionResult, raw: bool) -> str:
    fn = raw_players if raw else clean_players
    return fn(result.players, result.counters, result.warnings)


def render_books(result: ExtractionResult, raw: bool) -> str:
    fn = raw_books if raw else clean_books
    return fn(result.books, result.counters, result.warnings)


def render_mobs(result: ExtractionResult, raw: bool) -> str:
    fn = raw_mobs if raw else clean_mobs
    return fn(result.mobs, result.counters, result.warnings)


def write_output(path: str, text: str) -> List[str]:
    return _write_validated(path, text)
