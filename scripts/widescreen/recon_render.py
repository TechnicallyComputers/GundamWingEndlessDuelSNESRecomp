#!/usr/bin/env py -3
"""Offline SNES tile/tilemap renderer for the GWED widescreen recon.

Pure post-processing: it takes the VRAM + CGRAM blobs a recon bundle already
captured (`analysis/widescreen/recon/screens/<scene>/<scene>_{vram,cgram}.json`)
and rasterises them.  Nothing here talks to the game — no process, no port,
no arming, no timing.  That is the point: R3's margin candidates and R5's HUD
decode are *synthesis* questions, and synthesising them offline means they can
be re-derived from the committed bundle without another capture.

Mirrors `snesrecomp/runner/src/snes/ppu.c`:
  * tilemap quadrant arithmetic (see `ws_recon.decode_bg_map`, reused here),
  * 2bpp / 4bpp planar character decode (bitplane pairs, 8 rows of 2 bytes
    per plane pair, planes at +0x00 / +0x10 / +0x20 / +0x30 within the tile),
  * BGR555 -> RGB888 with the 5->8 bit replication ppu.c uses,
  * palette index 0 of every palette is transparent (drawn as the backdrop,
    CGRAM entry 0, or as a caller-supplied key colour).

PNG is written with zlib only (no Pillow on this box).
"""

from __future__ import annotations

import json
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ws_recon as R  # noqa: E402  (decode_bg_map / unhex are the shared bits)


# --------------------------------------------------------------------------
# bundle loading
# --------------------------------------------------------------------------

def scene_dir(scene: str) -> str:
    return os.path.join(R.OUT_ROOT, "screens", scene)


def load_vram(scene: str) -> bytes:
    p = os.path.join(scene_dir(scene), "%s_vram.json" % scene)
    with open(p) as fh:
        return R.unhex(json.load(fh)["hex"])


def load_cgram(scene: str) -> list:
    """CGRAM as 256 RGB888 tuples."""
    p = os.path.join(scene_dir(scene), "%s_cgram.json" % scene)
    with open(p) as fh:
        raw = R.unhex(json.load(fh)["hex"])
    out = []
    for i in range(256):
        w = raw[i * 2] | (raw[i * 2 + 1] << 8)
        out.append(bgr555(w))
    return out


def load_ppu(scene: str) -> dict:
    p = os.path.join(scene_dir(scene), "%s_ppu.json" % scene)
    with open(p) as fh:
        return json.load(fh)


def bgr555(w: int) -> tuple:
    r = (w & 0x1F) << 3
    g = ((w >> 5) & 0x1F) << 3
    b = ((w >> 10) & 0x1F) << 3
    # ppu.c replicates the top 3 bits into the low 3 so 0x1F maps to 0xFF.
    return (r | (r >> 5), g | (g >> 5), b | (b >> 5))


# --------------------------------------------------------------------------
# character decode
# --------------------------------------------------------------------------

def tile_pixels(vram: bytes, char_base_word: int, tile: int, bpp: int) -> list:
    """8x8 palette indices for one character.  rows[y][x], 0 = transparent."""
    words_per_tile = 8 * bpp // 2          # 4bpp -> 16 words, 2bpp -> 8
    base = (char_base_word + tile * words_per_tile) * 2
    rows = []
    for y in range(8):
        row = [0] * 8
        for plane_pair in range(bpp // 2):
            off = base + plane_pair * 16 + y * 2
            if off + 1 >= len(vram):
                continue
            lo = vram[off]
            hi = vram[off + 1]
            for x in range(8):
                bit = 7 - x
                v = ((lo >> bit) & 1) | (((hi >> bit) & 1) << 1)
                row[x] |= v << (plane_pair * 2)
        rows.append(row)
    return rows


def draw_map_region(vram, cgram, map_base_word, tiles_w, tiles_h,
                    char_base_word, bpp, tx0, tx1, ty0, ty1,
                    key=None, palette_base=0):
    """Rasterise tilemap cells [tx0,tx1] x [ty0,ty1] into an RGB row list."""
    rows_map = R.decode_bg_map(vram, map_base_word, tiles_w, tiles_h)
    w = (tx1 - tx0 + 1) * 8
    h = (ty1 - ty0 + 1) * 8
    keyc = key if key is not None else cgram[0]
    img = [[keyc] * w for _ in range(h)]
    colors_per_pal = 1 << bpp
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            e = rows_map[ty & (tiles_h - 1)][tx & (tiles_w - 1)]
            tile = e & 0x3FF
            pal = (e >> 10) & 7
            xflip = bool(e & 0x4000)
            yflip = bool(e & 0x8000)
            px = tile_pixels(vram, char_base_word, tile, bpp)
            for y in range(8):
                sy = (ty - ty0) * 8 + (7 - y if yflip else y)
                for x in range(8):
                    v = px[y][x]
                    if v == 0:
                        continue
                    sx = (tx - tx0) * 8 + (7 - x if xflip else x)
                    img[sy][sx] = cgram[palette_base
                                        + pal * colors_per_pal + v]
    return img


# --------------------------------------------------------------------------
# PNG
# --------------------------------------------------------------------------

def write_png(path: str, img: list, scale: int = 1) -> str:
    h = len(img)
    w = len(img[0]) if h else 0
    raw = bytearray()
    for y in range(h):
        for _ in range(scale):
            raw.append(0)
            for x in range(w):
                r, g, b = img[y][x]
                raw += bytes((r, g, b)) * scale
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w * scale, h * scale, 8, 2, 0, 0, 0)
    blob = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(blob)
    return path


def hcat(imgs: list, gap: int = 4, gapc=(255, 0, 255)) -> list:
    h = max(len(i) for i in imgs)
    out = []
    for y in range(h):
        row = []
        for n, im in enumerate(imgs):
            if n:
                row += [gapc] * gap
            row += im[y] if y < len(im) else [gapc] * len(im[0])
        out.append(row)
    return out


def vcat(imgs: list, gap: int = 4, gapc=(255, 0, 255)) -> list:
    w = max(len(i[0]) for i in imgs)
    out = []
    for n, im in enumerate(imgs):
        if n:
            out += [[gapc] * w for _ in range(gap)]
        for r in im:
            out.append(list(r) + [gapc] * (w - len(r)))
    return out


if __name__ == "__main__":
    print(__doc__)
