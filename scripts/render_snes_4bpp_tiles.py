#!/usr/bin/env python3
"""Render SNES 4bpp tile ranges from the patched reference ROM images."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__.replace("\\", "/")).resolve().parent))

from analyze_reference_ips import apply_records, parse_ips, repo_root


RANGES = (
    (0x006F00, 0x007F00, "bank00_font_or_tiles"),
    (0x00E000, 0x010000, "bank00_large_font_or_tiles"),
    (0x015400, 0x016844, "bank01_font_or_tiles"),
)

PALETTE = (
    (0x00, 0x00, 0x00),
    (0x44, 0x44, 0x44),
    (0x77, 0x77, 0x77),
    (0xAA, 0xAA, 0xAA),
    (0xDD, 0xDD, 0xDD),
    (0xFF, 0xFF, 0xFF),
    (0x33, 0x66, 0x99),
    (0x55, 0x99, 0xCC),
    (0x88, 0xBB, 0xEE),
    (0xCC, 0x88, 0x44),
    (0xEE, 0xAA, 0x66),
    (0x88, 0xCC, 0x88),
    (0xAA, 0xEE, 0xAA),
    (0xCC, 0x88, 0xAA),
    (0xEE, 0xAA, 0xCC),
    (0xFF, 0xEE, 0xAA),
)


def decode_4bpp_tile(tile: bytes) -> list[list[int]]:
    if len(tile) != 32:
        raise ValueError("SNES 4bpp tiles are 32 bytes")
    pixels = [[0 for _ in range(8)] for _ in range(8)]
    for y in range(8):
        p0 = tile[y * 2]
        p1 = tile[y * 2 + 1]
        p2 = tile[16 + y * 2]
        p3 = tile[16 + y * 2 + 1]
        for x in range(8):
            bit = 7 - x
            pixels[y][x] = (
                ((p0 >> bit) & 1)
                | (((p1 >> bit) & 1) << 1)
                | (((p2 >> bit) & 1) << 2)
                | (((p3 >> bit) & 1) << 3)
            )
    return pixels


def put_rect(rgb: bytearray, width: int, x: int, y: int, w: int, h: int, color: tuple[int, int, int]) -> None:
    r, g, b = color
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            off = (yy * width + xx) * 3
            rgb[off:off + 3] = bytes((r, g, b))


def render_sheet(data: bytes, out_path: Path, columns: int, scale: int) -> None:
    tile_count = len(data) // 32
    rows = (tile_count + columns - 1) // columns
    cell = 8 * scale + 1
    width = columns * cell + 1
    height = rows * cell + 1
    rgb = bytearray([0x18, 0x18, 0x18] * width * height)

    for index in range(tile_count):
        tile = decode_4bpp_tile(data[index * 32:index * 32 + 32])
        ox = 1 + (index % columns) * cell
        oy = 1 + (index // columns) * cell
        for y in range(8):
            for x in range(8):
                color = PALETTE[tile[y][x]]
                put_rect(rgb, width, ox + x * scale, oy + y * scale, scale, scale, color)

    write_bmp(out_path, width, height, rgb)


def write_bmp(path: Path, width: int, height: int, rgb: bytes) -> None:
    row_size = ((width * 3 + 3) // 4) * 4
    pixel_size = row_size * height
    file_size = 14 + 40 + pixel_size
    header = b"BM" + struct.pack("<IHHI", file_size, 0, 0, 14 + 40)
    dib = struct.pack("<IIIHHIIIIII", 40, width, height, 1, 24, 0, pixel_size, 2835, 2835, 0, 0)
    rows = bytearray()
    for y in range(height - 1, -1, -1):
        row = bytearray()
        for x in range(width):
            r, g, b = rgb[(y * width + x) * 3:(y * width + x) * 3 + 3]
            row.extend((b, g, r))
        row.extend(b"\x00" * (row_size - len(row)))
        rows.extend(row)
    path.write_bytes(header + dib + rows)


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", default=str(root / "Shin Kidou Senki Gundam W - Endless Duel (J).smc"))
    parser.add_argument("--en-ips", required=True)
    parser.add_argument("--es-ips", required=True)
    parser.add_argument("--out", default=str(root / "translations" / "reference_tiles"))
    parser.add_argument("--columns", type=int, default=16)
    parser.add_argument("--scale", type=int, default=3)
    args = parser.parse_args()

    source = Path(args.rom).read_bytes()
    images = {
        "en": apply_records(source, parse_ips(Path(args.en_ips), -0x200), "en"),
        "es": apply_records(source, parse_ips(Path(args.es_ips), 0), "es"),
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for lang, image in images.items():
        for start, end, name in RANGES:
            out_path = out_dir / f"{lang}_{start:06x}_{end - 1:06x}_{name}.bmp"
            render_sheet(bytes(image[start:end]), out_path, args.columns, args.scale)
            print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
