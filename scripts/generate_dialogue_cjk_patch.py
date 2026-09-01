#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the CJK dialogue glyph pages for the victory/defeat quote screen.

The quote screen is encoded BG tilemap text (playbook section 3a), but unlike
the Latin languages it cannot be translated by rewriting tilemap words alone:
the font installed at BG3 char base 0x4000 holds 128 Latin cells and nothing
else, and Korean/Chinese need thousands of glyphs.

Mechanism: per-quote glyph paging.

  * The 256 tile ids 0x300-0x3ff (VRAM 0x7000-0x7fff) are unreferenced by every
    layer on this screen and byte-stable across every recorded capture, so they
    are a free 4 KiB scratch page.
  * Each quote (its one or two ROM rows) gets its OWN page laid out from a
    per-quote base tile id.  Pages deliberately OVERLAP in address space --
    only one quote is ever on screen, so only one page needs to be resident.
  * The quote's ROM rows are rewritten (rom_patch, emitted by
    generate_dialogue_patch.py which imports row_payloads() from here) to
    reference that page's tiles.
  * The page art ships as a guarded vram_patch whose guard sits on the BG3 MAP
    words the draw routine writes for that quote's first row.  The guard is the
    interception point: the page lands the instant the game starts revealing
    this specific quote, and can never land on any other screen.

Guards are proved unique, per language, against every row the language will
actually render (CJK rows here plus the Latin rows every other surface keeps).
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__.replace("\\", "/")).resolve().parent))

from decode_reference_tilemaps import CHAR_BY_TILE

BEGIN_MARKER = "# BEGIN GENERATED DIALOGUE CJK PAGE PATCHES"
END_MARKER = "# END GENERATED DIALOGUE CJK PAGE PATCHES"
LANGS = ("ko", "zh")
TILE_BY_CHAR = {char: tile for tile, char in CHAR_BY_TILE.items()}
ROW_WORDS = 32
ROW_BYTES = ROW_WORDS * 2
FONT_DIR = Path("C:/Windows/Fonts")
# Palette 0 on this surface is 0 transparent, 1 black (box fill), 2 grey,
# 3 white.  The stock Latin cells draw white bodies with grey shading on the
# black field, so CJK cells use the same three indices.
PIX_BG, PIX_EDGE, PIX_BODY = 1, 2, 3
MAX_GLYPH_W, MAX_GLYPH_H = 16, 14


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


def load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def word_bytes(word: int) -> bytes:
    return bytes((word & 0xFF, (word >> 8) & 0xFF))


def is_full_width(char: str) -> bool:
    return ord(char) >= 128


def text_cells(text: str) -> int:
    return sum(2 if is_full_width(c) else 1 for c in text)


# --------------------------------------------------------------------------
# glyph rendering
# --------------------------------------------------------------------------

_FONT_CACHE: dict[tuple[str, int], object] = {}
_GLYPH_CACHE: dict[tuple[str, str], list[list[int]]] = {}


def _font(spec: dict):
    key = (spec["file"], int(spec["size"]))
    if key not in _FONT_CACHE:
        from PIL import ImageFont
        path = FONT_DIR / spec["file"]
        if not path.is_file():
            raise ValueError(f"font not installed: {path}")
        _FONT_CACHE[key] = ImageFont.truetype(str(path), int(spec["size"]))
    return _FONT_CACHE[key]


