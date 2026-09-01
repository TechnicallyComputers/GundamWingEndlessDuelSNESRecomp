#!/usr/bin/env python3
"""Render title-menu translation overlay previews from a captured screenshot."""

from __future__ import annotations

import argparse
import struct
import sys
import tomllib
from pathlib import Path


LANGS = ("en", "es", "fr", "it", "pt", "tl", "id", "zh", "ko", "th")

DEFAULT_TITLE_GLYPHS = {
    " ": (5, (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00)),
    ".": (2, (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x03)),
    "A": (8, (0x7e, 0xff, 0xc3, 0xdf, 0xdf, 0xc3, 0xc3, 0xc3)),
    "B": (8, (0xfe, 0xff, 0xc3, 0xfe, 0xff, 0xc3, 0xff, 0xfe)),
    "C": (8, (0x7e, 0xff, 0xc0, 0xc0, 0xc0, 0xc0, 0xff, 0x7e)),
    "D": (8, (0xfe, 0xff, 0xc3, 0xc3, 0xc3, 0xc3, 0xdf, 0xde)),
    "E": (8, (0x7f, 0xff, 0xe0, 0xff, 0xff, 0xe0, 0xff, 0xff)),
    "F": (8, (0x7f, 0xff, 0xe0, 0xff, 0xff, 0xe0, 0xe0, 0xe0)),
    "G": (8, (0x7f, 0xff, 0xc0, 0xc0, 0xcf, 0xc3, 0xff, 0x7f)),
    "H": (8, (0xc3, 0xc3, 0xc3, 0xff, 0xff, 0xc3, 0xc3, 0xc3)),
    "I": (3, (0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07)),
    "J": (8, (0x1f, 0x1f, 0x06, 0x06, 0xc6, 0xc6, 0xfe, 0x7c)),
    "K": (8, (0xc3, 0xc6, 0xcc, 0xf8, 0xfc, 0xce, 0xc7, 0xc3)),
    "L": (7, (0x70, 0x70, 0x70, 0x70, 0x70, 0x70, 0x7f, 0x7f)),
    "M": (8, (0xfe, 0xff, 0xdb, 0xdb, 0xdb, 0xdb, 0xdb, 0xdb)),
    "N": (8, (0xfe, 0xff, 0xc3, 0xc3, 0xc3, 0xc3, 0xc3, 0xc3)),
    "O": (8, (0x7e, 0xff, 0xc3, 0xc3, 0xc3, 0xc3, 0xff, 0x7e)),
    "P": (8, (0xfe, 0xff, 0xc3, 0xdf, 0xde, 0xc0, 0xc0, 0xc0)),
    "Q": (8, (0x7e, 0xff, 0xc3, 0xc3, 0xdb, 0xcf, 0xff, 0x7b)),
    "R": (8, (0xfe, 0xff, 0xc3, 0xdf, 0xde, 0xc3, 0xc3, 0xc3)),
    "S": (8, (0x7f, 0xff, 0xc0, 0xfe, 0x7f, 0x03, 0xff, 0xfe)),
    "T": (7, (0x7f, 0x7f, 0x1c, 0x1c, 0x1c, 0x1c, 0x1c, 0x1c)),
    "U": (8, (0xc3, 0xc3, 0xc3, 0xc3, 0xc3, 0xc3, 0xff, 0x7e)),
    "V": (10, (0x303, 0x303, 0x387, 0x1ce, 0x0cc, 0x0fc, 0x078, 0x030)),
    "W": (8, (0xdb, 0xdb, 0xdb, 0xdb, 0xdb, 0xdb, 0xff, 0x66)),
    "X": (8, (0xc3, 0xe7, 0x7e, 0x3c, 0x3c, 0x7e, 0xe7, 0xc3)),
    "Y": (8, (0xc3, 0xc3, 0xc3, 0xc3, 0xff, 0x7e, 0x18, 0x18)),
    "Z": (8, (0xff, 0xff, 0x06, 0x0c, 0x18, 0x30, 0xff, 0xff)),
}


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


