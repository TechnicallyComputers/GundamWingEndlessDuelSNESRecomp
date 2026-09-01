#!/usr/bin/env python3
"""Compose per-language accented glyph cells for the 8x16 dialogue font.

The dialogue font is raw in the cart at 0x006f00 (2bpp, 16 bytes/tile), so the
glyph art ships as ``[[rom_patch]]`` entries rather than VRAM interception.

Each allocated cell is owned WHOLE by this generator (top tile and bottom tile,
16 bytes each). The imported reference IPS fragments that used to overlap those
tiles are split or removed by ``--write``, and the generated entries carry the
pre-split English and Spanish bytes verbatim, so the en/es ROM images are
byte-identical across the restructuring. That invariant is re-proved by
``--check``.

Usage:
    py -3 scripts/generate_dialogue_accent_patch.py --capture-baseline
    py -3 scripts/generate_dialogue_accent_patch.py --write
    py -3 scripts/generate_dialogue_accent_patch.py --check
    py -3 scripts/generate_dialogue_accent_patch.py --previews
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__.replace("\\", "/")).resolve().parent))

from decode_reference_tilemaps import CHAR_BY_TILE
from generate_intro_caption_patch import split_rom_patch_blocks
from reconstruct_table_image import (
    build_image,
    default_rom_path,
    default_table_path,
    load_table,
    repo_root,
)

BEGIN_MARK = "# BEGIN GENERATED DIALOGUE ACCENT GLYPH PATCHES"
END_MARK = "# END GENERATED DIALOGUE ACCENT GLYPH PATCHES"

TILE_BY_CHAR = {char: tile for tile, char in CHAR_BY_TILE.items()}
BASELINE_LANGS = ("en", "es")


class GenError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 2bpp tile pixel helpers
# ---------------------------------------------------------------------------

def tile_to_pixels(blob: bytes) -> list[list[int]]:
    if len(blob) != 16:
        raise GenError(f"2bpp tile must be 16 bytes, got {len(blob)}")
    rows = []
    for row in range(8):
        plane0 = blob[row * 2]
        plane1 = blob[row * 2 + 1]
        rows.append([
            ((plane0 >> (7 - col)) & 1) | (((plane1 >> (7 - col)) & 1) << 1)
            for col in range(8)
        ])
    return rows


def pixels_to_tile(rows: list[list[int]]) -> bytes:
    out = bytearray()
    for row in rows:
        plane0 = plane1 = 0
        for col, value in enumerate(row):
            if value & 1:
                plane0 |= 1 << (7 - col)
            if value & 2:
                plane1 |= 1 << (7 - col)
        out += bytes((plane0, plane1))
    return bytes(out)


# ---------------------------------------------------------------------------
# source model
# ---------------------------------------------------------------------------

def load_source(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def marks_by_name(source: dict) -> dict[str, list[tuple[str, int, list[int]]]]:
    """name -> [(half, row, cols)].

    ``half`` is "top" (the default: a diacritic above the x-height, stamped in
    the cleared band at the top of the top tile) or "bottom" (a diacritic below
    the baseline - the cedilla - stamped in the cleared band at the bottom of
    the bottom tile, where the font's own descenders live).
    """
    marks: dict[str, list[tuple[str, int, list[int]]]] = {}
    for entry in source.get("mark", []):
        half = str(entry.get("half", "top"))
        if half not in ("top", "bottom"):
            raise GenError(f"mark {entry['name']!r}: bad half {half!r}")
        marks.setdefault(str(entry["name"]), []).append(
            (half, int(entry["row"]), [int(c) for c in entry["cols"]])
        )
    return marks


def cell_tiles(source: dict, cell: dict) -> tuple[int, int]:
    top = int(cell["tile"])
    return top, top + int(source["bottom_tile_offset"])


def tile_address(source: dict, tile: int) -> int:
    return int(source["font_address"]) + tile * int(source["tile_bytes"])


def compose_cell(source: dict, cell: dict, en_image: bytes) -> tuple[bytes, bytes]:
    """Return (top_art, bottom_art) for one allocated cell."""
    base_char = str(cell["base"])
    if base_char not in TILE_BY_CHAR:
        raise GenError(f"base glyph {base_char!r} is not in the dialogue charmap")
    base_top = TILE_BY_CHAR[base_char]
    base_bottom = base_top + int(source["bottom_tile_offset"])
    size = int(source["tile_bytes"])

    top = tile_to_pixels(en_image[tile_address(source, base_top):
                                  tile_address(source, base_top) + size])
    bottom = tile_to_pixels(en_image[tile_address(source, base_bottom):
                                     tile_address(source, base_bottom) + size])

    background = int(source["background"])
    ink = int(source["ink"])
    clear_top = int(source["clear_top_rows"])
    clear_bottom = int(source.get("clear_bottom_rows", 0))
    if not 0 <= clear_bottom <= 8:
        raise GenError(f"clear_bottom_rows {clear_bottom} is out of range 0..8")
    for row in range(clear_top + 1):
        top[row] = [background] * 8
    for row in range(8 - clear_bottom, 8):
        bottom[row] = [background] * 8

    marks = marks_by_name(source)
    mark_name = str(cell["mark"])
    if mark_name not in marks:
        raise GenError(f"unknown diacritic {mark_name!r}")
    for half, row, cols in marks[mark_name]:
        if half == "top":
            if not 0 <= row <= clear_top:
                raise GenError(
                    f"{mark_name} top row {row} is outside the cleared band "
                    f"0..{clear_top}")
            target = top
        else:
            if not 8 - clear_bottom <= row <= 7:
                raise GenError(
                    f"{mark_name} bottom row {row} is outside the cleared band "
                    f"{8 - clear_bottom}..7")
            target = bottom
        for col in cols:
            target[row][col] = ink

    return pixels_to_tile(top), pixels_to_tile(bottom)


def allocations(source: dict) -> dict[str, dict[str, int]]:
    """lang -> {char: top tile}. Also validates the per-language budget."""
    out: dict[str, dict[str, int]] = {}
    for cell in source.get("cell", []):
        lang = str(cell["lang"])
        char = str(cell["char"])
        tile = int(cell["tile"])
        table = out.setdefault(lang, {})
        if char in table:
            raise GenError(f"{lang}: duplicate allocation for {char!r}")
        if tile in table.values():
            raise GenError(f"{lang}: tile 0x{tile:03x} allocated twice")
        claimed = CHAR_BY_TILE.get(tile)
        if claimed is not None and claimed.isascii():
            # 0x06a/0x06b hold ':' and ';' in the reference font and are not in
            # the charmap at all; 0x06e/0x06f hold the Spanish inverted marks,
            # which fr/it text can never contain. Anything the charmap maps to
            # an ASCII character is a glyph the encoder may emit, so it is off
            # limits.
            raise GenError(
                f"{lang}: tile 0x{tile:03x} is the live glyph {claimed!r}")
        table[char] = tile
    return out


def per_language_charmap(source: dict, lang: str) -> dict[str, int]:
    """Base charmap plus this language's accented cells."""
    table = dict(TILE_BY_CHAR)
    for char, tile in allocations(source).get(lang, {}).items():
        table[char] = tile
    return table


# ---------------------------------------------------------------------------
# rects / section emission
# ---------------------------------------------------------------------------

def owned_tiles(source: dict) -> list[int]:
    tiles: set[int] = set()
    for cell in source.get("cell", []):
        top, bottom = cell_tiles(source, cell)
        tiles.add(top)
        tiles.add(bottom)
    return sorted(tiles)


def owned_rects(source: dict) -> list[tuple[int, int]]:
    size = int(source["tile_bytes"])
    return [(tile_address(source, tile), size) for tile in owned_tiles(source)]


def generate_section(source: dict, rom: bytes, en_image: bytes) -> str:
    size = int(source["tile_bytes"])
    art: dict[int, dict[str, bytes]] = {}
    for cell in source.get("cell", []):
        lang = str(cell["lang"])
        top_tile, bottom_tile = cell_tiles(source, cell)
        top_art, bottom_art = compose_cell(source, cell, en_image)
        art.setdefault(top_tile, {})[lang] = top_art
        art.setdefault(bottom_tile, {})[lang] = bottom_art

    baseline = source.get("baseline", {})
    lines = [
        BEGIN_MARK,
        "# Per-language accented glyph cells for the raw 8x16 dialogue font at",
        "# 0x006f00, composed from the English font art by",
        "# scripts/generate_dialogue_accent_patch.py from",
        "# translations/endless_duel_dialogue_accents.toml.",
        "# These entries own each allocated tile whole; the reference IPS",
        "# fragments that used to overlap them were split by --write, and the",
        "# en/es payloads below are the pre-split bytes, so the English and",
        "# Spanish ROM images are unchanged. Do not hand-edit.",
        "",
    ]
    for tile in owned_tiles(source):
        address = tile_address(source, tile)
        key = f"tile_{tile:03x}"
        record = baseline.get(key)
        if not record:
            raise GenError(
                f"no captured baseline for tile 0x{tile:03x}; run "
                f"--capture-baseline against a table that still has the "
                f"reference fragments")
        chars = ", ".join(
            f"{cell['lang']}={cell['char']}"
            for cell in source.get("cell", [])
            if tile in cell_tiles(source, cell)
        )
        bottoms = {cell_tiles(source, cell)[1] for cell in source.get("cell", [])}
        half = "bottom" if tile in bottoms else "top"
        lines.append("[[rom_patch]]")
        lines.append(f"# dialogue font tile 0x{tile:03x} ({half} half) - {chars}")
        lines.append(f"address = 0x{address:06x}")
        lines.append(f'source_hex = "{rom[address:address + size].hex()}"')
        for lang in BASELINE_LANGS:
            lines.append(f'{lang}_hex = "{record[lang]}"')
        for lang in sorted(art.get(tile, {})):
            lines.append(f'{lang}_hex = "{art[tile][lang].hex()}"')
        lines.append("")
    lines.append(END_MARK)
    return "\n".join(lines)


# Sentinel left behind by strip_section so replace_section can put the
# regenerated section back exactly where the old one lived. Without it the
# stripped text has no markers to find and the section is appended at EOF,
# silently RELOCATING it past every generated section that followed it - and
# rom_patch file order is application order.
PLACEHOLDER = "# ACCENT SECTION PLACEHOLDER (generator internal)"


def replace_section(text: str, section: str) -> str:
    if PLACEHOLDER in text:
        return text.replace(PLACEHOLDER, section, 1)
    pattern = re.compile(
        re.escape(BEGIN_MARK) + r".*?" + re.escape(END_MARK), re.S)
    match = pattern.search(text)
    if match:
        return text[:match.start()] + section + text[match.end():]
    return text.rstrip() + "\n\n" + section + "\n"


def strip_section(text: str) -> str:
    """Remove this generator's own section, leaving PLACEHOLDER in its place.

    Idempotence: the splitter must never see the entries this generator
    emitted on a previous run - they cover the owned rects exactly, so it
    would delete them along with the marker comments that follow them.
    """
    pattern = re.compile(
        r"\n*" + re.escape(BEGIN_MARK) + r".*?" + re.escape(END_MARK) + r"\n*",
        re.S)
    return pattern.sub("\n\n" + PLACEHOLDER + "\n\n", text)


# ---------------------------------------------------------------------------
# baseline capture + invariants
# ---------------------------------------------------------------------------

def capture_baseline(source_path: Path, source: dict, table: dict,
                     rom: bytes) -> None:
    size = int(source["tile_bytes"])
    images = {lang: build_image(lang, table, rom)[0] for lang in BASELINE_LANGS}
    block = ["", "# Captured pre-split reference art for every owned tile.",
             "# scripts/generate_dialogue_accent_patch.py --capture-baseline",
             ""]
    for tile in owned_tiles(source):
        address = tile_address(source, tile)
        block.append(f"[baseline.tile_{tile:03x}]")
        for lang in BASELINE_LANGS:
            block.append(
                f'{lang} = "{images[lang][address:address + size].hex()}"')
        block.append("")
    text = source_path.read_text(encoding="utf-8")
    text = re.sub(r"\n# Captured pre-split reference art.*", "", text, flags=re.S)
    source_path.write_text(text.rstrip("\n") + "\n" + "\n".join(block),
                           encoding="utf-8", newline="\n")


def check_baseline(source: dict, table: dict, rom: bytes) -> None:
    size = int(source["tile_bytes"])
    baseline = source.get("baseline", {})
    for lang in BASELINE_LANGS:
        image = build_image(lang, table, rom)[0]
        for tile in owned_tiles(source):
            address = tile_address(source, tile)
            want = baseline[f"tile_{tile:03x}"][lang]
            got = image[address:address + size].hex()
            if want != got:
                raise GenError(
                    f"{lang} image changed at dialogue font tile 0x{tile:03x}: "
                    f"expected {want}, replay produced {got}")
    print("baseline invariant ok: en + es font art byte-identical to pre-split")


def check_no_overlap(table: dict, source: dict) -> None:
    rects = owned_rects(source)
    generated = {tile_address(source, tile) for tile in owned_tiles(source)}
    for patch in table.get("rom_patch", []):
        address = int(patch["address"])
        width = len(bytes.fromhex(patch["source_hex"]))
        if address in generated and width == int(source["tile_bytes"]):
            continue
        for start, length in rects:
            if address < start + length and address + width > start:
                raise GenError(
                    f"rom_patch 0x{address:06x}+{width} still overlaps the "
                    f"accent cell at 0x{start:06x}")
    print("non-overlap invariant ok: no fragment intersects an accent cell")


# ---------------------------------------------------------------------------
# previews
# ---------------------------------------------------------------------------

PALETTE = ((248, 248, 248), (40, 40, 56), (120, 128, 168), (248, 208, 96))


def write_previews(root: Path, source: dict, en_image: bytes,
                   out_dir: Path, scale: int = 8) -> list[Path]:
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed; skipping accent glyph previews")
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    by_lang: dict[str, list[dict]] = {}
    for cell in source.get("cell", []):
        by_lang.setdefault(str(cell["lang"]), []).append(cell)
    for lang, cells in sorted(by_lang.items()):
        width = 8 * len(cells)
        image = Image.new("RGB", (width * scale, 16 * scale), PALETTE[0])
        pixels = image.load()
        for index, cell in enumerate(cells):
            top_art, bottom_art = compose_cell(source, cell, en_image)
            rows = tile_to_pixels(top_art) + tile_to_pixels(bottom_art)
            for y, row in enumerate(rows):
                for x, value in enumerate(row):
                    colour = PALETTE[value]
                    for dy in range(scale):
                        for dx in range(scale):
                            pixels[(index * 8 + x) * scale + dx,
                                   y * scale + dy] = colour
        path = out_dir / f"dialogue_accents_{lang}.png"
        image.save(path)
        written.append(path)
        chars = " ".join(
            f"{c['char']}@0x{int(c['tile']):03x}" for c in cells)
        print(f"  {path.name}: {chars}")
    return written


# ---------------------------------------------------------------------------

def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=str(root / "translations" / "endless_duel_dialogue_accents.toml"))
    parser.add_argument("--table", default=str(default_table_path()))
    parser.add_argument("--rom", default=str(default_rom_path()))
    parser.add_argument("--preview-dir",
                        default=str(root / "translations" / "dialogue_previews"))
    parser.add_argument("--capture-baseline", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--previews", action="store_true")
    args = parser.parse_args()

    source_path = Path(args.source)
    table_path = Path(args.table)
    rom = Path(args.rom).read_bytes()
    source = load_source(source_path)
    table = load_table(table_path)

    if args.capture_baseline:
        capture_baseline(source_path, source, table, rom)
        print(f"captured baseline art into {source_path}")
        return 0

    en_image = build_image("en", table, rom)[0]
    section = generate_section(source, rom, en_image)
    text = table_path.read_text(encoding="utf-8")

    if args.write:
        split_text, split_count, removed_count = split_rom_patch_blocks(
            strip_section(text), owned_rects(source))
        updated = replace_section(split_text, section)
        table_path.write_text(updated, encoding="utf-8", newline="\n")
        new_table = load_table(table_path)
        check_baseline(source, new_table, rom)
        check_no_overlap(new_table, source)
        print(f"fragments split: {split_count}, removed: {removed_count}")
        print(f"updated {table_path}")
        if args.previews:
            write_previews(root, source, en_image, Path(args.preview_dir))
        return 0

    if args.previews:
        write_previews(root, source, en_image, Path(args.preview_dir))
        return 0

    updated = replace_section(text, section)
    status = 0
    if updated != text:
        print("dialogue accent glyph section is not up to date")
        status = 1
    else:
        print("dialogue accent glyph section is up to date")
    check_baseline(source, table, rom)
    check_no_overlap(table, source)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