def render_cell(char: str, lang: str, spec: dict) -> list[list[int]]:
    """A 16x16 grid of 2bpp palette indices for one full-width character."""
    key = (lang, char)
    if key in _GLYPH_CACHE:
        return _GLYPH_CACHE[key]
    from PIL import Image, ImageDraw
    img = Image.new("L", (48, 48), 0)
    ImageDraw.Draw(img).text((24, 24), char, font=_font(spec), fill=255,
                             anchor="mm")
    box = img.getbbox()
    if box is None:
        raise ValueError(f"{lang}: font renders {char!r} as an empty cell")
    width, height = box[2] - box[0], box[3] - box[1]
    if width > MAX_GLYPH_W or height > MAX_GLYPH_H:
        raise ValueError(
            f"{lang}: {char!r} renders {width}x{height}, exceeds "
            f"{MAX_GLYPH_W}x{MAX_GLYPH_H} and would collide with the box frame"
        )
    cell = Image.new("L", (16, 16), 0)
    cell.paste(img.crop(box), ((16 - width) // 2, (16 - height) // 2))
    px = cell.load()
    grid = [[PIX_BG] * 16 for _ in range(16)]
    for y in range(16):
        for x in range(16):
            value = px[x, y]
            if value >= 128:
                grid[y][x] = PIX_BODY
            elif value >= 48:
                grid[y][x] = PIX_EDGE
    _GLYPH_CACHE[key] = grid
    return grid


def encode_2bpp(grid: list[list[int]], x0: int, y0: int) -> bytes:
    out = bytearray()
    for y in range(8):
        plane0 = plane1 = 0
        for x in range(8):
            value = grid[y0 + y][x0 + x]
            if value & 1:
                plane0 |= 1 << (7 - x)
            if value & 2:
                plane1 |= 1 << (7 - x)
        out += bytes((plane0, plane1))
    return bytes(out)


def quad_tiles(char: str, lang: str, spec: dict) -> tuple[bytes, ...]:
    """(top-left, top-right, bottom-left, bottom-right) 2bpp tiles."""
    grid = render_cell(char, lang, spec)
    return (encode_2bpp(grid, 0, 0), encode_2bpp(grid, 8, 0),
            encode_2bpp(grid, 0, 8), encode_2bpp(grid, 8, 8))


# --------------------------------------------------------------------------
# quote model
# --------------------------------------------------------------------------

class Quote:
    def __init__(self, index: int, addresses: list[int], texts: list[str],
                 lang: str):
        self.index = index
        self.addresses = addresses
        self.texts = texts
        self.lang = lang
        self.base = 0
        self.tiles: dict[str, int] = {}

    @property
    def chars(self) -> list[str]:
        seen: list[str] = []
        for text in self.texts:
            for char in text:
                if is_full_width(char) and char not in seen:
                    seen.append(char)
        return seen


def row_cells(text: str, quote: Quote) -> list[tuple[int, int]]:
    """(top word, bottom word) per map column, ROM-word encoded (0x0400|tile)."""
    cells: list[tuple[int, int]] = []
    for char in text:
        if is_full_width(char):
            base = quote.tiles[char]
            cells.append((0x0400 | base, 0x0400 | (base + 2)))
            cells.append((0x0400 | (base + 1), 0x0400 | (base + 3)))
            continue
        if char not in TILE_BY_CHAR:
            raise ValueError(
                f"{quote.lang} 0x{quote.addresses[0]:06x}: unsupported ASCII "
                f"glyph {char!r} (the dialogue font has no cell for it)"
            )
        tile = TILE_BY_CHAR[char]
        bottom = tile if char == " " else tile + 0x10
        cells.append((0x0400 | tile, 0x0400 | bottom))
    return cells


def encode_row(source_hex: str, start_col: int,
               cells: list[tuple[int, int]]) -> str:
    source = bytearray.fromhex(source_hex)
    if len(source) != ROW_BYTES * 2:
        raise ValueError(f"dialogue row must be 0x80 bytes, got {len(source):#x}")
    for col in range(start_col, ROW_WORDS):
        index = col - start_col
        top, bottom = cells[index] if index < len(cells) else (0x0800, 0x0800)
        source[col * 2:col * 2 + 2] = word_bytes(top)
        source[ROW_BYTES + col * 2:ROW_BYTES + col * 2 + 2] = word_bytes(bottom)
    return source.hex()


def vram_word(rom_word: int) -> int:
    """What the draw routine writes into the BG3 map for a ROM word."""
    return (rom_word & 0x03FF) | 0x2000


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

class Build:
    def __init__(self, root: Path, source_path: Path | None = None):
        self.root = root
        self.source = load_toml(
            source_path or root / "translations" / "endless_duel_dialogue_cjk.toml")
        self.dialogue = load_toml(
            root / "translations" / "endless_duel_dialogue.toml")
        self.surface = self.source["surface"]
        self.by_address = {int(l["address"]): l
                           for l in self.dialogue.get("line", [])}
        self.groups = tuple(self.surface["groups"])
        self.page_blob = bytes.fromhex(self.surface["page_source_hex"])
        first = int(self.surface["page_tile_first"])
        last = int(self.surface["page_tile_last"])
        if len(self.page_blob) != (last - first + 1) * 16:
            raise ValueError("page_source_hex does not cover the page region")
        self.rows: dict[int, dict[str, str]] = {}
        self.quotes: dict[str, list[Quote]] = {}
        self._build()

    # -- helpers ---------------------------------------------------------
    def _authored(self) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for index, entry in enumerate(self.source.get("line", []), 1):
            address = int(entry["address"])
            if address in out:
                raise ValueError(f"line {index}: duplicate address 0x{address:06x}")
            if address not in self.by_address:
                raise ValueError(
                    f"line {index}: 0x{address:06x} is not a decoded dialogue row")
            if self.by_address[address]["group"] not in self.groups:
                raise ValueError(
                    f"line {index}: 0x{address:06x} is in group "
                    f"{self.by_address[address]['group']!r}, which this surface "
                    "does not cover")
            out[address] = entry
        return out

    def _build(self) -> None:
        authored = self._authored()
        first_tile = int(self.surface["page_tile_first"])
        last_tile = int(self.surface["page_tile_last"])
        cells_budget = int(self.surface["text_cells"])
        start_col = int(self.surface["text_start_col"])

        # Quote boundaries: a ROM row whose address low bit 0x80 is clear starts
        # a quote; the row 0x80 above it, when the source authors one, is its
        # continuation line.  Both render from the same page.
        starts = [a for a in sorted(authored) if not (a & 0x80)]
        for lang in LANGS:
            quotes: list[Quote] = []
            for start in starts:
                addresses, texts = [], []
                for address in (start, start + 0x80):
                    entry = authored.get(address)
                    if entry is None or not entry.get(lang):
                        continue
                    if address != start and not addresses:
                        raise ValueError(
                            f"{lang}: 0x{address:06x} is a continuation line but "
                            f"0x{start:06x} has no {lang} text")
                    addresses.append(address)
                    texts.append(str(entry[lang]))
                if not addresses:
                    continue
                quote = Quote(len(quotes), addresses, texts, lang)
                if not quote.chars:
                    # Pure-ASCII translation (e.g. "......") needs no page and no
                    # rewritten row: the Latin cells already render it.
                    continue
                quotes.append(quote)
            # Allocate one distinct base tile per quote.  Distinct bases are what
            # makes the map guards distinguishable; the pages themselves overlap.
            for quote in quotes:
                quote.base = first_tile + quote.index
                need = 4 * len(quote.chars)
                if quote.base + need - 1 > last_tile:
                    raise ValueError(
                        f"{lang} 0x{quote.addresses[0]:06x}: page needs {need} "
                        f"tiles from 0x{quote.base:03x}, past 0x{last_tile:03x}")
                for slot, char in enumerate(quote.chars):
                    quote.tiles[char] = quote.base + 4 * slot
            # ROM row payloads.
            for quote in quotes:
                for address, text in zip(quote.addresses, quote.texts):
                    if text_cells(text) > cells_budget:
                        raise ValueError(
                            f"{lang} 0x{address:06x}: {text!r} is "
                            f"{text_cells(text)} cells, budget is {cells_budget}")
                    row = self.by_address[address]
                    if int(row["start_col"]) != start_col:
                        raise ValueError(
                            f"0x{address:06x}: start_col {row['start_col']} "
                            f"differs from the surface's {start_col}")
                    payload = encode_row(row["en_hex"], start_col,
                                         row_cells(text, quote))
                    self.rows.setdefault(address, {})[lang] = payload
            self.quotes[lang] = quotes
        self._verify_guards()

    # -- guards ----------------------------------------------------------
    def guard_prefix_words(self, quote: Quote) -> list[int]:
        row = self.by_address[quote.addresses[0]]
        payload = bytes.fromhex(self.rows[quote.addresses[0]][quote.lang])
        start_col = int(row["start_col"])
        words = []
        for col in range(start_col, ROW_WORDS):
            words.append(vram_word(payload[col * 2] | (payload[col * 2 + 1] << 8)))
        return words

    def _latin_prefixes(self, lang: str) -> list[list[int]]:
        """Post-transform map words for every row this language renders in Latin.

        A guard must not match one of those, or a page would land on an
        untranslated quote and swap its font out from under it.
        """
        out = []
        for line in self.dialogue.get("line", []):
            address = int(line["address"])
            if address in self.rows and lang in self.rows[address]:
                continue
            payload = bytes.fromhex(line["en_hex"])
            start_col = int(line["start_col"])
            out.append([vram_word(payload[c * 2] | (payload[c * 2 + 1] << 8))
                        for c in range(start_col, ROW_WORDS)])
        return out

    def _verify_guards(self) -> None:
        self.guards: dict[str, dict[int, list[int]]] = {}
        for lang in LANGS:
            quotes = self.quotes[lang]
            cjk = {q.addresses[0]: self.guard_prefix_words(q) for q in quotes}
            latin = self._latin_prefixes(lang)
            chosen: dict[int, list[int]] = {}
            for quote in quotes:
                mine = cjk[quote.addresses[0]]
                for length in range(2, len(mine) + 1):
                    prefix = mine[:length]
                    clash = any(other[:length] == prefix
                                for address, other in cjk.items()
                                if address != quote.addresses[0])
                    clash = clash or any(row[:length] == prefix for row in latin)
                    if not clash:
                        chosen[quote.addresses[0]] = prefix
                        break
                else:
                    raise ValueError(
                        f"{lang} 0x{quote.addresses[0]:06x}: no unique map guard "
                        "prefix exists for this quote")
            self.guards[lang] = chosen

    # -- emission --------------------------------------------------------
    def guard_address(self) -> int:
        return (int(self.surface["map_base"])
                + int(self.surface["map_first_row"]) * int(self.surface["map_row_stride"])
                + int(self.surface["text_start_col"]) * 2)

    def page_payload(self, quote: Quote) -> tuple[int, str, str]:
        spec = self.source["font"][quote.lang]
        payload = bytearray()
        for char in quote.chars:
            payload += b"".join(quad_tiles(char, quote.lang, spec))
        first_tile = int(self.surface["page_tile_first"])
        offset = (quote.base - first_tile) * 16
        source = self.page_blob[offset:offset + len(payload)]
        if len(source) != len(payload):
            raise ValueError("page slice runs past the captured free region")
        address = int(self.surface["char_base"]) + quote.base * 16
        return address, source.hex(), payload.hex()

    def generated_blocks(self) -> list[str]:
        blocks = [BEGIN_MARKER]
        guard_address = self.guard_address()
        for lang in LANGS:
            for quote in self.quotes[lang]:
                address, source_hex, payload_hex = self.page_payload(quote)
                guard = self.guards[lang][quote.addresses[0]]
                guard_hex = b"".join(word_bytes(w) for w in guard).hex()
                blocks.extend([
                    "",
                    "[[vram_patch]]",
                    f"# {lang} quote 0x{quote.addresses[0]:06x} "
                    f"({' / '.join(self.by_address[a]['en'] for a in quote.addresses)})",
                    f"# page base tile 0x{quote.base:03x}, "
                    f"{len(quote.chars)} glyphs, {4 * len(quote.chars)} tiles",
                    f"address = 0x{address:04x}",
                    f'source_hex = "{source_hex}"',
                    f'{lang}_hex = "{payload_hex}"',
                    f"guard_address = 0x{guard_address:04x}",
                    f'guard_hex = "{guard_hex}"',
                ])
        blocks.extend(["", END_MARKER, ""])
        if not any(self.quotes[lang] for lang in LANGS):
            blocks.insert(1, "# No CJK dialogue pages are authored yet.")
        return blocks


def replace_generated_section(table_text: str, generated: str) -> str:
    pattern = re.compile(
        rf"(?ms)^{re.escape(BEGIN_MARKER)}\n.*?^{re.escape(END_MARKER)}\n?")
    matches = list(pattern.finditer(table_text))
    if matches:
        pieces = [table_text[:matches[0].start()], generated]
        cursor = matches[0].end()
        for match in matches[1:]:
            pieces.append(table_text[cursor:match.start()])
            cursor = match.end()
        pieces.append(table_text[cursor:])
        return "".join(pieces)
    return table_text.rstrip() + "\n\n" + generated


def row_payloads(root: Path | None = None) -> dict[int, dict[str, str]]:
    """{rom row address: {lang: tilemap hex}} for generate_dialogue_patch.py.

    The CJK payloads must ride inside the SAME rom_patch entry as the Latin
    ones.  A second patch over the same row would break language switching:
    the first patch's guard would no longer recognise the bytes the second one
    left behind, so es/fr/it/pt would stop being restored.
    """
    return Build(root or repo_root()).rows


# --------------------------------------------------------------------------
# previews
# --------------------------------------------------------------------------

PALETTE = ((0, 0, 0), (0, 0, 0), (160, 160, 160), (248, 248, 248))
# The 128 Latin dialogue cells the reference translation installs; VRAM
# 0x4000-0x47ff is a byte-exact copy of this span of the English post-patch
# cart image, so previews can replay ASCII cells from the same source.
LATIN_FONT_ROM = 0x006F00
_LATIN_CACHE: dict[str, bytes] = {}


def latin_font(root: Path) -> bytes:
    if "font" not in _LATIN_CACHE:
        from reconstruct_table_image import build_image, load_table
        rom = (root / "Shin Kidou Senki Gundam W - Endless Duel (J).smc").read_bytes()
        image, _, _ = build_image("en", load_table(root / "translations" /
                                                  "endless_duel.toml"), rom)
        _LATIN_CACHE["font"] = image[LATIN_FONT_ROM:LATIN_FONT_ROM + 0x80 * 16]
    return _LATIN_CACHE["font"]


def write_previews(build: Build, out_dir: Path, count: int = 6) -> list[Path]:
    from PIL import Image
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for lang in LANGS:
        quotes = build.quotes[lang]
        if count <= 0:
            selected = quotes
        else:
            step = max(1, len(quotes) // count)
            selected = quotes[::step][:count]
        for quote in selected:
            # Replay the real path: page tiles into a tile bank, then the rows'
            # map words through (rom & 0x3ff) | 0x2000 into pixels.
            spec = build.source["font"][lang]
            bank: dict[int, bytes] = {}
            for char in quote.chars:
                for offset, tile in enumerate(quad_tiles(char, lang, spec)):
                    bank[quote.tiles[char] + offset] = tile
            for tile in range(0x80):
                bank.setdefault(tile, latin_font(build.root)[tile * 16:tile * 16 + 16])
            rows = []
            for address in quote.addresses:
                payload = bytes.fromhex(build.rows[address][lang])
                rows.append(payload)
            width, height = 30 * 8, len(rows) * 16
            img = Image.new("RGB", (width, height), PALETTE[1])
            px = img.load()
            for row_index, payload in enumerate(rows):
                for half in (0, 1):
                    for col in range(2, 32):
                        word = payload[half * ROW_BYTES + col * 2] | (
                            payload[half * ROW_BYTES + col * 2 + 1] << 8)
                        tile = vram_word(word) & 0x03FF
                        data = bank.get(tile)
                        if data is None:
                            continue
                        for y in range(8):
                            plane0, plane1 = data[y * 2], data[y * 2 + 1]
                            for x in range(8):
                                value = ((plane0 >> (7 - x)) & 1) | (
                                    ((plane1 >> (7 - x)) & 1) << 1)
                                px[(col - 2) * 8 + x,
                                   row_index * 16 + half * 8 + y] = PALETTE[value]
            path = out_dir / f"{lang}_{quote.addresses[0]:06x}.png"
            img.resize((width * 3, height * 3), Image.NEAREST).save(path)
            written.append(path)
    return written


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=str(root / "translations" / "endless_duel_dialogue_cjk.toml"))
    parser.add_argument(
        "--table", default=str(root / "translations" / "endless_duel.toml"))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--previews", action="store_true")
    parser.add_argument(
        "--preview-dir",
        default=str(root / "translations" / "dialogue_cjk_previews"))
    parser.add_argument(
        "--preview-count", type=int, default=6,
        help="how many quotes per language to render (0 = every quote)")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    build = Build(root, Path(args.source))
    table_path = Path(args.table)
    generated = "\n".join(build.generated_blocks())
    table_text = table_path.read_text(encoding="utf-8")
    updated = replace_generated_section(table_text, generated)

    if args.stats:
        for lang in LANGS:
            quotes = build.quotes[lang]
            print(f"{lang}: {len(quotes)} quotes, "
                  f"{sum(len(q.addresses) for q in quotes)} rows, "
                  f"pages {min(4 * len(q.chars) for q in quotes)}-"
                  f"{max(4 * len(q.chars) for q in quotes)} tiles, "
                  f"guards {min(len(g) for g in build.guards[lang].values())}-"
                  f"{max(len(g) for g in build.guards[lang].values())} words")

    if args.previews:
        for path in write_previews(build, Path(args.preview_dir),
                                   args.preview_count):
            print(f"preview {path}")

    if args.write:
        table_path.write_text(updated, encoding="utf-8", newline="\n")
        print(f"updated {table_path}")
        return 0

    if updated != table_text:
        print("dialogue CJK page section is not up to date")
        return 1
    print("dialogue CJK page section is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
