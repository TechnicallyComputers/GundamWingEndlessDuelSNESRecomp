#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Complex-script (Thai) text rasterisation for the localization generators.

WHY: Pillow only performs complex-script layout when it is built against
libraqm, and the Pillow in this tree is not (``PIL.features.check("raqm")`` is
False).  Without it, Thai vowel and tone marks advance as spacing glyphs instead
of stacking over their base consonant, which makes the line unreadable rather
than merely ugly.  GDI's Uniscribe path shapes Thai correctly, so this module
shells out to ``scripts/render_text_gdi.ps1`` and reads the PNGs back.

BUILD-HOST DEPENDENCY.  This is a GENERATION-time dependency only: Windows, GDI
and an installed Thai font (Leelawadee UI).  Everything it produces is baked
into ``translations/endless_duel.toml`` as tile bytes, so the shipped artifact
depends on none of it.

BAND COMPRESSION.  A Thai line needs 17-20 raster rows at a fixed baseline for
em sizes where the base consonants are still legible (the loops that separate
ก/ถ/ภ, ด/ต, บ/ป, พ/ผ, ม/น collapse below about 9px), but the dialogue line is
exactly 16 rows.  The fix, which is what hand-made Thai pixel fonts do, is to
render at the larger em and squash the above-mark band and the below-vowel band
2:1 while keeping the base-consonant band at full height.  The band boundaries
are measured from the font itself (``BandGeometry.measure``), never hardcoded,
and are the SAME for every line, so the baseline is fixed -- per-line centring
would make the text bounce.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

SCRIPT = Path(__file__.replace("\\", "/")).resolve().parent / "render_text_gdi.ps1"

ORIGIN_X, ORIGIN_Y = 4, 8
CANVAS_W, CANVAS_H = 512, 48

# Probe strings the band geometry is measured from.
#  * base: x-height consonants only -- no marks, and no tall letters.  This is
#    the band that must survive at full height, because it carries the loops
#    that tell the consonants apart.
#  * above: everything that can rise over the x-height -- the marks, the tall
#    ascenders (ป ฟ ฬ), the pre-base vowels (เ แ โ ใ ไ), an ascender carrying a
#    mark (ฟื้, ป้) and the double stacks (ที่, เชื่อ).  All of that is squashed;
#    it is the accepted 16px compromise and it is what Thai pixel fonts do.
#  * below: below-base vowels and the deepest descenders (ผู้, ญู, ฏฺ).
BASE_PROBE = "กขคงจชซดตนบมยรลวสอฮ"
ABOVE_PROBE = "ที่ เชื่อ ฟื้ ป้ นึ่ง ก็ ห์ แโใไฬ"
BELOW_PROBE = "ผู้ ญู ฏฺ ดุ ฐู"

PIX_BG, PIX_EDGE, PIX_BODY = 1, 2, 3


def quantize(pixels, x0: int, y0: int, width: int, height: int) -> list[list[int]]:
    """Grey -> the surfaces' 2bpp ramp: >=128 body, >=48 edge, else background."""
    return [[PIX_BODY if pixels[x0 + x, y0 + y] >= 128
             else (PIX_EDGE if pixels[x0 + x, y0 + y] >= 48 else PIX_BG)
             for x in range(width)]
            for y in range(height)]


class GdiRenderer:
    """Batched GDI text rasteriser for one (font file, pixel size) pair."""

    def __init__(self, font_file: str, size: int):
        self.font_file = str(font_file)
        self.size = int(size)
        self._cache: dict[str, object] = {}

    def render(self, texts) -> None:
        """Rasterise every text not already cached, in ONE PowerShell call."""
        from PIL import Image, ImageChops
        want = []
        for text in texts:
            if text not in self._cache and text not in want:
                want.append(text)
        if not want:
            return
        with tempfile.TemporaryDirectory(prefix="gdi_text_") as tmp:
            tmp_path = Path(tmp)
            jobs = [{"name": f"j{index:05d}", "text": text,
                     "font_file": self.font_file, "size": self.size,
                     "origin_x": ORIGIN_X, "origin_y": ORIGIN_Y,
                     "width": CANVAS_W, "height": CANVAS_H}
                    for index, text in enumerate(want)]
            jobs_path = tmp_path / "jobs.json"
            jobs_path.write_text(json.dumps(jobs, ensure_ascii=False),
                                 encoding="utf-8", newline="\n")
            out_dir = tmp_path / "out"
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(SCRIPT), "-JobsJson", str(jobs_path),
                 "-OutDir", str(out_dir)],
                capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(
                    "GDI text render failed:\n" + result.stdout + result.stderr)
            for job, text in zip(jobs, want):
                path = out_dir / f"{job['name']}.png"
                if not path.is_file():
                    raise RuntimeError(f"GDI renderer produced no {path.name}")
                image = Image.open(path).convert("RGB")
                red, green, blue = image.split()
                mask = ImageChops.lighter(ImageChops.lighter(red, green), blue)
                mask.load()
                self._cache[text] = mask

    def mask(self, text: str):
        """The greyscale ink mask of `text` on the full canvas."""
        if text not in self._cache:
            self.render([text])
        return self._cache[text]

    def ink_box(self, text: str):
        """(left, top, right, bottom) of the ink, or None for an empty render."""
        return self.mask(text).getbbox()


