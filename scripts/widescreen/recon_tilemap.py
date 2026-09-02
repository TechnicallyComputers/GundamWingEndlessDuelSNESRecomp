#!/usr/bin/env py -3
"""R3 addendum + R5/R7 tilemap synthesis for the GWED widescreen recon.

Everything here is OFFLINE post-processing of a recon bundle
(`analysis/widescreen/recon/screens/<scene>/`).  No process, no port, no
timing — which is the point: the "what would the margins look like" question
is a synthesis question, and answering it from the committed VRAM/CGRAM dump
means the owner can re-derive the same three candidate renders without a
capture, and a later implementation can be diffed against them.

What it produces under `analysis/widescreen/recon/`:

  margin_candidates/*.png   342x224 renders of the attract stage with three
                            different margin policies per world layer, plus
                            per-layer isolations, so "which looks continuous"
                            is decided by eye AND by `ws_metrics.edge_score`
                            at the old 4:3 boundaries x=43 and x=299.
  tilemap.json              the R3 answers: BG2 wrap seamlessness, the three
                            candidates' seam scores, the BG1/BG3 map overlap,
                            and which BG3 map rows are actually displayed in
                            each scene (so nobody later writes BG1 margin
                            tiles into a live BG3 row).
  hud.json / hud_spec.json  R5 (written by --hud).

Margin geometry: the 16:9 frame is 342 px = 256 + 2*43 (`ceil_even(256*4/3)`
at the 7:6 CRT pixel aspect, `runner/src/desktop/display_aspect.h`).  Native
pixel p maps to dest p+43, so the left margin is native x in [-43,0) and the
right margin is native x in [256,299).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import recon_render as RR  # noqa: E402
import ws_metrics as M  # noqa: E402
import ws_recon as R  # noqa: E402

EXTRA = 43
FRAME_W = 256 + 2 * EXTRA          # 342
NATIVE_X0 = -EXTRA                 # first native column the wide frame shows
NATIVE_X1 = 256 + EXTRA            # one past the last


# --------------------------------------------------------------------------
# margin policies
# --------------------------------------------------------------------------

def pol_wrap(x: int, w: int, **kw) -> int:
    """Natural tilemap wrap — what the PPU itself would do."""
    return x % w


def pol_mirror_map(x: int, w: int, **kw) -> int:
    """Reflect about the map's own edges: -1 -> 0, -2 -> 1, w -> w-1, ..."""
    if 0 <= x < w:
        return x
    if x < 0:
        return min(-1 - x, w - 1)
    return max(2 * w - 1 - x, 0)


def pol_mirror_bounds(x: int, w: int, lo: int = 0, hi: int = 0, **kw) -> int:
    """Reflect about an authored content bound [lo, hi).

    For BG1 the authored stage art is map columns 8..55, i.e. world px
    [64, 448) — exactly the camera's reach.  Reflecting about those edges is
    the `x < 64 -> 127 - x` / `x >= 448 -> 895 - x` rule the issue names.
    """
    if lo <= x < hi:
        return x % w
    if x < lo:
        return (2 * lo - 1 - x) % w
    return (2 * hi - 1 - x) % w


def pol_clamp_bounds(x: int, w: int, lo: int = 0, hi: int = 0, **kw) -> int:
    """Repeat the authored edge column outward (the 'extend' policy)."""
    if x < lo:
        return lo % w
    if x >= hi:
        return (hi - 1) % w
    return x % w


POLICIES = dict(wrap=pol_wrap, mirror_map=pol_mirror_map,
                mirror_bounds=pol_mirror_bounds,
                clamp_bounds=pol_clamp_bounds)


# --------------------------------------------------------------------------
# wide per-line layer render
# --------------------------------------------------------------------------

