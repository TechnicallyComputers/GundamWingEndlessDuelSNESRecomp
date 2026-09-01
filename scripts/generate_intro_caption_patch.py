#!/usr/bin/env python3
"""Generate per-language tile art for the intro caption ("After Colony 195").

The caption is OBJ sprite text. The English fan translation stores the whole
OBJ tile bank UNCOMPRESSED at ROM 0x00e000-0x00ffff, and OBJ VRAM
0x8000-0x9fff is a byte-exact linear copy of it (tile n at 0xe000 + n*32,
4bpp, 32 bytes/tile, planes 0/1 interleaved then planes 2/3 at +16). The OAM
layout is byte-identical across languages, so translating the caption is
purely a matter of swapping tile art in that bank.

Two rectangles carry the caption:

  line 1 ("After Colony")  12 tiles x 4 tile rows
      rows 0xf800 / 0xfa00 / 0xfc00 / 0xfe00, 0x180 bytes each
      screen y79-110; tiles +0..+5 -> x64-111, tiles +6..+11 -> x128-175
      (there is a hard 16px screen gap at x112-127 whose cell is the SHARED
      blank tile 236 - it is not part of the rectangle and must never be
      drawn into)
  line 2 ("195")           7 tiles x 4 tile rows
      rows 0xf0c0 / 0xf2c0 / 0xf4c0 / 0xf6c0, 0xe0 bytes each
      screen y119-142, x88-143.  Tiles 141-143 / 157-159 / 173-175 are the
      blinking-cursor sprite (JP "年") and are deliberately outside the
      rectangle.

Palette: OBJ palette 7 (CGRAM 240-255).  Line 1 text is the green ramp at
indices 8-12; index 0 is transparent.

The generated [[rom_patch]] entries own those rectangles whole, so every
pre-existing IPS-delta fragment that intersects them is split (or removed)
by --write.  --check re-runs the non-overlap invariant AND replays the whole
rom_patch table against the JP ROM with full guard + fallback semantics,
requiring the en and es 2MB images to match the digests recorded in
translations/endless_duel_intro_caption.toml (captured from the pre-split
table).

Sources:
- translations/endless_duel_intro_caption.toml   geometry, faces, strings,
                                                 baseline en/es art, digests
- Shin Kidou Senki Gundam W - Endless Duel (J).smc   source_hex ground truth

Usage:
  python scripts/generate_intro_caption_patch.py --check
  python scripts/generate_intro_caption_patch.py --write
  python scripts/generate_intro_caption_patch.py --check --previews
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tomllib
from pathlib import Path

LANGS = ("en", "es", "fr", "it", "pt", "zh", "ko")

BEGIN_MARK = "# BEGIN GENERATED INTRO CAPTION PATCHES"
END_MARK = "# END GENERATED INTRO CAPTION PATCHES"

ROM_NAME = "Shin Kidou Senki Gundam W - Endless Duel (J).smc"
ROM_SIZE = 0x200000

FONT_DIR = Path(r"C:\Windows\Fonts")


class GenError(RuntimeError):
    pass


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


def load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


# --------------------------------------------------------------------------
# runtime semantics (mirrors snesrecomp/runner/src/snes_text_xlate.cpp)
# --------------------------------------------------------------------------

def language_chain(table: dict, lang: str) -> list[str]:
    chain: list[str] = []
    current = lang
    for _ in range(8):
        if not current or current == "off" or current in chain:
            break
        chain.append(current)
        current = table.get(f"fallback_{current}", "")
    return chain


def patch_target(patch: dict, table: dict, lang: str, source: bytes) -> bytes:
    for candidate in language_chain(table, lang):
        key = f"{candidate}_hex"
        if key in patch:
            return bytes.fromhex(str(patch[key]))
    return source


def patch_matches_any(patch: dict, data: bytes, source: bytes) -> bool:
    if data == source:
        return True
    for lang in LANGS:
        key = f"{lang}_hex"
        if key in patch:
            candidate = bytes.fromhex(str(patch[key]))
            if len(candidate) == len(data) and candidate == data:
                return True
    return False


def apply_rom_patches(table: dict, lang: str, rom: bytes) -> bytes:
    image = bytearray(rom)
    for patch in table.get("rom_patch", []):
        source = bytes.fromhex(str(patch["source_hex"]))
        if not source:
            continue
        address = int(patch["address"])
        if address + len(source) > len(image):
            continue
        target = patch_target(patch, table, lang, source)
        if len(target) != len(source):
            continue
        current = bytes(image[address:address + len(source)])
        if not patch_matches_any(patch, current, source):
            continue
        image[address:address + len(source)] = target
    return bytes(image)


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

class Geometry:
    def __init__(self, spec: dict):
        self.tile_bytes = int(spec["tile_bytes"])
        self.lines = {}
        for line_id in ("line1", "line2"):
            line = spec[line_id]
            self.lines[line_id] = {
                "tiles": int(line["tiles"]),
                "rows": [int(a) for a in line["row_address"]],
                "span": int(line["tiles"]) * self.tile_bytes,
                "boxes": [(int(b["x"]), int(b["width"]))
                          for b in line.get("box", [])],
                "screen_x": [int(b["screen_x"]) for b in line.get("box", [])],
                "screen_y0": int(line["screen_y0"]),
                "text_y0": int(line["text_y0"]),
                "text_y1": int(line["text_y1"]),
            }

    def rects(self) -> list[tuple[int, int]]:
        out = []
        for line in self.lines.values():
            for row in line["rows"]:
                out.append((row, line["span"]))
        return sorted(out)


# --------------------------------------------------------------------------
# 4bpp packing
# --------------------------------------------------------------------------

def canvas_to_rows(canvas: list[list[int]], tiles: int,
                   tile_bytes: int) -> list[bytes]:
    """Canvas is (rows*8) x (tiles*8) palette indices -> one blob per tile row."""
    out = []
    for tile_row in range(len(canvas) // 8):
        blob = bytearray()
        for tile in range(tiles):
            packed = bytearray(tile_bytes)
            for y in range(8):
                b0 = b1 = b2 = b3 = 0
                for x in range(8):
                    value = canvas[tile_row * 8 + y][tile * 8 + x]
                    bit = 1 << (7 - x)
                    if value & 1:
                        b0 |= bit
                    if value & 2:
                        b1 |= bit
                    if value & 4:
                        b2 |= bit
                    if value & 8:
                        b3 |= bit
                packed[2 * y] = b0
                packed[2 * y + 1] = b1
                packed[16 + 2 * y] = b2
                packed[16 + 2 * y + 1] = b3
            blob += packed
        out.append(bytes(blob))
    return out


def rows_to_canvas(rows: list[bytes], tiles: int) -> list[list[int]]:
    canvas = [[0] * (tiles * 8) for _ in range(len(rows) * 8)]
    for tile_row, blob in enumerate(rows):
        for tile in range(tiles):
            base = tile * 32
            for y in range(8):
                a0, a1 = blob[base + 2 * y], blob[base + 2 * y + 1]
                a2, a3 = blob[base + 16 + 2 * y], blob[base + 16 + 2 * y + 1]
                for x in range(8):
                    shift = 7 - x
                    canvas[tile_row * 8 + y][tile * 8 + x] = (
                        ((a0 >> shift) & 1)
                        | (((a1 >> shift) & 1) << 1)
                        | (((a2 >> shift) & 1) << 2)
                        | (((a3 >> shift) & 1) << 3))
    return canvas


# --------------------------------------------------------------------------
# text rasterisation
# --------------------------------------------------------------------------

def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover
        raise GenError("Pillow is required to render intro caption art") from exc
    return Image, ImageDraw, ImageFont


def font_path(name: str) -> Path:
    path = FONT_DIR / name
    if not path.is_file():
        raise GenError(f"font not found: {path}")
    return path


def raster_proportional(strings: list[str], face: dict,
                        box_widths: list[int], height: int):
    """Render every string at one shared size on a shared baseline.

    Returns (size, [mask, ...], y_offset) where each mask is a list of rows
    of booleans already trimmed horizontally, and y_offset is the common top
    of the combined block relative to the text band.
    """
    Image, ImageDraw, ImageFont = _pil()
    threshold = int(face.get("threshold", 110))
    pad = int(face.get("shadow_pad", 1))
    tracking = float(face.get("tracking", 0))
    path = str(font_path(str(face["font"])))
    for size in range(int(face["max_size"]), int(face["min_size"]) - 1, -1):
        font = ImageFont.truetype(path, size)
        canvas_w = max(box_widths) * 4 + 64
        canvas_h = height * 4 + 64
        rendered = []
        ok = True
        for text, box_w in zip(strings, box_widths):
            image = Image.new("L", (canvas_w, canvas_h), 0)
            draw = ImageDraw.Draw(image)
            # per-character so the face can carry extra tracking: Impact-class
            # condensed faces run their stems together below ~16px
            pen = 32.0
            for ch in text:
                draw.text((pen, 32), ch, font=font, fill=255, anchor="ls")
                pen += font.getlength(ch) + tracking
            image = image.point(lambda v: 255 if v >= threshold else 0)
            box = image.getbbox()
            if box is None:
                ok = False
                break
            if box[2] - box[0] > box_w - pad:
                ok = False
                break
            rendered.append((image, box))
        if not ok:
            continue
        top = min(b[1] for _, b in rendered)
        bottom = max(b[3] for _, b in rendered)
        if bottom - top > height - pad:
            continue
        masks = []
        for image, box in rendered:
            crop = image.crop((box[0], top, box[2], bottom))
            pixels = crop.load()
            masks.append([[bool(pixels[x, y]) for x in range(crop.width)]
                          for y in range(crop.height)])
        return size, masks
    raise GenError(
        f"no font size in [{face['min_size']}, {face['max_size']}] fits "
        f"{strings!r} into {box_widths} x {height}")


def raster_cell(strings: list[str], face: dict, box_widths: list[int],
                height: int):
    """Render CJK text as fixed square cells, the way the crawl glyphs are."""
    Image, ImageDraw, ImageFont = _pil()
    threshold = int(face.get("threshold", 110))
    cell = int(face["cell"])
    size = int(face["size"])
    path = str(font_path(str(face["font"])))
    font = ImageFont.truetype(path, size)
    masks = []
    for text, box_w in zip(strings, box_widths):
        width = cell * len(text)
        if width > box_w:
            raise GenError(f"{text!r} needs {width}px, box is {box_w}px")
        if cell > height:
            raise GenError(f"cell {cell}px exceeds {height}px text band")
        image = Image.new("L", (width, cell), 0)
        draw = ImageDraw.Draw(image)
        for i, ch in enumerate(text):
            box = draw.textbbox((0, 0), ch, font=font)
            gw, gh = box[2] - box[0], box[3] - box[1]
            draw.text((i * cell + (cell - gw) // 2 - box[0],
                       (cell - gh) // 2 - box[1]), ch, font=font, fill=255)
        image = image.point(lambda v: 255 if v >= threshold else 0)
        pixels = image.load()
        masks.append([[bool(pixels[x, y]) for x in range(width)]
                      for y in range(cell)])
    return size, masks


def paint(canvas: list[list[int]], mask, box_x: int, box_w: int,
          y0: int, y1: int, body: int, shadow: int) -> None:
    height = len(mask)
    width = len(mask[0]) if height else 0
    ox = box_x + (box_w - width) // 2
    oy = y0 + ((y1 - y0 + 1) - height) // 2
    if oy < y0 or oy + height - 1 > y1:
        raise GenError("rendered text does not fit the text band")
    for dy, dx in ((1, 0), (0, 1), (1, 1)):
        for y in range(height):
            for x in range(width):
                if not mask[y][x]:
                    continue
                yy, xx = oy + y + dy, ox + x + dx
                if not (0 <= yy < len(canvas) and box_x <= xx < box_x + box_w):
                    continue
                if canvas[yy][xx] == 0:
                    canvas[yy][xx] = shadow
    for y in range(height):
        for x in range(width):
            if mask[y][x]:
                canvas[oy + y][ox + x] = body


# --------------------------------------------------------------------------
# per-language art
# --------------------------------------------------------------------------

def render_line(spec: dict, line_id: str, geom: Geometry, faces: dict,
                texts: list[str], palette: dict) -> list[bytes]:
    line = geom.lines[line_id]
    boxes = line["boxes"]
    if len(texts) != len(boxes):
        raise GenError(f"{line_id}: {len(texts)} strings for {len(boxes)} boxes")
    face = faces[str(spec["face"])]
    band = line["text_y1"] - line["text_y0"] + 1
    widths = [w for _, w in boxes]
    mode = str(face.get("mode", "proportional"))
    if mode == "proportional":
        _, masks = raster_proportional(texts, face, widths, band)
    elif mode == "cell":
        _, masks = raster_cell(texts, face, widths, band)
    else:
        raise GenError(f"unknown face mode {mode!r}")
    canvas = [[0] * (line["tiles"] * 8) for _ in range(len(line["rows"]) * 8)]
    for mask, (box_x, box_w) in zip(masks, boxes):
        paint(canvas, mask, box_x, box_w, line["text_y0"], line["text_y1"],
              int(palette["body"]), int(palette["shadow"]))
    return canvas_to_rows(canvas, line["tiles"], geom.tile_bytes)


def build_art(source: dict, geom: Geometry) -> dict[str, dict[str, list[bytes]]]:
    """language -> line_id -> [row blob, ...]; only languages with own art."""
    faces = source["face"]
    palette = source["palette"]
    baseline = {
        "line1": [bytes.fromhex(h) for h in source["baseline"]["en_line1_hex"]],
        "line2": [bytes.fromhex(h) for h in source["baseline"]["en_line2_hex"]],
    }
    art: dict[str, dict[str, list[bytes]]] = {"en": baseline}
    for lang, spec in source["language"].items():
        if lang not in LANGS:
            raise GenError(f"unknown language {lang!r}")
        if lang == "en":
            raise GenError("en art is the baseline and cannot be authored")
        if spec.get("keep") == "en":
            continue
        lines: dict[str, list[bytes]] = {}
        texts = [str(spec["line1_left"]), str(spec["line1_right"])]
        lines["line1"] = render_line(spec, "line1", geom, faces, texts, palette)
        line2 = spec.get("line2", "keep")
        if line2 != "keep":
            lines["line2"] = render_line(spec, "line2", geom, faces,
                                         [str(line2)], palette)
        art[lang] = lines
    return art


# --------------------------------------------------------------------------
# table text surgery
# --------------------------------------------------------------------------

BLOCK_RE = re.compile(r"^\[\[rom_patch\]\]$")
KEY_RE = re.compile(r"^([a-z0-9_]+)\s*=\s*(.*)$")


class Block:
    __slots__ = ("lines", "address", "width", "hexes")

    def __init__(self, lines: list[str]):
        self.lines = lines
        self.address = None
        self.width = None
        self.hexes: dict[str, str] = {}
        for line in lines:
            match = KEY_RE.match(line.strip())
            if not match:
                continue
            key, raw = match.group(1), match.group(2).strip()
            if key == "address":
                self.address = int(raw, 0)
            elif key.endswith("_hex") or key == "source_hex":
                self.hexes[key] = raw.strip('"')
        if "source_hex" in self.hexes:
            self.width = len(self.hexes["source_hex"]) // 2


def split_rom_patch_blocks(text: str, rects: list[tuple[int, int]]) -> tuple[str, int, int]:
    """Slice every [[rom_patch]] out of the caption rectangles."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    split_count = 0
    removed_count = 0
    while i < len(lines):
        if not BLOCK_RE.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        start = i
        i += 1
        while i < len(lines) and not lines[i].startswith("["):
            i += 1
        end = i
        # trailing blank lines belong after the block
        body = lines[start:end]
        trailing = 0
        while body and body[-1].strip() == "":
            trailing += 1
            body.pop()
        block = Block(body)
        if block.address is None or block.width is None:
            out.extend(body)
            out.extend([""] * trailing)
            continue
        keep = subtract_rects(block.address, block.width, rects)
        if len(keep) == 1 and keep[0] == (block.address, block.width):
            out.extend(body)
            out.extend([""] * trailing)
            continue
        if not keep:
            removed_count += 1
            # drop the block and its blank separator entirely
            continue
        split_count += 1
        for index, (addr, width) in enumerate(keep):
            offset = addr - block.address
            for line in body:
                match = KEY_RE.match(line.strip())
                if not match:
                    out.append(line)
                    continue
                key, _ = match.group(1), match.group(2)
                if key == "address":
                    out.append(f"address = 0x{addr:06x}")
                elif key.endswith("_hex"):
                    value = block.hexes[key]
                    out.append(f'{key} = "'
                               f'{value[offset * 2:(offset + width) * 2]}"')
                else:
                    out.append(line)
            if index != len(keep) - 1:
                out.append("")
        out.extend([""] * trailing)
    return "\n".join(out), split_count, removed_count


