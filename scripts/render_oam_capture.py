#!/usr/bin/env python3
"""Render the captured SNES OBJ/OAM layer from a VRAM/PPU/CGRAM bundle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__.replace("\\", "/")).resolve().parent))

from render_vram_bg_capture import cgram_colors, decode_4bpp_tile, read_hex_json, write_bmp


OBJ_BASE_BY_OBSEL = [0x0000, 0x4000, 0x8000, 0xC000, 0x10000, 0x14000, 0x18000, 0x1C000]
OBJ_NAME_SELECT = [0x0000, 0x2000, 0x4000, 0x6000]
OBJ_SIZES = [
    ((8, 8), (16, 16)),
    ((8, 8), (32, 32)),
    ((8, 8), (64, 64)),
    ((16, 16), (32, 32)),
    ((16, 16), (64, 64)),
    ((32, 32), (64, 64)),
    ((16, 32), (32, 64)),
    ((16, 32), (32, 32)),
]


def parse_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise TypeError(value)


def put_pixel(rgb: bytearray, width: int, x: int, y: int, color: tuple[int, int, int]) -> None:
    if x < 0 or y < 0 or x >= width or y >= 224:
        return
    off = (y * width + x) * 3
    rgb[off:off + 3] = bytes(color)


def render_obj(capture_dir: Path, out: Path) -> None:
    vram = read_hex_json(capture_dir / "vram.json")
    cgram = read_hex_json(capture_dir / "cgram.json")
    oam = read_hex_json(capture_dir / "oam.json")
    ppu = json.loads((capture_dir / "ppu_state.json").read_text(encoding="ascii"))
    colors = cgram_colors(cgram)
    obsel = parse_int(ppu["obsel"])
    base = OBJ_BASE_BY_OBSEL[obsel & 0x07]
    name_select = OBJ_NAME_SELECT[(obsel >> 3) & 0x03]
    small_size, large_size = OBJ_SIZES[(obsel >> 5) & 0x07]
    rgb = bytearray([0, 0, 0] * 256 * 224)

    high = oam[512:544]
    for index in range(127, -1, -1):
        off = index * 4
        x = oam[off]
        y = oam[off + 1]
        tile_no = oam[off + 2]
        attr = oam[off + 3]
        hi = (high[index // 4] >> ((index % 4) * 2)) & 0x03
        x |= (hi & 0x01) << 8
        if x >= 256:
            x -= 512
        large = bool(hi & 0x02)
        sprite_w, sprite_h = large_size if large else small_size
        palette = (attr >> 1) & 0x07
        hflip = bool(attr & 0x40)
        vflip = bool(attr & 0x80)

        tiles_w = sprite_w // 8
        tiles_h = sprite_h // 8
        for tile_y in range(tiles_h):
            for tile_x in range(tiles_w):
                source_x = tiles_w - 1 - tile_x if hflip else tile_x
                source_y = tiles_h - 1 - tile_y if vflip else tile_y
                source_tile = tile_no + source_x + source_y * 16
                tile_base = base + (name_select if source_tile >= 256 else 0)
                tile = decode_4bpp_tile(vram, tile_base + (source_tile & 0xFF) * 32)
                for py in range(8):
                    sy = 7 - py if vflip else py
                    for px in range(8):
                        sx = 7 - px if hflip else px
                        color_index = tile[sy][sx]
                        if color_index == 0:
                            continue
                        put_pixel(
                            rgb,
                            256,
                            x + tile_x * 8 + px,
                            y + tile_y * 8 + py,
                            colors[128 + palette * 16 + color_index],
                        )

    write_bmp(out, 256, 224, rgb)
    print(
        f"{out}: obsel=0x{obsel:02x} base=0x{base:05x} "
        f"name_select=0x{name_select:04x} sizes={small_size}/{large_size}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture_dir")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    capture_dir = Path(args.capture_dir)
    out = Path(args.out) if args.out else capture_dir / "obj.bmp"
    render_obj(capture_dir, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
