#!/usr/bin/env python3
"""Render SNES BG layers from a captured VRAM/PPU/CGRAM bundle."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path


def read_hex_json(path: Path) -> bytes:
    data = json.loads(path.read_text(encoding="ascii"))
    return bytes.fromhex(data["hex"])


def parse_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise TypeError(value)


def cgram_colors(cgram: bytes) -> list[tuple[int, int, int]]:
    colors = []
    for i in range(0, len(cgram), 2):
        word = cgram[i] | (cgram[i + 1] << 8)
        r = (word & 0x1F) * 255 // 31
        g = ((word >> 5) & 0x1F) * 255 // 31
        b = ((word >> 10) & 0x1F) * 255 // 31
        colors.append((r, g, b))
    return colors


def decode_4bpp_tile(vram: bytes, offset: int) -> list[list[int]]:
    if offset < 0 or offset + 32 > len(vram):
        return [[0 for _ in range(8)] for _ in range(8)]
    tile = vram[offset:offset + 32]
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


def decode_2bpp_tile(vram: bytes, offset: int) -> list[list[int]]:
    if offset < 0 or offset + 16 > len(vram):
        return [[0 for _ in range(8)] for _ in range(8)]
    tile = vram[offset:offset + 16]
    pixels = [[0 for _ in range(8)] for _ in range(8)]
    for y in range(8):
        p0 = tile[y * 2]
        p1 = tile[y * 2 + 1]
        for x in range(8):
            bit = 7 - x
            pixels[y][x] = ((p0 >> bit) & 1) | (((p1 >> bit) & 1) << 1)
    return pixels


def put_pixel(rgb: bytearray, width: int, x: int, y: int, color: tuple[int, int, int]) -> None:
    if x < 0 or y < 0:
        return
    off = (y * width + x) * 3
    if off < 0 or off + 3 > len(rgb):
        return
    rgb[off:off + 3] = bytes(color)


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


def render_bg(vram: bytes, cgram: bytes, ppu: dict, bg: int, out: Path) -> None:
    bg_sc = parse_int(ppu["bgXsc"][bg - 1])
    bgmode = parse_int(ppu["bgmode"]) & 0x07
    bgnba = parse_int(ppu["bgTileAdr"])
    bg12nba = (bgnba >> 8) & 0xFF
    bg34nba = bgnba & 0xFF
    if bg == 1:
        tile_base_word = (bg12nba & 0x0F) * 0x1000
    elif bg == 2:
        tile_base_word = ((bg12nba >> 4) & 0x0F) * 0x1000
    elif bg == 3:
        tile_base_word = (bg34nba & 0x0F) * 0x1000
    else:
        tile_base_word = ((bg34nba >> 4) & 0x0F) * 0x1000
    tile_base = tile_base_word * 2
    map_base = (bg_sc & 0xFC) << 9
    size_mode = bg_sc & 0x03
    map_width_tiles = 64 if size_mode in (1, 3) else 32
    map_height_tiles = 64 if size_mode in (2, 3) else 32
    width = min(map_width_tiles * 8, 512)
    height = min(map_height_tiles * 8, 256)
    colors = cgram_colors(cgram)
    rgb = bytearray([0, 0, 0] * width * height)
    bpp = 2 if bgmode == 1 and bg == 3 else 4
    tile_size = 16 if bpp == 2 else 32

    for ty in range(height // 8):
        for tx in range(width // 8):
            screen = 0
            local_x = tx
            local_y = ty
            if tx >= 32:
                screen += 1
                local_x -= 32
            if ty >= 32:
                screen += 2 if size_mode == 3 else 1
                local_y -= 32
            word_off = map_base + screen * 0x800 + ((local_y * 32 + local_x) * 2)
            if word_off + 2 > len(vram):
                continue
            word = vram[word_off] | (vram[word_off + 1] << 8)
            tile_no = word & 0x03FF
            palette_no = (word >> 10) & 0x07
            hflip = bool(word & 0x4000)
            vflip = bool(word & 0x8000)
            tile = (
                decode_2bpp_tile(vram, tile_base + tile_no * tile_size)
                if bpp == 2
                else decode_4bpp_tile(vram, tile_base + tile_no * tile_size)
            )
            for py in range(8):
                sy = 7 - py if vflip else py
                for px in range(8):
                    sx = 7 - px if hflip else px
                    color_index = tile[sy][sx]
                    if color_index == 0:
                        continue
                    palette_offset = palette_no * (4 if bpp == 2 else 16)
                    put_pixel(
                        rgb,
                        width,
                        tx * 8 + px,
                        ty * 8 + py,
                        colors[palette_offset + color_index],
                    )

    write_bmp(out, width, height, rgb)
    print(
        f"{out}: bg={bg} map_base=0x{map_base:04x} "
        f"tile_base=0x{tile_base:04x} bpp={bpp} size={map_width_tiles}x{map_height_tiles}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture_dir")
    parser.add_argument("--bg", type=int, default=1, choices=(1, 2, 3, 4))
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    capture_dir = Path(args.capture_dir)
    vram = read_hex_json(capture_dir / "vram.json")
    cgram = read_hex_json(capture_dir / "cgram.json")
    ppu = json.loads((capture_dir / "ppu_state.json").read_text(encoding="ascii"))
    out = Path(args.out) if args.out else capture_dir / f"bg{args.bg}.bmp"
    render_bg(vram, cgram, ppu, args.bg, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
