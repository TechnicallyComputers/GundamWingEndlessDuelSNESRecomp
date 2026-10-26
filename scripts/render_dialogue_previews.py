#!/usr/bin/env python3
"""Render decoded dialogue overlays as reviewable SVG contact sheets."""

from __future__ import annotations

import argparse
import html
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__.replace("\\", "/")).resolve().parent))

from analyze_reference_ips import repo_root
from decode_reference_tilemaps import CHAR_BY_TILE, bottom_matches, tile_words
from generate_dialogue_accent_patch import (
    load_source as load_accent_source,
    per_language_charmap,
)
from generate_dialogue_patch import AUTHORED_LANGS, encode_line, load_targets


ROW_WORDS = 32
ROW_BYTES = ROW_WORDS * 2


def load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def row_text(row_hex: str, char_by_tile: dict[int, str] | None = None) -> str:
    char_by_tile = CHAR_BY_TILE if char_by_tile is None else char_by_tile
    data = bytes.fromhex(row_hex)
    words = [data[i] | (data[i + 1] << 8) for i in range(0, ROW_BYTES, 2)]
    chars: list[str] = []
    for word in words:
        tile = word & 0x03FF
        if tile in (0, 0x060):
            chars.append(" ")
        else:
            chars.append(char_by_tile.get(tile, "?"))
    return "".join(chars)


def validate_encoded(address: int, start_col: int, text: str, encoded_hex: str,
                     char_by_tile: dict[int, str]) -> None:
    data = bytes.fromhex(encoded_hex)
    if len(data) != ROW_BYTES * 2:
        raise ValueError(f"0x{address:06x}: generated row width is {len(data):#x}")
    decoded = row_text(encoded_hex, char_by_tile)
    slot = decoded[start_col:start_col + len(text)]
    if slot != text:
        raise ValueError(
            f"0x{address:06x}: decoded tile row {slot!r} does not match {text!r}"
        )
    top = tile_words(data, 0, ROW_WORDS)
    bottom = tile_words(data, ROW_BYTES, ROW_WORDS)
    if not bottom_matches(top, bottom):
        raise ValueError(f"0x{address:06x}: generated bottom glyph row mismatch")


def cell_svg(x: int, y: int, char: str, index: int, start_col: int, text_len: int) -> str:
    active = start_col <= index < start_col + text_len
    fill = "#ffffff"
    if active and char != " ":
        fill = "#e8f1ff"
    elif active:
        fill = "#f6f8fb"
    elif char.strip():
        fill = "#fff8dd"
    escaped = html.escape(char) if char.strip() else " "
    return (
        f'<rect x="{x}" y="{y}" width="16" height="22" fill="{fill}" '
        f'stroke="#c8d0da" stroke-width="1"/>'
        f'<text x="{x + 8}" y="{y + 16}" text-anchor="middle" '
        f'font-family="Consolas, monospace" font-size="13" fill="#1c2430">{escaped}</text>'
    )


def render_lang(lang: str, entries: list[dict], targets: dict[int, dict[str, str]],
                charmap: dict[str, int], out_path: Path) -> int:
    char_by_tile = {tile: char for char, tile in charmap.items()}
    row_height = 34
    width = 1010
    height = 72 + len(entries) * row_height
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f7f8fb"/>',
        f'<text x="18" y="28" font-family="Segoe UI, Arial, sans-serif" font-size="20" font-weight="700" fill="#111827">Endless Duel dialogue overlay preview: {html.escape(lang)}</text>',
        '<text x="18" y="52" font-family="Segoe UI, Arial, sans-serif" font-size="12" fill="#4b5563">Each blue cell is one 16px dialogue glyph slot generated for the runtime patch.</text>',
        '<text x="18" y="76" font-family="Consolas, monospace" font-size="11" fill="#6b7280">address  group                 EN reference                     generated 32-slot target row</text>',
    ]
    count = 0
    for row, entry in enumerate(entries):
        address = int(entry["address"])
        text = targets.get(address, {}).get(lang)
        if not text:
            continue
        start_col = int(entry["start_col"])
        encoded = encode_line(entry["en_hex"], start_col, text, charmap)
        validate_encoded(address, start_col, text, encoded, char_by_tile)
        decoded = row_text(encoded, char_by_tile)
        y = 88 + row * row_height
        shade = "#ffffff" if row % 2 == 0 else "#f1f4f8"
        lines.append(f'<rect x="12" y="{y - 17}" width="{width - 24}" height="{row_height}" fill="{shade}"/>')
        lines.append(
            f'<text x="18" y="{y}" font-family="Consolas, monospace" font-size="12" fill="#111827">0x{address:06x}</text>'
        )
        lines.append(
            f'<text x="94" y="{y}" font-family="Consolas, monospace" font-size="12" fill="#374151">{html.escape(str(entry.get("group", ""))[:20])}</text>'
        )
        lines.append(
            f'<text x="248" y="{y}" font-family="Consolas, monospace" font-size="12" fill="#4b5563">{html.escape(str(entry.get("en", ""))[:27])}</text>'
        )
        grid_x = 474
        grid_y = y - 17
        for index, char in enumerate(decoded):
            lines.append(cell_svg(grid_x + index * 16, grid_y, char, index, start_col, len(text)))
        count += 1
    lines.append("</svg>")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return count


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=str(root / "translations" / "endless_duel_dialogue.toml"),
        help="decoded English/Spanish dialogue source table",
    )
    parser.add_argument(
        "--targets",
        default=str(root / "translations" / "endless_duel_dialogue_targets.toml"),
        help="authored target-language dialogue table",
    )
    parser.add_argument(
        "--out",
        default=str(root / "translations" / "dialogue_previews"),
        help="directory for generated SVG contact sheets",
    )
    parser.add_argument(
        "--accents",
        default=str(root / "translations" / "endless_duel_dialogue_accents.toml"),
        help="per-language accented glyph cell allocation table",
    )
    # es is included: its only authored rows are the reference repairs, so the
    # preview doubles as the review sheet for exactly those rows.
    parser.add_argument("--langs", default=",".join(AUTHORED_LANGS))
    args = parser.parse_args()

    accents = load_accent_source(Path(args.accents))
    source = load_toml(Path(args.source))
    entries = source.get("line", [])
    targets = load_targets(Path(args.targets))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    requested = [lang.strip() for lang in args.langs.split(",") if lang.strip()]
    bad = [lang for lang in requested if lang not in AUTHORED_LANGS]
    if bad:
        raise ValueError(f"unsupported preview language(s): {', '.join(bad)}")

    for lang in requested:
        count = render_lang(lang, entries, targets,
                            per_language_charmap(accents, lang),
                            out_dir / f"dialogue_{lang}.svg")
        print(f"{lang}: rendered {count} dialogue rows")
    print(f"wrote {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