def layer_wide(vram, cgram, lay, lines, policy, bpp, transparent=None,
               lo=0, hi=0, y0=0, y1=224, hud_band=None):
    """Render BG(lay) at 342x224 using each scanline's own scroll.

    `lines` is the per-line PPU journal (attract_fight_lines.json), which is
    the P1 authority for scroll: this game drives BG1HOFS/BG1VOFS from a
    raster IRQ chain, so a single vblank register read would render the HUD
    band with the world's scroll and vice versa.
    """
    m = R.decode_bg_map(vram, lay["map_base_word"], lay["tiles_w"],
                        lay["tiles_h"])
    W = lay["tiles_w"] * 8
    H = lay["tiles_h"] * 8
    key = transparent if transparent is not None else (0, 0, 0)
    byline = {e["line"]: e for e in lines}
    cache = {}
    img = []
    idx = lay["layer"] - 1
    colors = 1 << bpp
    for y in range(y1):
        e = byline.get(y) or byline.get(1)
        h = e["h"][idx]
        v = e["v"][idx]
        row = [key] * FRAME_W
        if y0 <= y:
            for dx in range(FRAME_W):
                nx = NATIVE_X0 + dx
                wx = policy(h + nx, W, lo=lo, hi=hi)
                wy = (v + y) % H
                ent = m[(wy >> 3) % lay["tiles_h"]][(wx >> 3) % lay["tiles_w"]]
                tile = ent & 0x3FF
                pal = (ent >> 10) & 7
                xf = bool(ent & 0x4000)
                yf = bool(ent & 0x8000)
                ck = (tile, bpp)
                px = cache.get(ck)
                if px is None:
                    px = RR.tile_pixels(vram, lay["char_base_word"], tile, bpp)
                    cache[ck] = px
                py = (7 - (wy & 7)) if yf else (wy & 7)
                pxx = (7 - (wx & 7)) if xf else (wx & 7)
                val = px[py][pxx]
                if val:
                    row[dx] = cgram[pal * colors + val]
        img.append(row)
    return img


def over(base, top, key):
    out = []
    for y in range(len(base)):
        row = list(base[y])
        for x in range(len(row)):
            if top[y][x] != key:
                row[x] = top[y][x]
        out.append(row)
    return out


def flat(img):
    """(r,g,b) rows -> the flat byte rows ws_metrics.edge_score expects."""
    out = []
    for row in img:
        f = []
        for r, g, b in row:
            f += [r, g, b]
        out.append(f)
    return out


def seams(img) -> dict:
    fr = flat(img)
    h = len(img)
    return dict(x43=M.edge_score(fr, FRAME_W, h, EXTRA),
                x299=M.edge_score(fr, FRAME_W, h, 256 + EXTRA))


# --------------------------------------------------------------------------
# BG3 live-row analysis
# --------------------------------------------------------------------------

