#!/usr/bin/env python3
"""Generate language-gated VRAM tile-art patches for the title mode menu.

The title labels (STORY MODE / VS. MODE / TRIAL MODE / OPTION) are
pre-rendered, unique, consecutive 4bpp BG1 tiles whose ROM source is
compressed, so translation intercepts the uploaded tile art with guarded
[[vram_patch]] entries instead of drawing over the presented frame.

Sources:
- translations/endless_duel_title_menu.toml        label strings per language
- translations/endless_duel_title_glyphs.toml      authored glyph overrides
- translations/endless_duel_title_menu_assets.toml original captured tile art

Geometry facts (verified 2026-08-31 against live VRAM captures):
- BG1 map base 0xd000, char base 0x0000, Mode 1, all label map words
  palette 6 / priority 1.
- Labels sit on a 12px pitch with 8px glyphs inside tile rows 17-22,
  cols 10-21; adjacent labels share tiles, so art is authored as whole
  row-block canvases, never per label.
- Selection highlight is CGRAM-only (each label owns one color index:
  STORY=14, VS=13, TRIAL=12, OPTION=11), so one art set per language
  inherits selection behaviour.

Usage:
  python scripts/generate_title_menu_vram_patch.py --check
  python scripts/generate_title_menu_vram_patch.py --write
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

LANGS = ("es", "fr", "it", "pt", "zh", "ko")

BEGIN_MARK = "# BEGIN GENERATED TITLE MENU VRAM PATCHES"
END_MARK = "# END GENERATED TITLE MENU VRAM PATCHES"

# Native title letterforms, seeded from selected-row captures of the stock
# title menu. Mirrors the tables in src/title_menu_overlay.c; the authored
# overrides in endless_duel_title_glyphs.toml replace entries (H, C, CJK).
NATIVE_GLYPHS = {
    " ": (5, [0] * 8),
    ".": (2, [0, 0, 0, 0, 0, 0, 0x3, 0x3]),
    "0": (8, [0x7e, 0xff, 0xc3, 0xc3, 0xc3, 0xc3, 0xff, 0x7e]),
    "1": (5, [0x0c, 0x1c, 0x3c, 0x0c, 0x0c, 0x0c, 0x3f, 0x3f]),
    "2": (8, [0x7e, 0xff, 0x03, 0x0e, 0x38, 0xe0, 0xff, 0xff]),
    "3": (8, [0xfe, 0xff, 0x03, 0x3e, 0x3f, 0x03, 0xff, 0xfe]),
    "4": (8, [0xc6, 0xc6, 0xc6, 0xff, 0xff, 0x06, 0x06, 0x06]),
    "5": (8, [0xff, 0xff, 0xc0, 0xfe, 0x7f, 0x03, 0xff, 0xfe]),
    "6": (8, [0x7e, 0xff, 0xc0, 0xfe, 0xff, 0xc3, 0xff, 0x7e]),
    "7": (8, [0xff, 0xff, 0x06, 0x0c, 0x18, 0x30, 0x30, 0x30]),
    "8": (8, [0x7e, 0xff, 0xc3, 0x7e, 0xff, 0xc3, 0xff, 0x7e]),
    "9": (8, [0x7e, 0xff, 0xc3, 0xff, 0x7f, 0x03, 0xff, 0x7e]),
    "A": (8, [0x7e, 0xff, 0xc3, 0xdf, 0xdf, 0xc3, 0xc3, 0xc3]),
    "B": (8, [0xfe, 0xff, 0xc3, 0xfe, 0xff, 0xc3, 0xff, 0xfe]),
    "C": (8, [0x7e, 0xff, 0xc0, 0xc0, 0xc0, 0xc0, 0xff, 0x7e]),
    "D": (8, [0xfe, 0xff, 0xc3, 0xc3, 0xc3, 0xc3, 0xdf, 0xde]),
    "E": (8, [0x7f, 0xff, 0xe0, 0xff, 0xff, 0xe0, 0xff, 0xff]),
    "F": (8, [0x7f, 0xff, 0xe0, 0xff, 0xff, 0xe0, 0xe0, 0xe0]),
    "G": (8, [0x7f, 0xff, 0xc0, 0xc0, 0xcf, 0xc3, 0xff, 0x7f]),
    "H": (8, [0xc3, 0xc3, 0xc3, 0xff, 0xff, 0xc3, 0xc3, 0xc3]),
    "I": (3, [0x7, 0x7, 0x7, 0x7, 0x7, 0x7, 0x7, 0x7]),
    "J": (8, [0x1f, 0x1f, 0x06, 0x06, 0xc6, 0xc6, 0xfe, 0x7c]),
    "K": (8, [0xc3, 0xc6, 0xcc, 0xf8, 0xfc, 0xce, 0xc7, 0xc3]),
    "L": (7, [0x70, 0x70, 0x70, 0x70, 0x70, 0x70, 0x7f, 0x7f]),
    "M": (8, [0xfe, 0xff, 0xdb, 0xdb, 0xdb, 0xdb, 0xdb, 0xdb]),
    "N": (8, [0xfe, 0xff, 0xc3, 0xc3, 0xc3, 0xc3, 0xc3, 0xc3]),
    "O": (8, [0x7e, 0xff, 0xc3, 0xc3, 0xc3, 0xc3, 0xff, 0x7e]),
    "P": (8, [0xfe, 0xff, 0xc3, 0xdf, 0xde, 0xc0, 0xc0, 0xc0]),
    "Q": (8, [0x7e, 0xff, 0xc3, 0xc3, 0xdb, 0xcf, 0xff, 0x7b]),
    "R": (8, [0xfe, 0xff, 0xc3, 0xdf, 0xde, 0xc3, 0xc3, 0xc3]),
    "S": (8, [0x7f, 0xff, 0xc0, 0xfe, 0x7f, 0x03, 0xff, 0xfe]),
    "T": (7, [0x7f, 0x7f, 0x1c, 0x1c, 0x1c, 0x1c, 0x1c, 0x1c]),
    "U": (8, [0xc3, 0xc3, 0xc3, 0xc3, 0xc3, 0xc3, 0xff, 0x7e]),
    "V": (10, [0x303, 0x303, 0x387, 0x1ce, 0xcc, 0xfc, 0x78, 0x30]),
    "W": (8, [0xdb, 0xdb, 0xdb, 0xdb, 0xdb, 0xdb, 0xff, 0x66]),
    "X": (8, [0xc3, 0xe7, 0x7e, 0x3c, 0x3c, 0x7e, 0xe7, 0xc3]),
    "Y": (8, [0xc3, 0xc3, 0xc3, 0xc3, 0xff, 0x7e, 0x18, 0x18]),
    "Z": (8, [0xff, 0xff, 0x06, 0x0c, 0x18, 0x30, 0xff, 0xff]),
}

# label id -> (color index, absolute glyph top y, usable col range inclusive)
LABEL_LAYOUT = {
    "story_mode": (14, 140, (10, 21)),
    "vs_mode": (13, 151, (12, 19)),
    "trial_mode": (12, 162, (11, 20)),
    "option": (11, 173, (11, 20)),
}
MAX_GLYPH_HEIGHT = 11  # 12px label pitch; taller art would collide


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


def load_glyphs(glyph_path: Path) -> dict[str, tuple[int, int, list[int]]]:
    """char -> (width, height, row bitmasks); native table plus toml overrides."""
    glyphs = {
        ch: (w, 8, rows) for ch, (w, rows) in NATIVE_GLYPHS.items()
    }
    for ch in list("abcdefghijklmnopqrstuvwxyz"):
        upper = ch.upper()
        if upper in glyphs:
            glyphs[ch] = glyphs[upper]
    with glyph_path.open("rb") as f:
        data = tomllib.load(f)
    for entry in data.get("glyph", []):
        if "char" in entry:
            ch = str(entry["char"])
        elif "codepoint" in entry:
            cp = str(entry["codepoint"])
            if cp.upper().startswith("U+"):
                cp = cp[2:]
            ch = chr(int(cp, 16))
        else:
            continue
        width = int(entry["width"])
        height = int(entry.get("height", 8))
        rows = []
        for i in range(16):
            key = f"row{i}"
            if key in entry:
                rows.append(int(str(entry[key]), 16))
        rows = rows[:height] + [0] * max(0, height - len(rows))
        if not 1 <= width <= 16 or not 1 <= height <= 16:
            raise ValueError(f"glyph {ch!r}: bad dimensions {width}x{height}")
        glyphs[ch] = (width, height, rows)
        if len(ch) == 1 and ch.isascii() and ch.isalpha():
            glyphs[ch.lower()] = glyphs[ch]
            glyphs[ch.upper()] = glyphs[ch]
    return glyphs


def text_width(text: str, glyphs: dict) -> int:
    width = 0
    for i, ch in enumerate(text):
        if ch not in glyphs:
            raise ValueError(f"unsupported title glyph {ch!r}")
        if i:
            width += 1
        width += glyphs[ch][0]
    return width


def paint_label(canvas: list[list[int]], canvas_x0: int, canvas_y0: int,
                label_id: str, text: str, glyphs: dict) -> None:
    color, y_abs, (c0, c1) = LABEL_LAYOUT[label_id]
    x_lo = c0 * 8
    x_hi = (c1 + 1) * 8
    tw = text_width(text, glyphs)
    if tw > x_hi - x_lo:
        raise ValueError(
            f"{label_id}: {text!r} is {tw}px wide, exceeds the "
            f"{x_hi - x_lo}px tile run (cols {c0}-{c1})")
    th = max(glyphs[ch][1] for ch in text)
    if th > MAX_GLYPH_HEIGHT:
        raise ValueError(f"{label_id}: glyph height {th} exceeds "
                         f"{MAX_GLYPH_HEIGHT}px label pitch budget")
    cursor = x_lo + (x_hi - x_lo - tw) // 2
    for ch in text:
        width, height, rows = glyphs[ch]
        for gy in range(height):
            row_bits = rows[gy] if gy < len(rows) else 0
            for gx in range(width):
                if not row_bits & (1 << (width - 1 - gx)):
                    continue
                px = cursor + gx - canvas_x0
                py = y_abs + gy - canvas_y0
                if py < 0 or py >= len(canvas) or px < 0 or \
                        px >= len(canvas[0]):
                    raise ValueError(
                        f"{label_id}: pixel ({px},{py}) outside canvas")
                canvas[py][px] = color
        cursor += width + 1


def pack_tile_4bpp(pixels: list[list[int]]) -> bytes:
    out = bytearray(32)
    for y in range(8):
        b0 = b1 = b2 = b3 = 0
        for x in range(8):
            v = pixels[y][x]
            bit = 1 << (7 - x)
            if v & 1:
                b0 |= bit
            if v & 2:
                b1 |= bit
            if v & 4:
                b2 |= bit
            if v & 8:
                b3 |= bit
        out[2 * y] = b0
        out[2 * y + 1] = b1
        out[16 + 2 * y] = b2
        out[16 + 2 * y + 1] = b3
    return bytes(out)


def render_language(labels: dict[str, str], glyphs: dict,
                    assets: dict) -> dict[int, bytes]:
    row0 = int(assets["canvas_row0"])
    col0 = int(assets["canvas_col0"])
    rows = int(assets["canvas_rows"])
    cols = int(assets["canvas_cols"])
    canvas = [[0] * (cols * 8) for _ in range(rows * 8)]
    for label_id, text in labels.items():
        paint_label(canvas, col0 * 8, row0 * 8, label_id, text, glyphs)

    mapped = set()
    for run in assets["tile_run"]:
        for col in range(int(run["col_start"]), int(run["col_end"]) + 1):
            mapped.add((int(run["row"]), col))
    for py, line in enumerate(canvas):
        for px, v in enumerate(line):
            if v and (row0 + py // 8, col0 + px // 8) not in mapped:
                raise ValueError(
                    f"pixel at canvas ({px},{py}) lands in unmapped tile "
                    f"({row0 + py // 8},{col0 + px // 8})")

    art: dict[int, bytes] = {}
    for run in assets["tile_run"]:
        row = int(run["row"])
        blob = bytearray()
        for col in range(int(run["col_start"]), int(run["col_end"]) + 1):
            ty = (row - row0) * 8
            tx = (col - col0) * 8
            tile = [line[tx:tx + 8] for line in canvas[ty:ty + 8]]
            blob += pack_tile_4bpp(tile)
        address = int(run["vram_address"])
        source = bytes.fromhex(run["source_hex"])
        if len(blob) != len(source):
            raise ValueError(
                f"run at {address:#06x}: generated {len(blob)} bytes, "
                f"source is {len(source)}")
        art[address] = bytes(blob)
    return art


def generate_section(root: Path) -> str:
    with (root / "translations" / "endless_duel_title_menu.toml").open("rb") as f:
        menu = tomllib.load(f)
    with (root / "translations" /
          "endless_duel_title_menu_assets.toml").open("rb") as f:
        assets = tomllib.load(f)
    glyphs = load_glyphs(
        root / "translations" / "endless_duel_title_glyphs.toml")

    per_lang: dict[str, dict[int, bytes]] = {}
    for lang in LANGS:
        labels = {}
        for label in menu["label"]:
            label_id = str(label["id"])
            if label_id not in LABEL_LAYOUT:
                raise ValueError(f"unknown label id {label_id!r}")
            text = label.get(lang)
            if not text:
                raise ValueError(f"{lang}: missing translation for {label_id}")
            labels[label_id] = str(text)
        per_lang[lang] = render_language(labels, glyphs, assets)

    lines = [
        BEGIN_MARK,
        "# Source-guarded tile-art interception for the title mode-menu",
        "# labels. Generated by scripts/generate_title_menu_vram_patch.py",
        "# from endless_duel_title_menu.toml strings and the captured stock",
        "# art in endless_duel_title_menu_assets.toml. Do not hand-edit.",
        "",
    ]
    for run in assets["tile_run"]:
        address = int(run["vram_address"])
        lines.append("[[vram_patch]]")
        lines.append(f"address = 0x{address:04x}")
        lines.append(f'source_hex = "{run["source_hex"]}"')
        for lang in LANGS:
            lines.append(f'{lang}_hex = "{per_lang[lang][address].hex()}"')
        lines.append("")
    lines.append(END_MARK)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true",
                        help="rewrite the generated section in the table")
    parser.add_argument("--check", action="store_true",
                        help="fail if the generated section differs")
    args = parser.parse_args()

    root = repo_root()
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
        print("generated title menu section missing from table",
              file=sys.stderr)
        return 1
    if match.group(0) != section:
        print("generated title menu section differs from table",
              file=sys.stderr)
        return 1
    counts = section.count("[[vram_patch]]")
    print(f"title menu vram patches: {counts} runs x {len(LANGS)} languages ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