def subtract_rects(address: int, width: int,
                   rects: list[tuple[int, int]]) -> list[tuple[int, int]]:
    spans = [(address, address + width)]
    for start, length in rects:
        stop = start + length
        next_spans = []
        for a, b in spans:
            if b <= start or stop <= a:
                next_spans.append((a, b))
                continue
            if a < start:
                next_spans.append((a, start))
            if stop < b:
                next_spans.append((stop, b))
        spans = next_spans
    return [(a, b - a) for a, b in spans]


# --------------------------------------------------------------------------
# section emission
# --------------------------------------------------------------------------

def generate_section(root: Path, source: dict, geom: Geometry,
                     rom: bytes, table: dict, baseline_only: bool = False) -> str:
    """Emit the generated section.

    baseline_only=True emits the English baseline art for every language,
    i.e. the caption rectangles as the pre-split table produced them. That
    variant is what the ROM-image invariant is measured against, so the
    table restructuring is proven byte-neutral independently of the
    deliberate per-language art change layered on top of it.
    """
    art = ({"en": build_art(source, geom)["en"]} if baseline_only
           else build_art(source, geom))
    explicit = [lang for lang in LANGS
                if lang != "en" and "en" not in language_chain(table, lang)]

    lines = [
        BEGIN_MARK,
        "# Per-language intro caption tile art ('After Colony 195'), authored",
        "# from translations/endless_duel_intro_caption.toml by",
        "# scripts/generate_intro_caption_patch.py. These entries own the two",
        "# caption rectangles in the uncompressed OBJ bank whole; the IPS",
        "# fragments that used to overlap them were split by --write.",
        "# source_hex is the untouched Japanese ROM. Do not hand-edit.",
        "",
    ]
    for line_id in ("line1", "line2"):
        line = geom.lines[line_id]
        for row_index, address in enumerate(line["rows"]):
            span = line["span"]
            source_hex = rom[address:address + span].hex()
            lines.append("[[rom_patch]]")
            lines.append(f"# intro caption {line_id} tile row {row_index}"
                         f" ({line['tiles']} tiles)")
            lines.append(f"address = 0x{address:06x}")
            lines.append(f'source_hex = "{source_hex}"')
            for lang in LANGS:
                blob = None
                if lang in art and line_id in art[lang]:
                    blob = art[lang][line_id][row_index]
                elif lang in explicit:
                    blob = art["en"][line_id][row_index]
                if blob is None:
                    continue
                if len(blob) != span:
                    raise GenError(
                        f"{lang} {line_id} row {row_index}: {len(blob)} bytes,"
                        f" expected {span}")
                lines.append(f'{lang}_hex = "{blob.hex()}"')
            lines.append("")
    lines.append(END_MARK)
    return "\n".join(lines)


