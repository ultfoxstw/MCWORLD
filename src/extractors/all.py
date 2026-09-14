"""Normalized data-model extractors.

Single extraction pipeline: produces one dataset per category from either a
Java or Bedrock WorldSource. Formatters/exporters operate on this dataset
only; there is no separate "raw parser" — raw export re-serializes the same
parsed data.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..bedrock.leveldb_scan import scan_bedrock_db
from ..containers.reader import WorldSource
from ..java.world import iter_all_dimensions, iter_playerdata, iter_region_chunks


# ---------- data model ----------

@dataclass
class PlayerRecord:
    identifier: str          # UUID (java) or client-id (bedrock) or "local"
    name: str | None
    playtime_ticks: int | None
    inventory: List[Dict[str, Any]] = field(default_factory=list)
    dimension: str | None = None
    position: Tuple[float, float, float] | None = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BookRecord:
    title: str | None
    author: str | None
    source: str            # e.g., "Item Frame", "Player Inventory", "Chest"
    location: Tuple[float, float, float] | None
    dimension: str | None
    pages: List[str] = field(default_factory=list)
    unique_key: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NamedMobRecord:
    name: str
    mob_type: str | None
    health: float | None
    max_health: float | None
    position: Tuple[float, float, float] | None
    dimension: str | None
    age: int | None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionResult:
    players: List[PlayerRecord] = field(default_factory=list)
    books: List[BookRecord] = field(default_factory=list)
    mobs: List[NamedMobRecord] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    counters: Dict[str, int] = field(default_factory=dict)


# ---------- item/text helpers ----------

def _decode_text_component(value: Any) -> str:
    """Decode a Minecraft text component (JSON or plain) into readable text."""
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        # NBT string may live under "" or similar wrapping; look for common keys
        for k in ("text",):
            if isinstance(value.get(k), str):
                return value[k]
        # concatenate 'extra'
        parts = [_decode_text_component(x) for x in value.get("extra", [])] if isinstance(value.get("extra"), list) else []
        return "".join(parts)
    if isinstance(value, list):
        return "".join(_decode_text_component(x) for x in value)
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                return _decode_text_component(json.loads(s))
            except Exception:
                return value
        if s.startswith("[") and s.endswith("]"):
            try:
                return _decode_text_component(json.loads(s))
            except Exception:
                return value
        return value
    return str(value)


def _get_ci(d: Dict[str, Any], *names: str) -> Any:
    """Case-insensitive get across variant tag names (Java/Bedrock differ)."""
    if not isinstance(d, dict):
        return None
    lower_map = {k.lower(): k for k in d.keys()}
    for n in names:
        if n in d:
            return d[n]
        real = lower_map.get(n.lower())
        if real is not None:
            return d[real]
    return None


def _iter_items_in(compound: Any) -> List[Dict[str, Any]]:
    """Yield item-like compounds within a nested NBT structure."""
    out: List[Dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            # An item compound has id + Count (Java) or Name + Count (Bedrock)
            has_id = ("id" in node or "Name" in node)
            has_count = ("Count" in node or "count" in node)
            if has_id and has_count:
                out.append(node)
            for k, v in node.items():
                if isinstance(v, dict):
                    visit(v)
                elif isinstance(v, list):
                    visit(v)
                elif isinstance(v, dict) and v.get("__list_type__") is not None:
                    for it in v.get("items", []):
                        visit(it)
            # Handle explicit NBT list wrappers
            if "__list_type__" in node:
                for it in node.get("items", []):
                    visit(it)
        elif isinstance(node, list):
            for v in node:
                visit(v)

    visit(compound)
    return out


def _item_readable(item: Dict[str, Any]) -> Dict[str, Any]:
    name = _get_ci(item, "id", "Name") or "unknown"
    count = _get_ci(item, "Count", "count") or 1
    slot = _get_ci(item, "Slot")
    tag = _get_ci(item, "tag", "components") or {}
    custom_name = None
    if isinstance(tag, dict):
        display = _get_ci(tag, "display")
        if isinstance(display, dict):
            custom_name = _decode_text_component(_get_ci(display, "Name"))
        # 1.20.5+ components
        if not custom_name:
            comp_name = _get_ci(tag, "minecraft:custom_name", "custom_name")
            if comp_name is not None:
                custom_name = _decode_text_component(comp_name)
    enchantments = None
    if isinstance(tag, dict):
        ench = _get_ci(tag, "Enchantments", "ench", "minecraft:enchantments")
        if ench:
            enchantments = ench
    return {
        "slot": slot,
        "id": name,
        "count": count,
        "custom_name": custom_name,
        "enchantments": enchantments,
        "raw": item,
    }


# ---------- player extraction ----------

def _extract_player_from_compound(pid: str, comp: Dict[str, Any]) -> PlayerRecord:
    inv = _get_ci(comp, "Inventory") or []
    if isinstance(inv, dict) and "items" in inv:
        inv = inv["items"]
    items: List[Dict[str, Any]] = []
    if isinstance(inv, list):
        for it in inv:
            if isinstance(it, dict):
                items.append(_item_readable(it))
    pos = _get_ci(comp, "Pos")
    position = None
    if isinstance(pos, dict) and pos.get("items"):
        vals = pos["items"]
        if len(vals) >= 3:
            position = (float(vals[0]), float(vals[1]), float(vals[2]))
    dim = _get_ci(comp, "Dimension")
    playtime = _get_ci(comp, "PlayTime", "playedTime", "TimePlayed")
    return PlayerRecord(
        identifier=pid,
        name=_get_ci(comp, "Name", "username") or None,
        playtime_ticks=int(playtime) if isinstance(playtime, (int, float)) else None,
        inventory=items,
        dimension=str(dim) if dim is not None else None,
        position=position,
        raw=comp,
    )


def extract_players(src: WorldSource) -> Tuple[List[PlayerRecord], List[str], Dict[str, int]]:
    warnings: List[str] = []
    players: List[PlayerRecord] = []
    discovered = 0

    if src.kind == "java":
        for pid, comp, w in iter_playerdata(src.root):
            discovered += 1
            warnings.extend(w)
            try:
                players.append(_extract_player_from_compound(pid, comp))
            except Exception as e:
                warnings.append(f"player {pid} parse failed: {e}")
        # Also inspect level.dat "Player" sub-compound (singleplayer)
        from ..java.world import parse_level_dat
        lvl, lw = parse_level_dat(src.root)
        warnings.extend(lw)
        if lvl:
            local = _get_ci(lvl, "Player") or _get_ci(_get_ci(lvl, "Data") or {}, "Player")
            if isinstance(local, dict):
                discovered += 1
                try:
                    players.append(_extract_player_from_compound("local", local))
                except Exception as e:
                    warnings.append(f"level.dat local player: {e}")
    elif src.kind == "bedrock":
        records, w = scan_bedrock_db(src.root / "db")
        warnings.extend(w)
        for name, comp in records:
            # Bedrock player keys look like "~local_player" or "player_<clientid>"
            if not isinstance(comp, dict):
                continue
            looks_like_player = (
                _get_ci(comp, "Inventory") is not None
                or _get_ci(comp, "PlayerGameMode") is not None
                or _get_ci(comp, "SelectedInventorySlot") is not None
                or _get_ci(comp, "PlayerLevel") is not None
            )
            if looks_like_player:
                discovered += 1
                players.append(_extract_player_from_compound(name or "player", comp))
    else:
        warnings.append(f"Unsupported world kind for players: {src.kind}")

    return players, warnings, {"discovered": discovered, "parsed": len(players)}


# ---------- book extraction ----------

_BOOK_IDS = {
    "minecraft:written_book",
    "minecraft:writable_book",
    "minecraft:book",  # legacy edge cases
    "written_book",
    "writable_book",
}


def _pages_from_item(item: Dict[str, Any]) -> List[str]:
    tag = _get_ci(item, "tag", "components") or {}
    if not isinstance(tag, dict):
        return []
    # Legacy: tag.pages = [str, str, ...] or [{"raw":"..."}]
    pages = _get_ci(tag, "pages")
    # Modern components: minecraft:written_book_content / writable_book_content
    if pages is None:
        wbc = _get_ci(tag, "minecraft:written_book_content", "written_book_content")
        if isinstance(wbc, dict):
            pages = _get_ci(wbc, "pages")
        if pages is None:
            wbc = _get_ci(tag, "minecraft:writable_book_content", "writable_book_content")
            if isinstance(wbc, dict):
                pages = _get_ci(wbc, "pages")

    result: List[str] = []
    if isinstance(pages, dict) and "items" in pages:
        pages = pages["items"]
    if isinstance(pages, list):
        for p in pages:
            if isinstance(p, dict):
                # {"raw": "...", "filtered": "..."} or component JSON dict
                raw = _get_ci(p, "raw") or _get_ci(p, "text")
                result.append(_decode_text_component(raw if raw is not None else p))
            else:
                result.append(_decode_text_component(p))
    return result


def _title_author_from_item(item: Dict[str, Any]) -> Tuple[str | None, str | None]:
    tag = _get_ci(item, "tag", "components") or {}
    title = None
    author = None
    if isinstance(tag, dict):
        title = _get_ci(tag, "title")
        author = _get_ci(tag, "author")
        wbc = _get_ci(tag, "minecraft:written_book_content", "written_book_content")
        if isinstance(wbc, dict):
            title = title or _decode_text_component(_get_ci(wbc, "title"))
            author = author or _get_ci(wbc, "author")
    if isinstance(title, dict):
        title = _decode_text_component(title)
    return (title, author)


def _walk_for_books(node: Any, source_label: str, dim: str | None,
                    location: Tuple[float, float, float] | None,
                    out: List[BookRecord]) -> None:
    for item in _iter_items_in(node):
        name = _get_ci(item, "id", "Name") or ""
        if isinstance(name, str) and name.lower().split(":")[-1] in {n.split(":")[-1] for n in _BOOK_IDS}:
            title, author = _title_author_from_item(item)
            pages = _pages_from_item(item)
            key = f"{title}|{author}|{len(pages)}|{location}"
            out.append(BookRecord(
                title=title,
                author=author,
                source=source_label,
                location=location,
                dimension=dim,
                pages=pages,
                unique_key=key,
                raw=item,
            ))


def extract_books(src: WorldSource) -> Tuple[List[BookRecord], List[str], Dict[str, int]]:
    warnings: List[str] = []
    books: List[BookRecord] = []

    if src.kind == "java":
        # Player inventories
        players, pw, _ = extract_players(src)
        warnings.extend(pw)
        for p in players:
            _walk_for_books(p.raw, f"Player Inventory ({p.name or p.identifier})", p.dimension, None, books)
        # Region chunks: tile entities, entities, item frames
        for dim_label, region_dir in iter_all_dimensions(src.root):
            for src_name, chunk, w in iter_region_chunks(region_dir):
                warnings.extend(w)
                # Java 1.18+ splits: level tag holds sections/block_entities
                for be in (_get_ci(chunk, "block_entities") or _get_ci(chunk, "TileEntities") or []):
                    if isinstance(be, dict):
                        loc = None
                        x = _get_ci(be, "x"); y = _get_ci(be, "y"); z = _get_ci(be, "z")
                        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and isinstance(z, (int, float)):
                            loc = (float(x), float(y), float(z))
                        _walk_for_books(be, f"Block Entity ({dim_label})", dim_label, loc, books)
                for ent in (_get_ci(chunk, "entities") or _get_ci(chunk, "Entities") or []):
                    if isinstance(ent, dict):
                        pos = _get_ci(ent, "Pos")
                        loc = None
                        if isinstance(pos, dict) and pos.get("items") and len(pos["items"]) >= 3:
                            loc = tuple(float(v) for v in pos["items"][:3])  # type: ignore
                        eid = _get_ci(ent, "id") or ""
                        label = "Item Frame" if "item_frame" in str(eid).lower() else f"Entity {eid}"
                        _walk_for_books(ent, f"{label} ({dim_label})", dim_label, loc, books)
    elif src.kind == "bedrock":
        records, w = scan_bedrock_db(src.root / "db")
        warnings.extend(w)
        for name, comp in records:
            _walk_for_books(comp, f"Bedrock DB ({name or 'record'})", None, None, books)
    else:
        warnings.append(f"Unsupported world kind for books: {src.kind}")

    # Deduplicate by unique_key
    seen = set()
    dedup: List[BookRecord] = []
    for b in books:
        if b.unique_key in seen:
            continue
        seen.add(b.unique_key)
        dedup.append(b)
    return dedup, warnings, {"discovered": len(books), "parsed": len(dedup)}


# ---------- named mob extraction ----------

def _mob_from_entity(ent: Dict[str, Any], dim: str | None) -> NamedMobRecord | None:
    name_tag = _get_ci(ent, "CustomName")
    if name_tag is None:
        # Bedrock may store NameTag on entity data
        name_tag = _get_ci(ent, "NameTag") or _get_ci(ent, "name")
    if not name_tag:
        return None
    name = _decode_text_component(name_tag)
    if not name:
        return None
    pos = _get_ci(ent, "Pos")
    position = None
    if isinstance(pos, dict) and pos.get("items") and len(pos["items"]) >= 3:
        try:
            position = tuple(float(v) for v in pos["items"][:3])  # type: ignore
        except Exception:
            position = None
    attrs = _get_ci(ent, "Attributes")
    health = _get_ci(ent, "Health")
    if isinstance(health, dict):
        health = _get_ci(health, "Base") or _get_ci(health, "Value")
    max_health = None
    if isinstance(attrs, dict) and "items" in attrs:
        for a in attrs["items"]:
            if isinstance(a, dict):
                nm = _get_ci(a, "Name")
                if isinstance(nm, str) and nm.lower().endswith("max_health"):
                    max_health = _get_ci(a, "Base") or _get_ci(a, "Value")
    age = _get_ci(ent, "Age")
    return NamedMobRecord(
        name=name,
        mob_type=_get_ci(ent, "id"),
        health=float(health) if isinstance(health, (int, float)) else None,
        max_health=float(max_health) if isinstance(max_health, (int, float)) else None,
        position=position,
        dimension=dim,
        age=int(age) if isinstance(age, (int, float)) else None,
        raw=ent,
    )


def extract_named_mobs(src: WorldSource) -> Tuple[List[NamedMobRecord], List[str], Dict[str, int]]:
    warnings: List[str] = []
    mobs: List[NamedMobRecord] = []
    discovered = 0

    if src.kind == "java":
        for dim_label, region_dir in iter_all_dimensions(src.root):
            for _src_name, chunk, w in iter_region_chunks(region_dir):
                warnings.extend(w)
                for ent in (_get_ci(chunk, "entities") or _get_ci(chunk, "Entities") or []):
                    if isinstance(ent, dict):
                        discovered += 1
                        m = _mob_from_entity(ent, dim_label)
                        if m is not None:
                            mobs.append(m)
    elif src.kind == "bedrock":
        records, w = scan_bedrock_db(src.root / "db")
        warnings.extend(w)
        for name, comp in records:
            # Bedrock stores entities as separate NBT records with id key
            if isinstance(comp, dict) and (_get_ci(comp, "identifier") or _get_ci(comp, "id")):
                discovered += 1
                m = _mob_from_entity(comp, None)
                if m is not None:
                    mobs.append(m)
    else:
        warnings.append(f"Unsupported world kind for mobs: {src.kind}")

    return mobs, warnings, {"discovered": discovered, "parsed": len(mobs)}


def extract_all(src: WorldSource) -> ExtractionResult:
    r = ExtractionResult()
    players, pw, pc = extract_players(src)
    r.players = players
    r.warnings.extend(pw)
    r.counters["players_discovered"] = pc.get("discovered", 0)
    r.counters["players_parsed"] = pc.get("parsed", 0)

    books, bw, bc = extract_books(src)
    r.books = books
    r.warnings.extend(bw)
    r.counters["books_discovered"] = bc.get("discovered", 0)
    r.counters["books_parsed"] = bc.get("parsed", 0)

    mobs, mw, mc = extract_named_mobs(src)
    r.mobs = mobs
    r.warnings.extend(mw)
    r.counters["mobs_discovered"] = mc.get("discovered", 0)
    r.counters["mobs_parsed"] = mc.get("parsed", 0)

    r.warnings.extend(src.warnings)
    return r
