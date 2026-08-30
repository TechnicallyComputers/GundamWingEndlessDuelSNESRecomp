#!/usr/bin/env python3
"""Render title-menu translation overlay previews from a captured screenshot."""

from __future__ import annotations

import argparse
import struct
import sys
import tomllib
from pathlib import Path


LANGS = ("en", "es", "fr", "it", "pt")

FONT_5X7 = {
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10011", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
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


def text_size(text: str, scale: int) -> tuple[int, int]:
    if not text:
        return 0, 7 * scale
    return (len(text) * 6 - 1) * scale, 7 * scale


def draw_text(rgb: bytearray, width: int, height: int, x: int, y: int, text: str, color: tuple[int, int, int], scale: int) -> None:
    cursor = x
    for char in text.upper():
        glyph = FONT_5X7.get(char)
        if glyph is None:
            raise ValueError(f"unsupported title-menu preview glyph {char!r}")
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit == "1":
                    put_rect(rgb, width, height, cursor + gx * scale, y + gy * scale, scale, scale, color)
        cursor += 6 * scale


def overlay_language(base: tuple[int, int, bytearray], source: dict, lang: str) -> tuple[int, int, bytearray]:
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
        scale = 2
        tw, th = text_size(text, scale)
        if tw > w - 4 or th > h - 2:
            scale = 1
            tw, th = text_size(text, scale)
        tx = x + max(0, (w - tw) // 2)
        ty = y + max(0, (h - th) // 2)
        draw_text(rgb, width, height, tx + scale, ty + scale, text, shadow, scale)
        draw_text(rgb, width, height, tx, ty, text, text_color, scale)
    return width, height, rgb


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", help="capture directory containing mode_menu.bmp, or the BMP itself")
    parser.add_argument("--source", default=str(root / "translations" / "endless_duel_title_menu.toml"))
    parser.add_argument("--out", default=str(root / "translations" / "title_menu_previews"))
    parser.add_argument("--langs", default=",".join(LANGS))
    args = parser.parse_args()

    capture = Path(args.capture)
    screenshot = capture if capture.suffix.lower() == ".bmp" else capture / "mode_menu.bmp"
    with Path(args.source).open("rb") as f:
        source = tomllib.load(f)
    base = read_bmp(screenshot)
    out_dir = Path(args.out)
    for lang in [part.strip() for part in args.langs.split(",") if part.strip()]:
        if lang not in LANGS:
            raise ValueError(f"unsupported preview language {lang!r}")
        width, height, rgb = overlay_language(base, source, lang)
        out = out_dir / f"title_menu_{lang}.bmp"
        write_bmp(out, width, height, rgb)
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