# Sentinel left behind by strip_section so replace_section can put the
# regenerated section back exactly where the old one lived. Without it the
# stripped text has no markers to find and the section is appended at EOF,
# silently RELOCATING it past every generated section that followed it - and
# rom_patch file order is application order.
PLACEHOLDER = "# CAPTION SECTION PLACEHOLDER (generator internal)"


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
    pattern = re.compile(
        r"\n*" + re.escape(BEGIN_MARK) + r".*?" + re.escape(END_MARK) + r"\n*",
        re.S)
    return pattern.sub("\n\n" + PLACEHOLDER + "\n\n", text)


# --------------------------------------------------------------------------
# invariant
# --------------------------------------------------------------------------

def digests(table: dict, rom: bytes) -> dict[str, str]:
    return {lang: hashlib.sha256(apply_rom_patches(table, lang, rom)).hexdigest()
            for lang in ("en", "es")}


def check_invariant(source: dict, table: dict, rom: bytes) -> None:
    expected = source["baseline"]["rom_sha256"]
    actual = digests(table, rom)
    for lang in ("en", "es"):
        if actual[lang] != str(expected[lang]):
            raise GenError(
                f"ROM image invariant FAILED for {lang}: table produces "
                f"{actual[lang]}, pre-split table produced {expected[lang]}")
    print("rom image invariant ok: en + es byte-identical to the pre-split table")


