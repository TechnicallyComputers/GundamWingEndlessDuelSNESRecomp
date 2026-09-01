#!/usr/bin/env python3
"""Generate native CJK option/key-config text by injecting font slots.

The option and key-config screens are drawn from a display list in ROM at
0x00c000-0x00c800. Opcode 0x0002 prints font codes until a 0xff terminator,
so the text itself is plain fixed-width bytes in ROM and can be replaced with
[[rom_patch]] entries -- but the stock font only defines ASCII.

Font facts (verified 2026-08-31 against live VRAM captures):
- BG2, Mode 0, 2bpp, 16 bytes per tile, char base 0x0000.
- A character code c in 0x20-0x9f draws an 8x16 cell from two stacked tiles:
  top = 2 * (c & 0xf0) - 0x40 + (c & 0x0f), bottom = top + 0x10.
- Codes 0x60-0x9f are FREE: their tiles 0x80-0xff (VRAM 0x0800-0x0fff) are
  all-zero on these screens and no record references them.
- The font is generated/compressed rather than stored raw in ROM, so glyph art
  ships as [[vram_patch]] entries guarded on the all-zero source. That guard is
  genuine: the same VRAM region is non-zero on the title screen, so the patches
  no-op everywhere else.

This generator allocates one free code per distinct CJK character per language,
packs the 8x8 masks from endless_duel_title_glyphs.toml vertically centred into
the 8x16 cell (mask rows 0-3 into top-tile rows 4-7, rows 4-7 into bottom-tile
rows 0-3, plane0 = plane1 so the pixels land on colour index 3), and rewrites
each record's bytes.

Sources:
- translations/endless_duel_option_text.toml    record text per language
- translations/endless_duel_title_glyphs.toml   8x8 glyph masks

Usage:
  python scripts/generate_option_cjk_patch.py --check
  python scripts/generate_option_cjk_patch.py --write
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

LANGS = ("ko", "zh", "th")

# Thai is not one-codepoint-per-cell, so a font code is allocated per
# orthographic CLUSTER instead of per codepoint: a pre-base vowel (เ แ โ ใ ไ) is
# its own advancing cell, and a base consonant carries its own above and below
# marks inside its 8x16 cell.  Line-as-image (what the dialogue surface uses)
# was considered and rejected here: ~224 columns across 28 records overruns the
# 64 free font codes roughly three-fold, while cluster-per-code needs ~30.
THAI_ABOVE = frozenset(
    [0x0E31] + list(range(0x0E34, 0x0E38)) + list(range(0x0E47, 0x0E4F)))
THAI_BELOW = frozenset(range(0x0E38, 0x0E3B))
THAI_COMBINING = THAI_ABOVE | THAI_BELOW
THAI_RANGE = range(0x0E00, 0x0E80)


def thai_clusters(text: str) -> list[str]:
    """Split into cells: a combining mark joins the cell before it."""
    cells: list[str] = []
    for char in text:
        if (ord(char) in THAI_COMBINING and cells
                and ord(cells[-1][0]) in THAI_RANGE):
            cells[-1] += char
            continue
        cells.append(char)
    return cells


def cells_of(text: str) -> list[str]:
    """One entry per font cell the record needs, for any script."""
    return thai_clusters(text)

BEGIN_MARK = "# BEGIN GENERATED OPTION CJK PATCHES"
END_MARK = "# END GENERATED OPTION CJK PATCHES"

CURSOR_BYTE = 0x09
PAD_BYTE = 0x20
FREE_CODE_LO = 0x60
FREE_CODE_HI = 0x9F
TILE_BYTES = 16
GLYPH_ROWS = 8
# The cell is 8x16, but the top and bottom rows are the label pitch's breathing
# room, so a mask may be up to 8x14.  ko/zh ship 8x8 masks from the authored
# atlas; Thai needs the extra rows for its mark stacks.
MAX_MASK_ROWS = 14
CELL_ROWS = 16
# Thai cluster cells are rendered through GDI (see scripts/gdi_text.py) rather
# than hand-authored into endless_duel_title_glyphs.toml: the atlas is keyed by
# single codepoint and cannot express a cluster, and ~30 hand-authored 8x14
# masks would be ~420 hand-entered hex values with no way to review them except
# the preview this generator already emits.  Generation-time dependency only.
# 16px em band-compresses to exactly 14 rows (3 above + 9 base + 2 below), so
# the base consonants keep 9 rows -- 13px em leaves them 8 and the loops close
# up.  Threshold 100 was picked by rendering the whole cluster set at 72 / 100 /
# 110: 72 over-inks the 2px strokes into blobs, 110 drops the thin tone marks.
THAI_FONT_FILE = "LeelawUI.ttf"
THAI_FONT_SIZE = 16
THAI_MASK_THRESHOLD = 100

ROM_NAME = "Shin Kidou Senki Gundam W - Endless Duel (J).smc"
RECORD_TERMINATOR = 0xFF


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


def load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def top_tile(code: int) -> int:
    """Top tile index of the 8x16 cell drawn for font code `code`."""
    return 2 * (code & 0xF0) - 0x40 + (code & 0x0F)


def load_masks(glyph_path: Path) -> dict[str, list[int]]:
    """codepoint -> 8 row bitmasks, from the shared authored glyph atlas."""
    masks: dict[str, list[int]] = {}
    for entry in load_toml(glyph_path).get("glyph", []):
        if "codepoint" not in entry:
            continue
        text = str(entry["codepoint"])
        if text.upper().startswith("U+"):
            text = text[2:]
        char = chr(int(text, 16))
        width = int(entry["width"])
        height = int(entry.get("height", GLYPH_ROWS))
        if width != GLYPH_ROWS or not 1 <= height <= MAX_MASK_ROWS:
            raise ValueError(
                f"glyph U+{ord(char):04X}: option font needs a {GLYPH_ROWS}px "
                f"wide mask of at most {MAX_MASK_ROWS} rows, got "
                f"{width}x{height}")
        rows = [int(str(entry[f"row{i}"]), 16) for i in range(height)]
        for index, row in enumerate(rows):
            if not 0 <= row <= 0xFF:
                raise ValueError(
                    f"glyph U+{ord(char):04X}: row{index} out of 8-bit range")
        masks[char] = rows
    return masks


def load_records(path: Path) -> list[dict]:
    data = load_toml(path)
    records = data.get("record", [])
    if not records:
        raise ValueError(f"{path}: no [[record]] entries")
    seen: set[int] = set()
    for record in records:
        address = int(record["address"])
        if address in seen:
            raise ValueError(f"duplicate record address {address:#08x}")
        seen.add(address)
    return sorted(records, key=lambda r: int(r["address"]))


def record_span(record: dict) -> int:
    return len(str(record["source"])) + (1 if record.get("cursor") else 0)


def allocate(records: list[dict], lang: str,
             masks: dict[str, list[int]]) -> dict[str, int]:
    """cell -> font code, assigned deterministically over the sorted cell set."""
    chars = sorted({
        cell
        for record in records
        for cell in cells_of(str(record[lang]))
        if ord(cell[0]) > 0x7F
    })
    missing = [c for c in chars if c not in masks]
    if missing:
        raise ValueError(
            f"{lang}: no glyph mask for " +
            ", ".join("+".join(f"U+{ord(ch):04X}" for ch in c)
                      for c in missing))
    budget = FREE_CODE_HI - FREE_CODE_LO + 1
    if len(chars) > budget:
        raise ValueError(
            f"{lang}: {len(chars)} distinct characters exceed the "
            f"{budget} free font codes 0x{FREE_CODE_LO:02x}-0x{FREE_CODE_HI:02x}")
    return {char: FREE_CODE_LO + index for index, char in enumerate(chars)}


def encode(record: dict, lang: str, codes: dict[str, int]) -> bytes:
    span = record_span(record)
    out = bytearray()
    if record.get("cursor"):
        out.append(CURSOR_BYTE)
    for char in cells_of(str(record[lang])):
        if ord(char[0]) > 0x7F:
            out.append(codes[char])
            continue
        value = ord(char)
        if not 0x20 <= value <= 0x5F:
            raise ValueError(
                f"{record['record_id']} {lang}: {char!r} is outside the stock "
                "font's ASCII range 0x20-0x5f")
        out.append(value)
    if len(out) > span:
        raise ValueError(
            f"{record['record_id']} {lang}: {len(out)} bytes exceed the "
            f"{span}-byte record span")
    out.extend([PAD_BYTE] * (span - len(out)))
    return bytes(out)


def source_bytes(record: dict) -> bytes:
    out = bytearray()
    if record.get("cursor"):
        out.append(CURSOR_BYTE)
    out.extend(str(record["source"]).encode("ascii"))
    return bytes(out)


def verify_against_rom(root: Path, records: list[dict]) -> bool:
    rom_path = root / ROM_NAME
    if not rom_path.is_file():
        return False
    rom = rom_path.read_bytes()
    errors: list[str] = []
    for record in records:
        address = int(record["address"])
        want = source_bytes(record)
        end = address + len(want)
        if end >= len(rom):
            errors.append(f"{record['record_id']}: {address:#08x} past end of ROM")
            continue
        have = rom[address:end]
        if have != want:
            errors.append(
                f"{record['record_id']} at {address:#08x}: ROM has {have.hex()}, "
                f"source says {want.hex()}")
        elif rom[end] != RECORD_TERMINATOR:
            errors.append(
                f"{record['record_id']} at {address:#08x}: record does not end at "
                f"0x{RECORD_TERMINATOR:02x} (found 0x{rom[end]:02x})")
    if errors:
        raise ValueError("\n".join(errors))
    return True


def pack_cell(mask: list[int]) -> tuple[bytes, bytes]:
    """8xN mask (N <= 14) -> (top tile, bottom tile) 2bpp bytes, centred.

    The mask is centred in the 16-row cell -- an 8-row mask lands in rows 4-11,
    exactly where the ko/zh cells have always sat, and a 14-row Thai cluster
    lands in rows 1-14.  Both bitplanes carry the mask so lit pixels use palette
    colour index 3.
    """
    if not 1 <= len(mask) <= MAX_MASK_ROWS:
        raise ValueError(f"mask is {len(mask)} rows, at most {MAX_MASK_ROWS}")
    tiles = (bytearray(TILE_BYTES), bytearray(TILE_BYTES))
    offset = (CELL_ROWS - len(mask)) // 2
    for index, row in enumerate(mask):
        y = offset + index
        tile = tiles[y // 8]
        tile[(y % 8) * 2] = row
        tile[(y % 8) * 2 + 1] = row
    return bytes(tiles[0]), bytes(tiles[1])


_THAI_CELL: list = []


def thai_masks(clusters) -> dict[str, list[int]]:
    """Render each Thai cluster into an 8x14 cell mask, in one GDI batch."""
    from gdi_text import BandGeometry, GdiRenderer, cluster_mask
    if not _THAI_CELL:
        renderer = GdiRenderer(THAI_FONT_FILE, THAI_FONT_SIZE)
        _THAI_CELL.append(
            (renderer, BandGeometry.measure(renderer, MAX_MASK_ROWS)))
    renderer, geometry = _THAI_CELL[0]
    wanted = sorted(set(clusters))
    renderer.render(wanted)
    return {cluster: cluster_mask(renderer, geometry, cluster, GLYPH_ROWS,
                                  THAI_MASK_THRESHOLD)
            for cluster in wanted}


def generate_section(root: Path) -> str:
    records = load_records(
        root / "translations" / "endless_duel_option_text.toml")
    masks = load_masks(
        root / "translations" / "endless_duel_title_glyphs.toml")
    rom_verified = verify_against_rom(root, records)

    # Thai cluster cells are rendered, not authored; merge them into the mask
    # table before allocation so the "no glyph mask" gate still covers them.
    thai_cells = sorted({
        cell
        for record in records
        for cell in cells_of(str(record["th"]))
        if ord(cell[0]) > 0x7F})
    if thai_cells:
        masks = dict(masks)
        masks.update(thai_masks(thai_cells))

    allocations = {lang: allocate(records, lang, masks) for lang in LANGS}

    # address -> {lang: 16 bytes}; the same tile serves both languages with
    # different glyphs whenever their allocations collide on a code.
    tiles: dict[int, dict[str, bytes]] = {}
    for lang, codes in allocations.items():
        for char, code in codes.items():
            top, bottom = pack_cell(masks[char])
            index = top_tile(code)
            for tile, data in ((index, top), (index + 0x10, bottom)):
                tiles.setdefault(tile * TILE_BYTES, {})[lang] = data

    lines = [
        BEGIN_MARK,
        "# Native CJK option / key-config text via font-slot injection.",
        "# Generated by scripts/generate_option_cjk_patch.py from",
        "# endless_duel_option_text.toml and the glyph masks in",
        "# endless_duel_title_glyphs.toml. Do not hand-edit.",
        "#",
        "# Free font codes 0x60-0x9f are allocated per language over the sorted",
        "# distinct characters; code c draws tiles 2*(c&0xf0)-0x40+(c&0x0f) and",
        "# +0x10, i.e. VRAM 0x0800-0x0fff, which is all-zero on these screens.",
        "#",
        f"# ROM source bytes verified against {ROM_NAME}: "
        f"{'yes' if rom_verified else 'ROM absent, not verified'}",
        "#",
    ]
    width = max(6, max(len(cell) for codes in allocations.values()
                       for cell in codes) * 7)
    lines.append("# code | " + " | ".join(f"{lang:^{width}}" for lang in LANGS))
    highest = max(FREE_CODE_LO + len(codes) - 1
                  for codes in allocations.values())
    for code in range(FREE_CODE_LO, highest + 1):
        cells = []
        for lang in LANGS:
            char = next((c for c, v in allocations[lang].items() if v == code),
                        None)
            label = "+".join(f"U+{ord(ch):04X}" for ch in char) if char else "-"
            cells.append(f"{label:^{width}}")
        lines.append(f"# 0x{code:02x} | " + " | ".join(cells))
    lines.append("")

    for record in records:
        address = int(record["address"])
        span = record_span(record)
        lines.append("[[rom_patch]]")
        lines.append(f"# option text: {record['record_id']}")
        lines.append(f"address = 0x{address:06x}")
        lines.append(f'source_hex = "{source_bytes(record).hex()}"')
        for lang in LANGS:
            payload = encode(record, lang, allocations[lang])
            if len(payload) != span:
                raise ValueError(f"{record['record_id']} {lang}: span mismatch")
            lines.append(f'{lang}_hex = "{payload.hex()}"')
        lines.append("")

    for address in sorted(tiles):
        lines.append("[[vram_patch]]")
        lines.append(f"# option font tile 0x{address // TILE_BYTES:02x}")
        lines.append(f"address = 0x{address:04x}")
        lines.append(f'source_hex = "{"00" * TILE_BYTES}"')
        for lang in LANGS:
            if lang in tiles[address]:
                lines.append(f'{lang}_hex = "{tiles[address][lang].hex()}"')
        lines.append("")

    lines.append(END_MARK)
    return "\n".join(line.rstrip() for line in lines)


def write_previews(root: Path, out_dir: Path) -> list[Path]:
    """One PNG per language, every record replayed through its own font cells.

    Painted from the SAME bytes the generated section emits: the record's font
    codes indexed into the 8x16 cells built from the allocated masks, so what the
    preview shows is what the screen will show.
    """
    from PIL import Image, ImageDraw
    records = load_records(root / "translations" / "endless_duel_option_text.toml")
    masks = load_masks(root / "translations" / "endless_duel_title_glyphs.toml")
    thai_cells = sorted({cell for record in records
                         for cell in cells_of(str(record["th"]))
                         if ord(cell[0]) > 0x7F})
    if thai_cells:
        masks = dict(masks)
        masks.update(thai_masks(thai_cells))
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for lang in LANGS:
        codes = allocate(records, lang, masks)
        cells = {code: pack_cell(masks[cell]) for cell, code in codes.items()}
        rows = 18
        image = Image.new("RGB", (16 * 8 + 140, len(records) * rows + 8),
                          (0, 0, 0))
        draw = ImageDraw.Draw(image)
        for index, record in enumerate(records):
            y = 4 + index * rows
            draw.text((4, y + 3), record["record_id"][:18], fill=(120, 170, 220))
            payload = encode(record, lang, codes)
            for column, code in enumerate(payload):
                if code not in cells:
                    continue
                top, bottom = cells[code]
                for half, data in ((0, top), (1, bottom)):
                    for row in range(8):
                        bits = data[row * 2]
                        for bit in range(8):
                            if (bits >> (7 - bit)) & 1:
                                image.putpixel((132 + column * 8 + bit,
                                                y + half * 8 + row),
                                               (255, 255, 255))
        path = out_dir / f"option_{lang}.png"
        image.resize((image.width * 3, image.height * 3),
                     Image.NEAREST).save(path)
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true",
                        help="rewrite the generated section in the table")
    parser.add_argument("--check", action="store_true",
                        help="fail if the generated section differs")
    parser.add_argument("--previews", action="store_true",
                        help="render one review PNG per language")
    args = parser.parse_args()

    root = repo_root()
    if args.previews:
        for path in write_previews(
                root, root / "translations" / "option_cjk_previews"):
            print(f"preview {path}")
    table_path = root / "translations" / "endless_duel.toml"
    section = generate_section(root)

    text = table_path.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(BEGIN_MARK) + r".*?" + re.escape(END_MARK), re.S)
    match = pattern.search(text)

    if args.write:
        if match:
            new_text = text[:match.start()] + section + text[match.end():]
        else:
            new_text = text.rstrip() + "\n\n" + section + "\n"
        if new_text != text:
            table_path.write_text(new_text, encoding="utf-8", newline="\n")
            print(f"updated {table_path}")
        else:
            print("table already up to date")
        return 0

    if not match:
        print("generated option CJK section missing from table", file=sys.stderr)
        return 1
    if match.group(0) != section:
        print("generated option CJK section differs from table", file=sys.stderr)
        return 1
    print(f"option CJK patches: {section.count('[[rom_patch]]')} records, "
          f"{section.count('[[vram_patch]]')} font tiles, "
          f"{len(LANGS)} languages ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
