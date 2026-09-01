#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the CJK dialogue glyph pages for every dialogue text surface.

The dialogue screens are encoded BG tilemap text (playbook section 3a), but
unlike the Latin languages they cannot be translated by rewriting tilemap words
alone: the font the reference translation installs holds 128 Latin cells and
nothing else, and Korean/Chinese need thousands of glyphs.

Mechanism: per-quote glyph paging.

  * Each surface owns a window of tile ids that is unreferenced by every layer
    on that screen and byte-stable across every recorded capture, so it is a
    free scratch page.
  * Each quote (its one or two ROM rows) gets its OWN page laid out from a
    per-quote base tile id.  Pages deliberately OVERLAP in address space --
    only one quote is ever on screen, so only one page needs to be resident.
  * The quote's ROM rows are rewritten (rom_patch, emitted by
    generate_dialogue_patch.py which imports row_payloads() from here) to
    reference that page's tiles.
  * The page art ships as a guarded vram_patch whose guard sits on the BG3 MAP
    words the draw routine writes for that quote's first row.  The guard is the
    interception point: the page lands the instant the game starts revealing
    this specific quote, and can never land on any other screen.  The engine
    treats an explicit guard as REPLACING the content check at the payload
    address, which is what lets overlapping pages work at all.

Three surfaces, all captured live (see the geometry evidence banner in
translations/endless_duel_dialogue_cjk.toml):

  | surface            | map base | 1st text row | guard  | char base | word OR |
  |--------------------|----------|--------------|--------|-----------|---------|
  | battle_quote       | 0xc000   | 22           | 0xc584 | 0x4000    | 0x2000  |
  | final_conversation | 0xf000   | 22           | 0xf584 | 0xc000    | 0x2000  |
  | ending             | 0xf000   | 21           | 0xf544 | 0xc000    | 0x2400  |

Guards are proved unique, per language, against every dialogue row any surface
will actually render -- the CJK rows of every surface plus the Latin rows every
surface keeps -- so a guard can never match a row it does not own, whichever
screen that row draws on.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
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
# Every surface's palette row is the same three-step ramp: value 1 black (box
# fill), 2 grey (shading), 3 white (glyph body); value 0 is transparent.  The
# stock Latin cells draw white bodies with grey shading on the black field, so
# CJK cells use the same three indices.
PIX_BG, PIX_EDGE, PIX_BODY = 1, 2, 3
MAX_GLYPH_W, MAX_GLYPH_H = 16, 14
MIN_GUARD_WORDS = 2
# The reference translation's Latin dialogue font is 128 cells at tile id 0 of
# every surface's char base (byte-identical on all three, and equal to the
# English post-patch cart image at ROM 0x006f00).  No page window may start
# inside it.
LATIN_FONT_TILES = 0x80


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
# surface model
# --------------------------------------------------------------------------

