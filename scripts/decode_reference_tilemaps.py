#!/usr/bin/env python3
"""Decode Endless Duel reference dialogue tilemaps into readable lines."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__.replace("\\", "/")).resolve().parent))

from analyze_reference_ips import apply_records, parse_ips, repo_root


SCRIPT_RANGES = (
    (0x017000, 0x018000, "battle_dialogue_1"),
    (0x026B00, 0x027B00, "battle_dialogue_2"),
    (0x03EB00, 0x03FB00, "battle_dialogue_3"),
    (0x05E000, 0x05F000, "ending_dialogue"),
)

# Tile IDs from the top half of a 16px glyph. Bottom halves are top + 0x10.
CHAR_BY_TILE = {
    0x002: "A",
    0x003: "B",
    0x004: "C",
    0x005: "D",
    0x006: "E",
    0x007: "F",
    0x008: "G",
    0x009: "H",
    0x00A: "I",
    0x00B: "J",
    0x00C: "K",
    0x00D: "L",
    0x00E: "M",
    0x00F: "N",
    0x020: "O",
    0x021: "P",
    0x022: "Q",
    0x023: "R",
    0x024: "S",
    0x025: "T",
    0x026: "U",
    0x027: "V",
    0x028: "W",
    0x029: "X",
    0x02A: "Y",
    0x02B: "Z",
    0x02C: "a",
    0x02D: "b",
    0x02E: "c",
    0x02F: "d",
    0x040: "e",
    0x041: "f",
    0x042: "g",
    0x043: "h",
    0x044: "i",
    0x045: "j",
    0x046: "k",
    0x047: "l",
    0x048: "m",
    0x049: "n",
    0x04A: "o",
    0x04B: "p",
    0x04C: "q",
    0x04D: "r",
    0x04E: "s",
    0x04F: "t",
    0x060: " ",
    0x061: "u",
    0x062: "v",
    0x063: "w",
    0x064: "x",
    0x065: "y",
    0x066: "z",
    0x067: ".",
    0x068: ",",
    0x069: "'",
    0x06C: "?",
    0x06D: "!",
    0x06E: "\u00bf",
    0x06F: "\u00a1",
}


@dataclass
class DecodedLine:
    address: int
    en: str
    es: str
    en_ids: list[int]
    es_ids: list[int]
    start_col: int
    en_hex: str
    es_hex: str
    bottom_ok_en: bool
    bottom_ok_es: bool


def tile_words(image: bytes, address: int, count: int = 32) -> list[int]:
    words = []
    for i in range(count):
        offset = address + i * 2
        words.append(image[offset] | (image[offset + 1] << 8))
    return words


def text_ids(words: list[int]) -> list[int]:
    ids = [word & 0x03FF for word in words]
    while ids and (ids[-1] == 0 or ids[-1] not in CHAR_BY_TILE):
        ids.pop()
    while ids and (ids[0] == 0 or ids[0] not in CHAR_BY_TILE):
        ids.pop(0)
    return ids


def first_text_col(words: list[int]) -> int:
    for index, word in enumerate(words):
        if (word & 0x03FF) in CHAR_BY_TILE:
            return index
    return 0


def decode_ids(ids: list[int]) -> str:
    return "".join(CHAR_BY_TILE.get(tile, f"[0x{tile:03x}]") for tile in ids).rstrip()


def bottom_matches(top_words: list[int], bottom_words: list[int]) -> bool:
    for top, bottom in zip(top_words, bottom_words, strict=True):
        top_id = top & 0x03FF
        bottom_id = bottom & 0x03FF
        if top_id in (0, 0x060) or top_id not in CHAR_BY_TILE:
            continue
        if bottom_id != top_id + 0x10:
            return False
    return True


def decode_range(en_image: bytes, es_image: bytes, start: int, end: int) -> list[DecodedLine]:
    lines: list[DecodedLine] = []
    for address in range(start, end, 0x80):
        en_top = tile_words(en_image, address)
        en_bottom = tile_words(en_image, address + 0x40)
        es_top = tile_words(es_image, address)
        es_bottom = tile_words(es_image, address + 0x40)
        en_ids = text_ids(en_top)
        es_ids = text_ids(es_top)
        if not en_ids and not es_ids:
            continue
        lines.append(
            DecodedLine(
                address=address,
                en=decode_ids(en_ids),
                es=decode_ids(es_ids),
                en_ids=en_ids,
                es_ids=es_ids,
                start_col=first_text_col(en_top),
                en_hex=en_image[address:address + 0x80].hex(),
                es_hex=es_image[address:address + 0x80].hex(),
                bottom_ok_en=bottom_matches(en_top, en_bottom),
                bottom_ok_es=bottom_matches(es_top, es_bottom),
            )
        )
    return lines


def toml_quote(value: str) -> str:
    parts: list[str] = []
    for char in value:
        code = ord(char)
        if char == "\\":
            parts.append("\\\\")
        elif char == '"':
            parts.append('\\"')
        elif char == "\n":
            parts.append("\\n")
        elif 0x20 <= code <= 0x7E:
            parts.append(char)
        elif code <= 0xFFFF:
            parts.append(f"\\u{code:04x}")
        else:
            parts.append(f"\\U{code:08x}")
    return '"' + "".join(parts) + '"'


def ascii_escape(value: str) -> str:
    parts: list[str] = []
    for char in value:
        code = ord(char)
        if 0x20 <= code <= 0x7E:
            parts.append(char)
        else:
            parts.append(f"&#x{code:04x};")
    return "".join(parts)


def write_toml(path: Path, decoded: dict[str, list[DecodedLine]]) -> None:
    lines = [
        "# Decoded source for the English and Spanish reference dialogue tilemaps.",
        "# Regenerate with scripts/decode_reference_tilemaps.py --write after updating",
        "# the decoder's tile table or reference-patch inputs.",
        "",
        "schema = 1",
        "row_width = 32",
        "line_stride = 0x80",
        "bottom_row_offset = 0x40",
        "space_tile = 0x060",
        "blank_tile = 0x000",
        "",
        "# Bottom-half glyphs are top_tile + 0x10.",
    ]
    for tile, char in sorted(CHAR_BY_TILE.items()):
        if char == " ":
            escaped = "<space>"
        else:
            escaped = char if char.isascii() else f"\\u{ord(char):04x}"
        lines.append(f"# tile 0x{tile:03x} = {escaped}")
    lines.append("")
    for name, entries in decoded.items():
        for entry in entries:
            lines.append("[[line]]")
            lines.append(f'group = "{name}"')
            lines.append(f"address = 0x{entry.address:06x}")
            lines.append(f"start_col = {entry.start_col}")
            lines.append(f"en = {toml_quote(entry.en)}")
            lines.append(f"es = {toml_quote(entry.es)}")
            lines.append(f"bottom_ok_en = {str(entry.bottom_ok_en).lower()}")
            lines.append(f"bottom_ok_es = {str(entry.bottom_ok_es).lower()}")
            lines.append(f'en_hex = "{entry.en_hex}"')
            lines.append(f'es_hex = "{entry.es_hex}"')
            lines.append(
                "en_ids = [" + ", ".join(f"0x{tile:03x}" for tile in entry.en_ids) + "]"
            )
            lines.append(
                "es_ids = [" + ", ".join(f"0x{tile:03x}" for tile in entry.es_ids) + "]"
            )
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_md(path: Path, decoded: dict[str, list[DecodedLine]]) -> None:
    lines = [
        "# Endless Duel Decoded Reference Dialogue",
        "",
        "Generated by `scripts/decode_reference_tilemaps.py`. The English and",
        "Spanish fan patches store many dialogue lines as pre-rendered 16px tilemaps:",
        "one 32-word top row followed by one 32-word bottom row. Bottom glyph tiles",
        "are the top tile plus `0x10`; in-line spaces are tile `0x060`; out-of-line",
        "padding is tile `0x000`.",
        "",
        "The decoder proves these ranges are text-layout data rather than opaque",
        "graphics. Translation parity for French, Italian, and Portuguese can be",
        "implemented by adding target strings for these rows and regenerating",
        "language-specific tilemap bytes with the same tile table.",
        "",
    ]
    for name, entries in decoded.items():
        lines.extend([f"## {name}", "", "| Address | English | Spanish |", "| --- | --- | --- |"])
        for entry in entries:
            en = ascii_escape(entry.en)
            es = ascii_escape(entry.es)
            if not entry.bottom_ok_en:
                en += " (WARNING: top/bottom glyph mismatch)"
            if not entry.bottom_ok_es:
                es += " (WARNING: top/bottom glyph mismatch)"
            lines.append(f"| `0x{entry.address:06x}` | {en} | {es} |")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", default=str(root / "Shin Kidou Senki Gundam W - Endless Duel (J).smc"))
    parser.add_argument("--en-ips", required=True)
    parser.add_argument("--es-ips", required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--allow-mismatch", action="store_true")
    parser.add_argument("--toml", default=str(root / "translations" / "endless_duel_dialogue.toml"))
    parser.add_argument("--md", default=str(root / "translations" / "reference_dialogue_decode.md"))
    args = parser.parse_args()

    source = Path(args.rom).read_bytes()
    en_image = apply_records(source, parse_ips(Path(args.en_ips), -0x200), "en")
    es_image = apply_records(source, parse_ips(Path(args.es_ips), 0), "es")
    decoded = {
        name: decode_range(en_image, es_image, start, end)
        for start, end, name in SCRIPT_RANGES
    }

    total = sum(len(entries) for entries in decoded.values())
    failures = [
        entry
        for entries in decoded.values()
        for entry in entries
        if not entry.bottom_ok_en or not entry.bottom_ok_es
    ]
    print(f"decoded dialogue lines: {total}")
    for name, entries in decoded.items():
        print(f"  {name}: {len(entries)} lines")
    print(f"bottom-row validation failures: {len(failures)}")
    if failures:
        for entry in failures[:10]:
            print(
                f"  0x{entry.address:06x}: "
                f"en={entry.bottom_ok_en} es={entry.bottom_ok_es}"
            )
        if not args.allow_mismatch:
            return 1

    if args.write:
        write_toml(Path(args.toml), decoded)
        write_md(Path(args.md), decoded)
        print(f"wrote {args.toml}")
        print(f"wrote {args.md}")
    else:
        for name, entries in decoded.items():
            print()
            print(name)
            for entry in entries:
                print(f"  0x{entry.address:06x}: {entry.en} / {entry.es}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