@dataclass(frozen=True)
class BandGeometry:
    """Canvas rows delimiting the above / base / below bands of a Thai line."""

    ascent_top: int
    base_top: int
    baseline: int
    descent_bottom: int
    height: int          # rows the compressed line must occupy
    factor: int = 2      # squash factor for the outer bands

    @property
    def above_rows(self) -> int:
        return max(0, (self.base_top - self.ascent_top) // self.factor)

    @property
    def base_rows(self) -> int:
        return self.baseline - self.base_top

    @property
    def below_rows(self) -> int:
        return max(0, (self.descent_bottom - self.baseline) // self.factor)

    @property
    def total_rows(self) -> int:
        return self.above_rows + self.base_rows + self.below_rows

    @property
    def top_pad(self) -> int:
        """Constant top padding -- this is what fixes the baseline."""
        return max(0, (self.height - self.total_rows) // 2)

    @classmethod
    def measure(cls, renderer: GdiRenderer, height: int,
                factor: int = 2) -> "BandGeometry":
        """Derive the bands from the font's own rendering of the probes."""
        renderer.render([BASE_PROBE, ABOVE_PROBE, BELOW_PROBE])
        base = renderer.ink_box(BASE_PROBE)
        above = renderer.ink_box(ABOVE_PROBE)
        below = renderer.ink_box(BELOW_PROBE)
        for name, box in (("base", base), ("above", above), ("below", below)):
            if box is None:
                raise ValueError(
                    f"{renderer.font_file} {renderer.size}px renders the {name} "
                    "probe empty -- the font has no Thai coverage")
        geometry = cls(ascent_top=min(above[1], base[1]),
                       base_top=base[1],
                       baseline=base[3],
                       descent_bottom=max(below[3], base[3]),
                       height=int(height), factor=int(factor))
        if geometry.total_rows > height:
            raise ValueError(
                f"{renderer.font_file} {renderer.size}px: compressed bands need "
                f"{geometry.total_rows} rows ({geometry.above_rows} above + "
                f"{geometry.base_rows} base + {geometry.below_rows} below), the "
                f"line is {height}")
        return geometry


def compressed_image(renderer: GdiRenderer, geometry: BandGeometry, text: str,
                     width: int):
    """`text` as a greyscale geometry.height x width band-compressed strip.

    The above-mark and below-vowel bands are squashed by geometry.factor and the
    base-consonant band is kept at full height, at a FIXED baseline.
    """
    from PIL import Image
    mask = renderer.mask(text)
    box = mask.getbbox()
    if box is not None:
        if box[1] < geometry.ascent_top or box[3] > geometry.descent_bottom:
            raise ValueError(
                f"{text!r} renders rows {box[1]}-{box[3] - 1}, outside the "
                f"measured band {geometry.ascent_top}-"
                f"{geometry.descent_bottom - 1}; the band probes do not cover "
                "this line's marks")
        if box[2] - ORIGIN_X > width:
            raise ValueError(
                f"{text!r} is {box[2] - ORIGIN_X}px wide, budget is {width}px")
    strip = mask.crop((ORIGIN_X, 0, ORIGIN_X + width, CANVAS_H))
    out = Image.new("L", (width, geometry.height), 0)

    def squashed(y0: int, y1: int, rows: int):
        if rows <= 0:
            return None
        return strip.crop((0, y0, width, y1)).resize(
            (width, rows), Image.LANCZOS)

    y = geometry.top_pad
    above = squashed(geometry.ascent_top, geometry.base_top, geometry.above_rows)
    if above is not None:
        out.paste(above, (0, y))
    y += geometry.above_rows
    out.paste(strip.crop((0, geometry.base_top, width, geometry.baseline)),
              (0, y))
    y += geometry.base_rows
    below = squashed(geometry.baseline, geometry.descent_bottom,
                     geometry.below_rows)
    if below is not None:
        out.paste(below, (0, y))
    return out


def compress_line(renderer: GdiRenderer, geometry: BandGeometry, text: str,
                  width: int) -> list[list[int]]:
    """`text` as a geometry.height x width grid of 2bpp palette indices."""
    image = compressed_image(renderer, geometry, text, width)
    return quantize(image.load(), 0, 0, width, geometry.height)


def cluster_mask(renderer: GdiRenderer, geometry: BandGeometry, text: str,
                 cell_width: int = 8, threshold: int = 110,
                 scratch: int = 64) -> list[int]:
    """One orthographic cluster as `geometry.height` 1-bit row masks.

    For the 8px-wide option/key-config font cell.  The cluster is rendered and
    band-compressed like a line, then cropped to its ink and, if it is wider
    than the cell, squeezed horizontally into it -- the same compromise the
    existing 8x8 CJK option masks make for whole Han characters.
    """
    from PIL import Image
    image = compressed_image(renderer, geometry, text, scratch)
    box = image.getbbox()
    if box is None:
        raise ValueError(f"{text!r} renders as an empty cluster cell")
    image = image.crop((box[0], 0, box[2], geometry.height))
    if image.width > cell_width:
        image = image.resize((cell_width, geometry.height), Image.LANCZOS)
    offset = (cell_width - image.width) // 2
    pixels = image.load()
    rows = []
    for y in range(geometry.height):
        mask = 0
        for x in range(image.width):
            if pixels[x, y] >= threshold:
                mask |= 1 << (cell_width - 1 - (x + offset))
        rows.append(mask)
    return rows


def ink_width(renderer: GdiRenderer, text: str) -> int:
    box = renderer.ink_box(text)
    return 0 if box is None else box[2] - ORIGIN_X