def check_no_overlap(table: dict, rects: list[tuple[int, int]]) -> None:
    seen = set()
    for patch in table.get("rom_patch", []):
        address = int(patch["address"])
        width = len(bytes.fromhex(str(patch["source_hex"])))
        if (address, width) in rects:
            if (address, width) in seen:
                raise GenError(
                    f"caption rectangle 0x{address:06x}+{width} is claimed "
                    f"by more than one rom_patch")
            seen.add((address, width))
            continue
        if subtract_rects(address, width, rects) != [(address, width)]:
            raise GenError(
                f"rom_patch 0x{address:06x}+{width} still overlaps a caption "
                f"rectangle")
    missing = sorted(set(rects) - seen)
    if missing:
        raise GenError("caption rectangles unclaimed: " +
                       ", ".join(f"0x{a:06x}+{n}" for a, n in missing))
    print("non-overlap invariant ok: no fragment intersects the caption rects")


# --------------------------------------------------------------------------
# previews
# --------------------------------------------------------------------------

def write_previews(root: Path, source: dict, geom: Geometry, table: dict,
                   rom: bytes, out_dir: Path) -> list[Path]:
    Image, _, _ = _pil()
    palette = [tuple(int(c) for c in rgb)
               for rgb in source["preview"]["palette_rgb"]]
    scale = int(source["preview"].get("scale", 3))
    background = tuple(int(c) for c in source["preview"]["background_rgb"])
    width = int(source["preview"].get("screen_width", 256))
    out_dir.mkdir(parents=True, exist_ok=True)

    # true screen extent of the caption, so the 16px x112-127 gap between the
    # two line-1 boxes shows up in the preview exactly as it does in game
    top = min(line["screen_y0"] for line in geom.lines.values())
    bottom = max(line["screen_y0"] + len(line["rows"]) * 8 - 1
                 for line in geom.lines.values())
    height = bottom - top + 1

    written = []
    for lang in LANGS:
        image_rom = apply_rom_patches(table, lang, rom)
        image = Image.new("RGB", (width, height), background)
        pixels = image.load()
        for line in geom.lines.values():
            rows = [image_rom[a:a + line["span"]] for a in line["rows"]]
            canvas = rows_to_canvas(rows, line["tiles"])
            for (box_x, box_w), screen_x in zip(line["boxes"],
                                                line["screen_x"]):
                for y, row in enumerate(canvas):
                    sy = line["screen_y0"] + y - top
                    for x in range(box_w):
                        value = row[box_x + x]
                        if value and 0 <= screen_x + x < width:
                            pixels[screen_x + x, sy] = palette[value]
        path = out_dir / f"intro_caption_{lang}.png"
        image.resize((width * scale, height * scale),
                     Image.NEAREST).save(path)
        written.append(path)
    return written


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true",
                        help="rewrite the generated section and split the "
                             "overlapping IPS fragments")
    parser.add_argument("--check", action="store_true",
                        help="fail if the table differs or an invariant breaks")
    parser.add_argument("--previews", action="store_true",
                        help="also write per-language preview PNGs")
    args = parser.parse_args()

    root = repo_root()
    table_path = root / "translations" / "endless_duel.toml"
    source_path = root / "translations" / "endless_duel_intro_caption.toml"
    rom_path = root / ROM_NAME
    if not rom_path.is_file():
        raise GenError(f"reference ROM missing: {rom_path}")
    rom = rom_path.read_bytes()
    if len(rom) != ROM_SIZE:
        raise GenError(f"{rom_path.name}: expected {ROM_SIZE} bytes, "
                       f"got {len(rom)}")

    source = load_toml(source_path)
    geom = Geometry(source["geometry"])
    rects = geom.rects()

    text = table_path.read_text(encoding="utf-8")

    if args.write:
        stripped = strip_section(text)
        new_text, split_count, removed_count = split_rom_patch_blocks(
            stripped, rects)
        old_table = tomllib.loads(text)
        section = generate_section(root, source, geom, rom, old_table)
        baseline_section = generate_section(root, source, geom, rom,
                                            old_table, baseline_only=True)
        split_text = new_text
        new_text = replace_section(split_text, section)
        if not new_text.endswith("\n"):
            new_text += "\n"
        new_table = tomllib.loads(new_text)
        # Measure the restructuring alone: strip the per-language art out of
        # both the incoming and the outgoing table (every language gets the
        # English baseline) and require the en/es 2MB images to be identical
        # to each other and to the digests recorded from the pre-split table.
        before = digests(tomllib.loads(replace_section(text, baseline_section)),
                         rom)
        after = digests(tomllib.loads(replace_section(split_text,
                                                      baseline_section)), rom)
        recorded = source["baseline"]["rom_sha256"]
        for lang in ("en", "es"):
            if before[lang] != after[lang]:
                raise GenError(
                    f"REFUSING to write: restructured table changes the "
                    f"{lang} ROM image ({before[lang]} -> {after[lang]})")
            if str(recorded[lang]) != after[lang]:
                raise GenError(
                    f"REFUSING to write: baseline.rom_sha256.{lang} in "
                    f"{source_path.name} is {recorded[lang]}, the "
                    f"restructured table produces {after[lang]}")
        print("rom image invariant ok: en + es byte-identical after the "
              "fragment split")
        check_no_overlap(new_table, rects)
        if new_text != text:
            table_path.write_text(new_text, encoding="utf-8", newline="\n")
            print(f"updated {table_path}")
        else:
            print("table already up to date")
        print(f"fragments split: {split_count}, removed: {removed_count}")
        if args.previews:
            for path in write_previews(root, source, geom, new_table, rom,
                                       root / "translations" /
                                       "title_menu_previews"):
                print(f"preview {path}")
        return 0

    table = tomllib.loads(text)
    section = generate_section(root, source, geom, rom, table)
    pattern = re.compile(
        re.escape(BEGIN_MARK) + r".*?" + re.escape(END_MARK), re.S)
    match = pattern.search(text)
    if not match:
        raise GenError("generated intro caption section missing from table")
    if match.group(0) != section:
        raise GenError("generated intro caption section differs from table")
    check_no_overlap(table, rects)
    baseline_section = generate_section(root, source, geom, rom, table,
                                        baseline_only=True)
    check_invariant(source, tomllib.loads(replace_section(
        text, baseline_section)), rom)
    print(f"intro caption patches: {section.count('[[rom_patch]]')} rows x "
          f"{len(LANGS)} languages ok")
    if args.previews:
        for path in write_previews(root, source, geom, table, rom,
                                   root / "translations" /
                                   "title_menu_previews"):
            print(f"preview {path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