def bg3_live_rows(vram, lay, lines) -> dict:
    m = R.decode_bg_map(vram, lay["map_base_word"], lay["tiles_w"],
                        lay["tiles_h"])
    byline = {e["line"]: e for e in lines}
    idx = lay["layer"] - 1
    rows = {}
    for y in range(224):
        e = byline.get(y) or byline.get(1)
        v = e["v"][idx]
        r = ((v + y) >> 3) % lay["tiles_h"]
        rows.setdefault(r, []).append(y)
    out = []
    for r in sorted(rows):
        cells = m[r]
        nz = sum(1 for c in cells[:32] if (c & 0x3FF))
        nz_hi = sum(1 for c in cells[32:] if (c & 0x3FF))
        out.append(dict(map_row=r, lines=[min(rows[r]), max(rows[r])],
                        nonzero_cols_0_31=nz, nonzero_cols_32_63=nz_hi,
                        aliases_bg1_row=(r - 32) if r >= 32 else None))
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def load_scene(scene: str):
    v = RR.load_vram(scene)
    cg = RR.load_cgram(scene)
    b = json.load(open(os.path.join(RR.scene_dir(scene),
                                    "%s_bundle.json" % scene)))
    lines = json.load(open(os.path.join(
        RR.scene_dir(scene), "%s_lines.json" % scene)))["lines"]
    return v, cg, b["summary"], lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="attract_fight")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    v, cg, summ, lines = load_scene(a.scene)
    lays = {L["layer"]: L for L in summ["layers"]}
    bg1, bg2, bg3 = lays[1], lays[2], lays[3]
    key = (255, 0, 255)
    dest = os.path.join(R.OUT_ROOT, "margin_candidates")
    R.ensure_dir(dest)

    # BG1's world plane only: the HUD band (lines 22..71, hScroll forced 0) is
    # a different surface and belongs to R5, so it is rendered separately and
    # never fed into a margin-policy verdict.
    world_y0 = 72
    renders = {}
    for name, pol, lo, hi in (
            ("bg1_wrap", POLICIES["wrap"], 0, 0),
            ("bg1_mirror_arena", POLICIES["mirror_bounds"], 64, 448),
            ("bg1_extend_arena", POLICIES["clamp_bounds"], 64, 448)):
        renders[name] = layer_wide(v, cg, bg1, lines, pol, 4, key,
                                   lo=lo, hi=hi, y0=world_y0)
    for name, pol in (("bg2_wrap", POLICIES["wrap"]),
                      ("bg2_mirror_map", POLICIES["mirror_map"])):
        renders[name] = layer_wide(v, cg, bg2, lines, pol, 4, key)
    renders["bg3_wrap"] = layer_wide(v, cg, bg3, lines, POLICIES["wrap"], 2,
                                     key)

    # The three candidates the issue asks for, composited BG2 -> BG1 -> BG3.
    backdrop = [[cg[0]] * FRAME_W for _ in range(224)]
    cands = {
        "candidate_a_natural_wrap":
            over(over(over(backdrop, renders["bg2_wrap"], key),
                      renders["bg1_wrap"], key), renders["bg3_wrap"], key),
        "candidate_b_mirror_map_edge":
            over(over(over(backdrop, renders["bg2_mirror_map"], key),
                      renders["bg1_wrap"], key), renders["bg3_wrap"], key),
        "candidate_c_bg1_mirror_arena":
            over(over(over(backdrop, renders["bg2_mirror_map"], key),
                      renders["bg1_mirror_arena"], key),
                 renders["bg3_wrap"], key),
        "candidate_d_bg1_extend_arena":
            over(over(over(backdrop, renders["bg2_mirror_map"], key),
                      renders["bg1_extend_arena"], key),
                 renders["bg3_wrap"], key),
    }
    paths, scores = {}, {}
    for name, img in list(renders.items()) + list(cands.items()):
        paths[name] = RR.write_png(os.path.join(dest, name + ".png"), img)
        scores[name] = seams(img)
    RR.write_png(os.path.join(dest, "candidates_stack.png"),
                 RR.vcat([cands[k] for k in sorted(cands)], 6))

    # ---- the camera walls, synthesised (no owner state needed) -----------
    # BG1's hScroll IS the camera X (R2: slope 1.0000, r2 1.0000) and the
    # tilemap is static (R3), so the two wall cases can be rendered offline by
    # overriding the journal's scroll with the ROM clamp bounds
    # ($04:870B-$04:8725 -> camX in [64, 192]).  That is a synthesis, not a
    # measurement of the game at the wall: it is exact for the backgrounds
    # (static map + known scroll) and says nothing about sprites there.
    walls = {}
    wall_scores = {}
    for camx, side in ((64, "left_wall"), (192, "right_wall")):
        wl = [dict(e) for e in lines]
        for e in wl:
            h = list(e["h"])
            h[0] = 0 if h[0] == 0 else camx      # keep the HUD band's forced 0
            e["h"] = h
        for pname, pol, lo, hi in (
                ("wrap", POLICIES["wrap"], 0, 0),
                ("mirror_arena", POLICIES["mirror_bounds"], 64, 448),
                ("extend_arena", POLICIES["clamp_bounds"], 64, 448)):
            img = over(over(backdrop,
                            layer_wide(v, cg, bg2, wl, POLICIES["wrap"], 4,
                                       key), key),
                       layer_wide(v, cg, bg1, wl, pol, 4, key, lo=lo, hi=hi,
                                  y0=world_y0), key)
            nm = "%s_bg1_%s" % (side, pname)
            walls[nm] = RR.write_png(os.path.join(dest, nm + ".png"), img)
            wall_scores[nm] = seams(img)
    paths.update(walls)
    scores.update(wall_scores)

    # ---- BG2 wrap seamlessness -------------------------------------------
    m2 = R.decode_bg_map(v, bg2["map_base_word"], bg2["tiles_w"],
                         bg2["tiles_h"])
    v2 = bg2["vscroll"]
    vis_rows = sorted({((v2 + y) >> 3) % bg2["tiles_h"] for y in range(224)})
    col0 = [m2[r][0] for r in vis_rows]
    col31 = [m2[r][31] for r in vis_rows]
    # pixel-level: the column of pixels at world x=0 vs at world x=255
    px_l, px_r, same = [], [], 0
    for r in vis_rows:
        for e, xin, dst in ((m2[r][0], 0, px_l), (m2[r][31], 7, px_r)):
            t = RR.tile_pixels(v, bg2["char_base_word"], e & 0x3FF, 4)
            xf = bool(e & 0x4000)
            for yy in range(8):
                dst.append(t[yy][(7 - xin) if xf else xin])
    for pl, pr in zip(px_l, px_r):
        if (pl == 0) == (pr == 0):
            same += 1
    bg2_seam = dict(
        visible_map_rows=vis_rows,
        col0_entries=["0x%04x" % e for e in col0],
        col31_entries=["0x%04x" % e for e in col31],
        tiles_identical=col0 == col31,
        opacity_agreement=round(same / max(1, len(px_l)), 3),
        wrap_seam_score=scores["bg2_wrap"],
        mirror_seam_score=scores["bg2_mirror_map"])

    doc = dict(
        issue="beads-8wg.9.13.3",
        deliverable="R3 addendum — tilemap reach, wrap/mirror candidates, "
                    "BG1/BG3 map overlap",
        scene=a.scene,
        frame_geometry=dict(extra=EXTRA, width=FRAME_W,
                            native_window=[EXTRA, 256 + EXTRA],
                            left_margin_native_x=[NATIVE_X0, 0],
                            right_margin_native_x=[256, NATIVE_X1]),
        layers={str(L["layer"]): L for L in summ["layers"]},
        bg2_wrap=bg2_seam,
        seam_scores=scores,
        renders=paths,
        bg3_displayed_rows=bg3_live_rows(v, bg3, lines),
        bg1_displayed_rows=bg3_live_rows(v, bg1, lines),
        camera_walls=dict(
            clamp_x=[64, 192], clamp_source="$04:870B-$04:8725",
            authored_bg1_world_px=[64, 448],
            authored_bg1_map_cols=[8, 55],
            margin_outside_authored_px=dict(
                left_wall_left_margin=[64 - EXTRA, 64],
                right_wall_right_margin=[448, 448 + EXTRA],
                note="exactly EXTRA px of margin fall outside BG1's authored "
                     "art, and only at the wall on that side; at every other "
                     "camera X both margins land on real art"),
            renders=walls, seam_scores=wall_scores),
    )
    out = a.out or os.path.join(R.OUT_ROOT, "tilemap.json")
    R.write_json(out, doc)
    print("wrote", out)
    for k in sorted(scores):
        print("  %-32s x43 ratio=%-7s excess=%-8s | x299 ratio=%-7s "
              "excess=%s" % (k, scores[k]["x43"]["ratio"],
                             scores[k]["x43"]["excess"],
                             scores[k]["x299"]["ratio"],
                             scores[k]["x299"]["excess"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