class Surface:
    """One dialogue screen: its map geometry, char base and free page window."""

    def __init__(self, index: int, spec: dict):
        self.index = index
        self.name = str(spec["name"])
        self.groups = tuple(spec["groups"])
        if not self.groups:
            raise ValueError(f"surface {self.name!r}: no groups")
        self.map_base = int(spec["map_base"])
        self.map_row_stride = int(spec["map_row_stride"])
        self.map_first_row = int(spec["map_first_row"])
        self.text_start_col = int(spec["text_start_col"])
        self.text_cells = int(spec["text_cells"])
        self.char_base = int(spec["char_base"])
        # What the draw routine ORs into the map word: 0x2000 is priority with
        # palette 0, 0x2400 is priority with palette 1 (the ending screen).
        self.word_or = int(spec["word_or"])
        self.palette_row = int(spec["palette_row"])
        self.palette = self._palette(str(spec["palette_hex"]))
        self.page_tile_first = int(spec["page_tile_first"])
        self.page_tile_last = int(spec["page_tile_last"])
        # First VRAM byte that is NOT tile data under this char base -- the
        # 0xc000 screens hit their BG1 map at 0xd000, so a page window one tile
        # too long would stamp glyph art over a tilemap.
        self.char_region_end = int(spec["char_region_end"])
        if self.page_tile_last < self.page_tile_first:
            raise ValueError(f"surface {self.name!r}: empty page window")
        if self.page_tile_first < LATIN_FONT_TILES:
            raise ValueError(
                f"surface {self.name!r}: page window starts at "
                f"0x{self.page_tile_first:03x}, inside the Latin dialogue font "
                f"(tile ids 0x000-0x{LATIN_FONT_TILES - 1:03x})")
        end = self.char_base + (self.page_tile_last + 1) * 16
        if end > self.char_region_end:
            raise ValueError(
                f"surface {self.name!r}: page window ends at VRAM 0x{end:04x}, "
                f"past the char region end 0x{self.char_region_end:04x}")
        self.page_blob = bytes.fromhex(str(spec["page_source_hex"]))
        want = (self.page_tile_last - self.page_tile_first + 1) * 16
        if len(self.page_blob) != want:
            raise ValueError(
                f"surface {self.name!r}: page_source_hex is "
                f"{len(self.page_blob)} bytes, the page window needs {want}")
        if (self.word_or >> 10) & 7 != self.palette_row:
            raise ValueError(
                f"surface {self.name!r}: word_or {self.word_or:#06x} selects "
                f"palette {(self.word_or >> 10) & 7}, palette_row says "
                f"{self.palette_row}")

    @staticmethod
    def _palette(hex_value: str) -> tuple[tuple[int, int, int], ...]:
        """The four 15-bit BGR CGRAM words of this surface's palette row."""
        raw = bytes.fromhex(hex_value)
        if len(raw) != 8:
            raise ValueError("palette_hex must be 4 CGRAM words (8 bytes)")
        out = []
        for i in range(4):
            word = raw[i * 2] | (raw[i * 2 + 1] << 8)
            out.append((((word) & 31) * 255 // 31,
                        ((word >> 5) & 31) * 255 // 31,
                        ((word >> 10) & 31) * 255 // 31))
        # Value 0 is transparent on BG3, so its CGRAM colour is never shown;
        # what the player sees through it is the box's own black fill, which is
        # value 1.  Previews paint it that way so they match the screenshots.
        out[0] = out[PIX_BG]
        return tuple(out)

    @property
    def page_tiles(self) -> int:
        return self.page_tile_last - self.page_tile_first + 1

    def vram_word(self, rom_word: int) -> int:
        """What the draw routine writes into the BG3 map for a ROM word."""
        return (rom_word & 0x03FF) | self.word_or

    @property
    def guard_address(self) -> int:
        return (self.map_base + self.map_first_row * self.map_row_stride
                + self.text_start_col * 2)

    def tile_address(self, tile: int) -> int:
        return self.char_base + tile * 16


class Quote:
    def __init__(self, surface: Surface, index: int, addresses: list[int],
                 texts: list[str], lang: str):
        self.surface = surface
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
        self.by_address = {int(l["address"]): l
                           for l in self.dialogue.get("line", [])}
        self.surfaces = [Surface(i, spec)
                         for i, spec in enumerate(self.source["surface"])]
        self._verify_surfaces()
        self.rows: dict[int, dict[str, str]] = {}
        # {surface index: {lang: [Quote]}}
        self.quotes: dict[int, dict[str, list[Quote]]] = {}
        self._build()

    # -- surface sanity --------------------------------------------------
    def _verify_surfaces(self) -> None:
        self.surface_by_group: dict[str, Surface] = {}
        for surface in self.surfaces:
            for group in surface.groups:
                if group in self.surface_by_group:
                    raise ValueError(
                        f"group {group!r} is claimed by both "
                        f"{self.surface_by_group[group].name!r} and "
                        f"{surface.name!r}")
                self.surface_by_group[group] = surface
        seen: dict[int, Surface] = {}
        for surface in self.surfaces:
            other = seen.get(surface.guard_address)
            if other is not None:
                raise ValueError(
                    f"surfaces {other.name!r} and {surface.name!r} derive the "
                    f"same guard address 0x{surface.guard_address:04x}; guard "
                    "uniqueness cannot be proved per surface")
            seen[surface.guard_address] = surface

    # -- helpers ---------------------------------------------------------
    def _authored(self) -> dict[int, tuple[Surface, dict]]:
        out: dict[int, tuple[Surface, dict]] = {}
        for index, entry in enumerate(self.source.get("line", []), 1):
            address = int(entry["address"])
            if address in out:
                raise ValueError(f"line {index}: duplicate address 0x{address:06x}")
            if address not in self.by_address:
                raise ValueError(
                    f"line {index}: 0x{address:06x} is not a decoded dialogue row")
            group = self.by_address[address]["group"]
            surface = self.surface_by_group.get(group)
            if surface is None:
                raise ValueError(
                    f"line {index}: 0x{address:06x} is in group {group!r}, "
                    "which no surface covers")
            out[address] = (surface, entry)
        return out

    def _build(self) -> None:
        authored = self._authored()
        for surface in self.surfaces:
            self.quotes[surface.index] = {}
            mine = sorted(a for a, (s, _) in authored.items()
                          if s is surface)
            # Quote boundaries: a ROM row whose address bit 0x80 is clear starts
            # a quote; the row 0x80 above it, when the source authors one, is its
            # continuation line.  Both render from the same page.
            starts = [a for a in mine if not (a & 0x80)]
            for lang in LANGS:
                quotes: list[Quote] = []
                for start in starts:
                    addresses, texts = [], []
                    for address in (start, start + 0x80):
                        entry = authored.get(address)
                        if entry is None or entry[0] is not surface:
                            continue
                        if not entry[1].get(lang):
                            continue
                        if address != start and not addresses:
                            raise ValueError(
                                f"{lang}: 0x{address:06x} is a continuation line "
                                f"but 0x{start:06x} has no {lang} text")
                        addresses.append(address)
                        texts.append(str(entry[1][lang]))
                    if not addresses:
                        continue
                    quote = Quote(surface, len(quotes), addresses, texts, lang)
                    if not quote.chars:
                        # Pure-ASCII translation (e.g. "......") needs no page
                        # and no rewritten row: the Latin cells render it.
                        continue
                    quotes.append(quote)
                # Allocate one distinct base tile per quote.  Distinct bases are
                # what makes the map guards distinguishable (two quotes can share
                # the same English text -- battle_dialogue_3 repeats four rows
                # verbatim); the pages themselves overlap.
                for quote in quotes:
                    quote.base = surface.page_tile_first + quote.index
                    need = 4 * len(quote.chars)
                    if quote.base + need - 1 > surface.page_tile_last:
                        raise ValueError(
                            f"{surface.name} {lang} 0x{quote.addresses[0]:06x}: "
                            f"page needs {need} tiles from 0x{quote.base:03x}, "
                            f"past the window end 0x{surface.page_tile_last:03x} "
                            f"(window is {surface.page_tiles} tiles)")
                    for slot, char in enumerate(quote.chars):
                        quote.tiles[char] = quote.base + 4 * slot
                # ROM row payloads.
                for quote in quotes:
                    for address, text in zip(quote.addresses, quote.texts):
                        if text_cells(text) > surface.text_cells:
                            raise ValueError(
                                f"{surface.name} {lang} 0x{address:06x}: "
                                f"{text!r} is {text_cells(text)} cells, budget "
                                f"is {surface.text_cells}")
                        row = self.by_address[address]
                        if int(row["start_col"]) != surface.text_start_col:
                            raise ValueError(
                                f"0x{address:06x}: start_col {row['start_col']} "
                                f"differs from {surface.name}'s "
                                f"{surface.text_start_col}")
                        payload = encode_row(row["en_hex"], surface.text_start_col,
                                             row_cells(text, quote))
                        self.rows.setdefault(address, {})[lang] = payload
                self.quotes[surface.index][lang] = quotes
        self._verify_guards()

    def all_quotes(self, lang: str) -> list[Quote]:
        out = []
        for surface in self.surfaces:
            out.extend(self.quotes[surface.index][lang])
        return out

    # -- guards ----------------------------------------------------------
    def row_words(self, surface: Surface, address: int, payload_hex: str
                  ) -> list[int]:
        """Post-transform map words this row puts on its first text row."""
        payload = bytes.fromhex(payload_hex)
        start_col = int(self.by_address[address]["start_col"])
        return [surface.vram_word(payload[c * 2] | (payload[c * 2 + 1] << 8))
                for c in range(start_col, ROW_WORDS)]

    def _prefix_pool(self, lang: str) -> list[tuple[int, list[int]]]:
        """(row address, post-transform words) for every row that can land on a
        surface's FIRST text row -- i.e. everything a guard could ever see.

        A guard must not match one of those, or a page would land on a quote it
        does not own and swap that quote's font out from under it.  The pool
        spans all surfaces even though their guard addresses differ: that is
        strictly stronger than a per-surface check and costs nothing.

        Continuation rows (address bit 0x80 set) are excluded: they render two
        map rows below the guard and can never appear at the guard address.
        Every 0x80 row in the decoded table has its 0x00 partner, so the bit is
        a sound first-line test.  Including them is not merely wasted work, it
        is wrong -- two zh quotes legitimately produce the identical 30-word row
        (battle_dialogue_0 0x007c80's second line and battle_dialogue_4
        0x027500's first line), and treating that as a clash rejects a guard
        that is in fact unambiguous.
        """
        pool: list[tuple[int, list[int]]] = []
        for line in self.dialogue.get("line", []):
            address = int(line["address"])
            if address & 0x80:
                continue
            surface = self.surface_by_group.get(line["group"])
            if surface is None:
                continue
            payload = self.rows.get(address, {}).get(lang) or line["en_hex"]
            pool.append((address, self.row_words(surface, address, payload)))
        return pool

    def _verify_guards(self) -> None:
        self.guards: dict[str, dict[int, list[int]]] = {}
        for lang in LANGS:
            pool = self._prefix_pool(lang)
            chosen: dict[int, list[int]] = {}
            for quote in self.all_quotes(lang):
                own = quote.addresses[0]
                mine = self.row_words(quote.surface, own,
                                      self.rows[own][quote.lang])
                for length in range(MIN_GUARD_WORDS, len(mine) + 1):
                    prefix = mine[:length]
                    if not any(words[:length] == prefix
                               for address, words in pool if address != own):
                        chosen[own] = prefix
                        break
                else:
                    raise ValueError(
                        f"{quote.surface.name} {lang} 0x{own:06x}: no unique map "
                        "guard prefix exists for this quote")
            self.guards[lang] = chosen

    # -- emission --------------------------------------------------------
    def page_payload(self, quote: Quote) -> tuple[int, str, str]:
        surface = quote.surface
        spec = self.source["font"][quote.lang]
        payload = bytearray()
        for char in quote.chars:
            payload += b"".join(quad_tiles(char, quote.lang, spec))
        offset = (quote.base - surface.page_tile_first) * 16
        source = surface.page_blob[offset:offset + len(payload)]
        if len(source) != len(payload):
            raise ValueError(
                f"{surface.name} {quote.lang} 0x{quote.addresses[0]:06x}: page "
                "slice runs past the captured free region")
        return surface.tile_address(quote.base), source.hex(), payload.hex()

    def generated_blocks(self) -> list[str]:
        blocks = [BEGIN_MARKER]
        for surface in self.surfaces:
            for lang in LANGS:
                for quote in self.quotes[surface.index][lang]:
                    address, source_hex, payload_hex = self.page_payload(quote)
                    guard = self.guards[lang][quote.addresses[0]]
                    guard_hex = b"".join(word_bytes(w) for w in guard).hex()
                    en = " / ".join(self.by_address[a]["en"]
                                    for a in quote.addresses)
                    blocks.extend([
                        "",
                        "[[vram_patch]]",
                        f"# {surface.name} {lang} quote "
                        f"0x{quote.addresses[0]:06x} ({en})",
                        f"# page base tile 0x{quote.base:03x}, "
                        f"{len(quote.chars)} glyphs, {4 * len(quote.chars)} tiles",
                        f"address = 0x{address:04x}",
                        f'source_hex = "{source_hex}"',
                        f'{lang}_hex = "{payload_hex}"',
                        f"guard_address = 0x{surface.guard_address:04x}",
                        f'guard_hex = "{guard_hex}"',
                    ])
        blocks.extend(["", END_MARKER, ""])
        if not any(self.all_quotes(lang) for lang in LANGS):
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

# The 128 Latin dialogue cells the reference translation installs.  Every
# surface's char base holds a byte-exact copy of this span of the English
# post-patch cart image (verified against the live VRAM of all three screens),
# so previews can replay ASCII cells from the same source.
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
    for surface in build.surfaces:
        palette = surface.palette
        for lang in LANGS:
            quotes = build.quotes[surface.index][lang]
            if not quotes:
                continue
            if count <= 0:
                selected = quotes
            else:
                step = max(1, len(quotes) // count)
                selected = quotes[::step][:count]
            for quote in selected:
                # Replay the real path: page tiles into a tile bank, then the
                # rows' map words through the surface's own word transform into
                # pixels, painted with the surface's own CGRAM palette row.
                spec = build.source["font"][lang]
                bank: dict[int, bytes] = {}
                for char in quote.chars:
                    for offset, tile in enumerate(quad_tiles(char, lang, spec)):
                        bank[quote.tiles[char] + offset] = tile
                font = latin_font(build.root)
                for tile in range(0x80):
                    bank.setdefault(tile, font[tile * 16:tile * 16 + 16])
                rows = [bytes.fromhex(build.rows[a][lang])
                        for a in quote.addresses]
                width, height = 30 * 8, len(rows) * 16
                img = Image.new("RGB", (width, height), palette[PIX_BG])
                px = img.load()
                for row_index, payload in enumerate(rows):
                    for half in (0, 1):
                        for col in range(2, 32):
                            word = payload[half * ROW_BYTES + col * 2] | (
                                payload[half * ROW_BYTES + col * 2 + 1] << 8)
                            tile = surface.vram_word(word) & 0x03FF
                            data = bank.get(tile)
                            if data is None:
                                continue
                            for y in range(8):
                                plane0, plane1 = data[y * 2], data[y * 2 + 1]
                                for x in range(8):
                                    value = ((plane0 >> (7 - x)) & 1) | (
                                        ((plane1 >> (7 - x)) & 1) << 1)
                                    px[(col - 2) * 8 + x,
                                       row_index * 16 + half * 8 + y] = \
                                        palette[value]
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
        help="how many quotes per language per surface to render (0 = every one)")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    build = Build(root, Path(args.source))
    table_path = Path(args.table)
    generated = "\n".join(build.generated_blocks())
    table_text = table_path.read_text(encoding="utf-8")
    updated = replace_generated_section(table_text, generated)

    if args.stats:
        for surface in build.surfaces:
            print(f"{surface.name}: guard 0x{surface.guard_address:04x}, "
                  f"char base 0x{surface.char_base:04x}, word OR "
                  f"0x{surface.word_or:04x} (palette {surface.palette_row}), "
                  f"page window 0x{surface.page_tile_first:03x}-"
                  f"0x{surface.page_tile_last:03x} ({surface.page_tiles} tiles)")
            for lang in LANGS:
                quotes = build.quotes[surface.index][lang]
                if not quotes:
                    print(f"  {lang}: no pages")
                    continue
                tiles = [4 * len(q.chars) for q in quotes]
                highest = max(q.base + 4 * len(q.chars) - 1 for q in quotes)
                guards = [len(build.guards[lang][q.addresses[0]]) for q in quotes]
                print(f"  {lang}: {len(quotes)} pages, "
                      f"{sum(len(q.addresses) for q in quotes)} rows, "
                      f"pages {min(tiles)}-{max(tiles)} tiles, highest tile "
                      f"0x{highest:03x}, headroom "
                      f"{surface.page_tile_last - highest} tiles, "
                      f"guards {min(guards)}-{max(guards)} words")

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
