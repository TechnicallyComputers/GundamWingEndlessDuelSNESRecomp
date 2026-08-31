#!/usr/bin/env python3
"""Generate or verify compact CJK crawl runtime patches."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
import unicodedata
from pathlib import Path


BEGIN_MARKER = "# BEGIN GENERATED CJK CRAWL VRAM PATCHES"
END_MARKER = "# END GENERATED CJK CRAWL VRAM PATCHES"
LANG_SPECS = {
    "zh": ("prototype", "zh_compact", 2),
    "ko": ("pending", "ko", 2),
}
CJK_PATCH_LANGS = ("zh", "ko")


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


def load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def validate_hex(value: str, label: str, byte_len: int | None = None) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]*", value) or len(value) % 2:
        raise ValueError(f"{label}: invalid hex")
    if byte_len is not None and len(bytes.fromhex(value)) != byte_len:
        raise ValueError(f"{label}: expected {byte_len} bytes")
    return value.lower()


def tile_word_hex(tile: int) -> str:
    if tile < 0 or tile > 0x3ff:
        raise ValueError(f"tile out of range: {tile:#x}")
    return f"{0x2000 | tile:04x}"


def text_width(line: str, glyphs: dict, glyph_width: int) -> int:
    width = 0
    for char in line:
        if char == " " or char.isascii() or unicodedata.category(char).startswith("P"):
            width += 1
            continue
        if char not in glyphs:
            raise ValueError(f"missing generated CJK glyph for {char!r}")
        width += glyph_width
    return width


def centered_col(col: int, width: int) -> int:
    band_start = 32 if col >= 32 else 0
    return band_start + max((32 - width) // 2, 0)


def generate_language_hex(crawl: dict, candidates: dict, glyphs: dict, lang: str) -> str:
    group, key, glyph_width = LANG_SPECS[lang]
    lang_data = candidates[group][key]
    lines = list(lang_data["lines"])
    slots = crawl["line_slot"]
    row_width = int(crawl["row_width"])
    rows = int(crawl["rows"])
    slot_offset = int(lang_data.get("slot_offset", 0))
    if slot_offset < 0 or slot_offset + len(lines) > len(slots):
        raise ValueError(
            f"{lang}: slot_offset {slot_offset} with {len(lines)} lines "
            f"exceeds {len(slots)} crawl slots"
        )
    active_slots = slots[slot_offset:slot_offset + len(lines)]

    space = "2000"
    grid = [[space for _ in range(row_width)] for _ in range(rows)]
    lang_glyphs = glyphs["glyph"][lang]

    for line_number, line in enumerate(lines, 1):
        slot = active_slots[line_number - 1]
        top_row, top_col = [int(x) for x in slot["top"]]
        bottom_row, bottom_col = [int(x) for x in slot["bottom"]]
        width = text_width(line, lang_glyphs, glyph_width)
        top_col = centered_col(top_col, width)
        bottom_col = centered_col(bottom_col, width)
        for row, col, name in ((top_row, top_col, "top"), (bottom_row, bottom_col, "bottom")):
            if row < 0 or row >= rows:
                raise ValueError(f"{lang} line {line_number}: {name} row {row} outside 0..{rows - 1}")
            if col < 0 or col + width > row_width:
                raise ValueError(
                    f"{lang} line {line_number}: {name} column {col} plus {width} "
                    f"tiles exceeds row width {row_width}"
                )
        cursor = 0
        for char in line:
            if char == " " or char.isascii() or unicodedata.category(char).startswith("P"):
                top = bottom = space
                grid[top_row][top_col + cursor] = top
                grid[bottom_row][bottom_col + cursor] = bottom
                cursor += 1
            else:
                glyph = lang_glyphs[char]
                top_left = tile_word_hex(int(glyph["top_left_tile"]))
                bottom_left = tile_word_hex(int(glyph["bottom_left_tile"]))
                grid[top_row][top_col + cursor] = top_left
                grid[bottom_row][bottom_col + cursor] = bottom_left
                if glyph_width == 2:
                    top_right = tile_word_hex(int(glyph["top_right_tile"]))
                    bottom_right = tile_word_hex(int(glyph["bottom_right_tile"]))
                    grid[top_row][top_col + cursor + 1] = top_right
                    grid[bottom_row][bottom_col + cursor + 1] = bottom_right
                cursor += glyph_width

    return "".join(entry for row in grid for entry in row)


def parse_rom_patch_blocks(table_text: str) -> list[tuple[int, int, str]]:
    all_boundaries = [
        m.start()
        for m in re.finditer(
            r"(?m)^(?:\[\[[^\]]+\]\]|# BEGIN GENERATED .+|# END GENERATED .+)\s*$",
            table_text,
        )
    ]
    rom_starts = [m.start() for m in re.finditer(r"(?m)^\[\[rom_patch\]\]\s*$", table_text)]
    blocks: list[tuple[int, int, str]] = []
    for start in rom_starts:
        later_boundaries = [section for section in all_boundaries if section > start]
        end = later_boundaries[0] if later_boundaries else len(table_text)
        blocks.append((start, end, table_text[start:end]))
    return blocks


def find_crawl_block(table_text: str, address: int, byte_len: int) -> tuple[int, int, str]:
    address_re = re.compile(r"(?m)^address\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*$")
    for start, end, block in parse_rom_patch_blocks(table_text):
        match = address_re.search(block)
        source = re.search(r'(?m)^source_hex\s*=\s*"([0-9a-fA-F]+)"\s*$', block)
        if (
            match
            and int(match.group(1), 0) == address
            and source
            and len(bytes.fromhex(source.group(1))) == byte_len
        ):
            return start, end, block
    raise ValueError(f"crawl rom_patch block at {address:#x} not found")


def update_crawl_block(block: str, generated: dict[str, str]) -> str:
    out = block
    for lang in CJK_PATCH_LANGS:
        if lang not in generated:
            out = re.sub(rf'(?m)^{lang}_hex[ \t]*=[ \t]*"[0-9a-fA-F]*"[ \t]*\n?', "", out)
    for lang, hex_value in generated.items():
        key = f"{lang}_hex"
        line = f'{key} = "{hex_value}"'
        pattern = re.compile(rf'(?m)^{key}[ \t]*=[ \t]*"[0-9a-fA-F]*"[ \t]*$')
        if pattern.search(out):
            out = pattern.sub(line, out, count=1)
        else:
            out = out.rstrip() + "\n" + line + "\n"
    return out


def tile_address(tile_base_word: int, tile: int) -> int:
    return ((tile_base_word + tile * 8) & 0x7fff) * 2


def generated_vram_blocks(glyphs: dict) -> list[str]:
    tile_base_word = int(glyphs["tile_base_word"])
    by_tile: dict[int, dict[str, str]] = {}
    for lang in LANG_SPECS:
        glyph_width = int(glyphs.get("languages", {}).get(lang, {}).get("glyph_width", 2))
        for glyph in glyphs["glyph"][lang].values():
            parts = ["top_left", "bottom_left"]
            if glyph_width == 2:
                parts.extend(["top_right", "bottom_right"])
            for part in parts:
                tile = int(glyph[f"{part}_tile"])
                source = validate_hex(str(glyph[f"{part}_source_hex"]), f"{lang} {tile:#x} source", 16)
                target = validate_hex(str(glyph[f"{part}_hex"]), f"{lang} {tile:#x}", 16)
                entry = by_tile.setdefault(tile, {"source_hex": source})
                if entry["source_hex"] != source:
                    raise ValueError(f"source mismatch for shared tile {tile:#x}")
                entry[f"{lang}_hex"] = target

    blocks: list[str] = [BEGIN_MARKER]
    for tile in sorted(by_tile):
        entry = by_tile[tile]
        lines = [
            "",
            "[[vram_patch]]",
            f"address = 0x{tile_address(tile_base_word, tile):04x}",
            f'source_hex = "{entry["source_hex"]}"',
        ]
        for lang in sorted(LANG_SPECS):
            if f"{lang}_hex" in entry:
                lines.append(f'{lang}_hex = "{entry[f"{lang}_hex"]}"')
        blocks.append("\n".join(lines))
    blocks.append("")
    blocks.append(END_MARKER)
    blocks.append("")
    return blocks


def replace_generated_section(table_text: str, section_text: str) -> str:
    pattern = re.compile(
        rf"(?ms)^# BEGIN GENERATED CJK CRAWL VRAM PATCHES\n.*?^# END GENERATED CJK CRAWL VRAM PATCHES\n*"
    )
    if pattern.search(table_text):
        return pattern.sub(section_text.rstrip() + "\n", table_text, count=1)
    return table_text.rstrip() + "\n\n" + section_text.rstrip() + "\n"


def normalize_embedded_markers(table_text: str) -> str:
    """Repair old generated output that missed a newline before a marker."""
    return re.sub(
        r'("[0-9a-fA-F]*")# BEGIN GENERATED ([^\r\n]+)',
        r'\1\n# BEGIN GENERATED \2',
        table_text,
    )


def table_targets(block: str) -> dict[str, str]:
    return {
        key[:-4]: value.lower()
        for key, value in re.findall(r'(?m)^([a-z]{2}_hex)\s*=\s*"([0-9a-fA-F]*)"\s*$', block)
    }


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="rewrite generated CJK crawl patches")
    parser.add_argument("--check", action="store_true", help="fail if generated patches differ")
    parser.add_argument("--crawl", default=str(root / "translations" / "endless_duel_crawl.toml"))
    parser.add_argument("--candidates", default=str(root / "translations" / "endless_duel_cjk_candidates.toml"))
    parser.add_argument("--glyphs", default=str(root / "translations" / "endless_duel_cjk_glyphs.toml"))
    parser.add_argument("--table", default=str(root / "translations" / "endless_duel.toml"))
    args = parser.parse_args()

    crawl = load_toml(Path(args.crawl))
    candidates = load_toml(Path(args.candidates))
    glyphs = load_toml(Path(args.glyphs))
    generated = {
        lang: generate_language_hex(crawl, candidates, glyphs, lang)
        for lang in sorted(LANG_SPECS)
    }
    vram_section = "\n".join(generated_vram_blocks(glyphs))

    table_path = Path(args.table)
    table_text = normalize_embedded_markers(table_path.read_text(encoding="utf-8"))
    aggregate_len = int(crawl["row_width"]) * int(crawl["rows"]) * 2
    start, end, block = find_crawl_block(table_text, int(crawl["patch_address"]), aggregate_len)
    updated_block = update_crawl_block(block, generated)
    updated = table_text[:start] + updated_block + table_text[end:]
    updated = replace_generated_section(updated, vram_section)

    if args.write:
        if updated != table_text:
            table_path.write_text(updated, encoding="utf-8", newline="\n")
            print(f"updated {table_path} with compact CJK crawl patches")
        else:
            print(f"compact CJK crawl patches already match {table_path}")
        return 0

    current = table_targets(block)
    mismatches = [lang for lang, hex_value in generated.items() if current.get(lang) != hex_value]
    if replace_generated_section(table_text, vram_section) != table_text:
        mismatches.append("vram_patch_section")
    if mismatches:
        for item in mismatches:
            print(f"{item}: generated CJK patch differs from {table_path}", file=sys.stderr)
        return 1

    print(
        f"compact CJK crawl patches match {table_path}: "
        f"{', '.join(sorted(generated))}, {len(generated_vram_blocks(glyphs)) - 3} VRAM tiles"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