def read_bmp(path: Path) -> tuple[int, int, bytearray]:
    data = path.read_bytes()
    if data[:2] != b"BM":
        raise ValueError(f"{path} is not a BMP")
    offset = struct.unpack_from("<I", data, 10)[0]
    dib_size = struct.unpack_from("<I", data, 14)[0]
    if dib_size < 40:
        raise ValueError(f"{path}: unsupported BMP DIB header")
    width = struct.unpack_from("<i", data, 18)[0]
    height_signed = struct.unpack_from("<i", data, 22)[0]
    planes, bpp = struct.unpack_from("<HH", data, 26)
    compression = struct.unpack_from("<I", data, 30)[0]
    if planes != 1 or bpp != 24 or compression != 0:
        raise ValueError(f"{path}: expected uncompressed 24-bit BMP")
    top_down = height_signed < 0
    height = abs(height_signed)
    stride = ((width * 3 + 3) // 4) * 4
    rgb = bytearray(width * height * 3)
    for y in range(height):
        src_y = y if top_down else height - 1 - y
        row_off = offset + src_y * stride
        for x in range(width):
            b, g, r = data[row_off + x * 3:row_off + x * 3 + 3]
            dst = (y * width + x) * 3
            rgb[dst:dst + 3] = bytes((r, g, b))
    return width, height, rgb


def write_bmp(path: Path, width: int, height: int, rgb: bytes) -> None:
    row_size = ((width * 3 + 3) // 4) * 4
    pixel_size = row_size * height
    header = b"BM" + struct.pack("<IHHI", 14 + 40 + pixel_size, 0, 0, 14 + 40)
    dib = struct.pack("<IIiHHIIIIII", 40, width, -height, 1, 24, 0, pixel_size, 2835, 2835, 0, 0)
    rows = bytearray()
    for y in range(height):
        row = bytearray()
        for x in range(width):
            r, g, b = rgb[(y * width + x) * 3:(y * width + x) * 3 + 3]
            row.extend((b, g, r))
        row.extend(b"\x00" * (row_size - len(row)))
        rows.extend(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + dib + rows)


def put_rect(rgb: bytearray, width: int, height: int, x: int, y: int, w: int, h: int, color: tuple[int, int, int]) -> None:
    for yy in range(max(0, y), min(height, y + h)):
        for xx in range(max(0, x), min(width, x + w)):
            off = (yy * width + xx) * 3
            rgb[off:off + 3] = bytes(color)


def load_glyphs(path: Path | None) -> dict[str, tuple[int, int, tuple[int, ...]]]:
    glyphs = dict(DEFAULT_TITLE_GLYPHS)
    if path is None or not path.is_file():
        return {k: (v[0], len(v[1]), v[1]) for k, v in glyphs.items()}
    with path.open("rb") as f:
        source = tomllib.load(f)
    glyphs = {k: (v[0], len(v[1]), v[1]) for k, v in glyphs.items()}
    for entry in source.get("glyph", []):
        if "codepoint" in entry:
            value = str(entry["codepoint"])
            if value.startswith("U+"):
                value = value[2:]
            char = chr(int(value, 16))
        else:
            char = str(entry["char"])
        if len(char) != 1:
            raise ValueError(f"{path}: expected one glyph codepoint: {char!r}")
        width = int(entry["width"])
        height = int(entry.get("height", 8))
        rows = tuple(int(str(entry[f"row{i}"]), 16) for i in range(height))
        if width < 1 or width > 16 or height < 1 or height > 16:
            raise ValueError(f"{path}: glyph {char!r} has invalid size {width}x{height}")
        glyphs[char] = (width, height, rows)
        glyphs[char.upper()] = (width, height, rows)
    return glyphs


def text_size(text: str, scale: int, glyphs: dict[str, tuple[int, int, tuple[int, ...]]]) -> tuple[int, int]:
    width = 0
    height = 8
    for index, char in enumerate(text):
        try:
            glyph_width, glyph_height, _ = glyphs[char]
        except KeyError as exc:
            raise ValueError(f"unsupported title-menu preview glyph {char!r}") from exc
        if index:
            width += scale
        width += glyph_width * scale
        height = max(height, glyph_height)
    return width, height * scale


def draw_text(
    rgb: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    text: str,
    color: tuple[int, int, int],
    scale: int,
    glyphs: dict[str, tuple[int, int, tuple[int, ...]]],
) -> None:
    cursor = x
    for char in text:
        try:
            glyph_width, _, rows = glyphs[char]
        except KeyError as exc:
            raise ValueError(f"unsupported title-menu preview glyph {char!r}") from exc
        for gy, row in enumerate(rows):
            for gx in range(glyph_width):
                if row & (1 << (glyph_width - 1 - gx)):
                    put_rect(rgb, width, height, cursor + gx * scale, y + gy * scale, scale, scale, color)
        cursor += (glyph_width + 1) * scale


def overlay_language(
    base: tuple[int, int, bytearray],
    source: dict,
    lang: str,
    glyphs: dict[str, tuple[int, int, tuple[int, ...]]],
) -> tuple[int, int, bytearray]:
    width, height, base_rgb = base
    rgb = bytearray(base_rgb)
    palette = source["palette"]
    for label in source["label"]:
        text = str(label.get(lang, label["source"]))
        selected = bool(label.get("selected", False))
        x = int(label["x"])
        y = int(label["y"])
        w = int(label["width"])
        h = int(label["height"])
        fill = tuple(int(v) for v in palette["selected_fill" if selected else "inactive_fill"])
        text_color = tuple(int(v) for v in palette["selected_text" if selected else "inactive_text"])
        shadow = tuple(int(v) for v in palette["selected_shadow" if selected else "inactive_shadow"])
        put_rect(rgb, width, height, x, y, w, h, fill)
        scale = 1
        tw, th = text_size(text, scale, glyphs)
        tx = x + max(0, (w - tw) // 2)
        ty = int(label.get("text_y", y + max(0, (h - th) // 2)))
        if th > 8 * scale:
            ty = y + max(0, (h - th) // 2)
        draw_text(rgb, width, height, tx + scale, ty + scale, text, shadow, scale, glyphs)
        draw_text(rgb, width, height, tx, ty, text, text_color, scale, glyphs)
    return width, height, rgb


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", help="capture directory containing mode_menu.bmp, or the BMP itself")
    parser.add_argument("--source", default=str(root / "translations" / "endless_duel_title_menu.toml"))
    parser.add_argument("--glyphs", default=str(root / "translations" / "endless_duel_title_glyphs.toml"))
    parser.add_argument("--out", default=str(root / "translations" / "title_menu_previews"))
    parser.add_argument("--langs", default=",".join(LANGS))
    args = parser.parse_args()

    capture = Path(args.capture)
    screenshot = capture if capture.suffix.lower() == ".bmp" else capture / "mode_menu.bmp"
    with Path(args.source).open("rb") as f:
        source = tomllib.load(f)
    glyphs = load_glyphs(Path(args.glyphs) if args.glyphs else None)
    base = read_bmp(screenshot)
    out_dir = Path(args.out)
    for lang in [part.strip() for part in args.langs.split(",") if part.strip()]:
        if lang not in LANGS:
            raise ValueError(f"unsupported preview language {lang!r}")
        width, height, rgb = overlay_language(base, source, lang, glyphs)
        out = out_dir / f"title_menu_{lang}.bmp"
        write_bmp(out, width, height, rgb)
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
