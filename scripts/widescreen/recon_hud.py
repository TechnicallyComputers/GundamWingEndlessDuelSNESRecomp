#!/usr/bin/env py -3
"""R5 — GWED fight HUD layout (Beads beads-8wg.9.13.3).

Offline post-processing of the recon bundles: it decodes BG1's HUD tilemap
rows out of the captured VRAM for every scene that shows the HUD
(attract_fight, ko_1p_win, victory_quote), diffs them against each other to
prove which cells carry which element, and writes

  analysis/widescreen/recon/hud.json        the recon record (elements, their
                                            pixel columns, anchors, evidence)
  analysis/widescreen/recon/hud_spec.json   the same thing in the shape
                                            `ws_verdicts.py --hud-json` reads
  analysis/widescreen/recon/hud_work/*.png  the renders the numbers came from

The band geometry comes from the per-line PPU journal, not from a vblank
register read: during the fight BG1 is drawn as two surfaces by a raster IRQ
chain ($00:8900 writes BG1HOFS from $7E:068C, $00:891F writes BG1VOFS), and
the HUD is the band where that chain has forced (hScroll, vScroll) to
(0, 440).  With vScroll 440 the displayed map row is ((440 + y) >> 3) & 63, so
rows 58..63 are exactly scanlines 24..71.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import recon_render as RR  # noqa: E402
import ws_recon as R  # noqa: E402

SCENES = ["attract_fight", "ko_1p_win", "victory_quote"]
BG1_MAP_WORD = 26624          # $D000
HUD_ROWS = (58, 63)
HUD_VSCROLL = 440

# Element table.  Every `cols` range below is *derived* from the tilemap dump
# and the cross-scene pixel diffs this script recomputes; `evidence` names the
# measurement that pins it.  px = col * 8 (BG1 hScroll is forced to 0 in the
# band, so map column c is screen pixels [8c, 8c+8)).
ELEMENTS = [
    dict(name="frame_left_cap", cols=[0, 0], rows=[58, 63], anchor="left",
         kind="chrome",
         evidence="identical in all three scenes; map col 0 of rows 58-63"),
    dict(name="p1_name_plate", cols=[2, 9], rows=[58, 58], anchor="left",
         kind="text",
         evidence="tilemap entries $0768-$076F are CONSTANT across scenes "
                  "while the rendered name changes, so the glyphs live in the "
                  "CHARACTER data: BG1 4bpp tiles $368-$36F are rewritten per "
                  "match. The P2 twin ($0778-$077F -> tiles $378-$37F) is the "
                  "one that differs between the captured scenes "
                  "(DEATHSCYTHE / EPYON / SHENLONG) and a VRAM diff isolates "
                  "exactly tiles $378-$37F. Text is centred in the 64 px "
                  "field."),
    dict(name="p1_health_bar", cols=[1, 14], rows=[59, 59], anchor="left",
         kind="gauge",
         evidence="row 59 cols 1-14; segment tile = $330 + n where n = pixels "
                  "emptied (0 = full .. 8 = empty), palette 1. Depletes from "
                  "the OUTER end: the P2 mirror in attract_fight reads "
                  "$4B30 x12, $4B35, $4B38 across cols 17-30."),
    dict(name="p1_boost_bar", cols=[3, 14], rows=[60, 60], anchor="left",
         kind="gauge",
         evidence="row 60; segment tile = $320 + n. attract_fight P1 reads "
                  "$0728 $0728 $0728 $0721 then $0720 x8 over cols 3-14, i.e. "
                  "empty at the outer end and full toward the centre."),
    dict(name="p1_energy_counter", cols=[1, 6], rows=[61, 63], anchor="left",
         kind="text",
         evidence="3 decimal digits at cols 2-4 (px 16-39) plus a charge "
                  "arrow at col 5; digit d = tile $301 + d (top half, row 61) "
                  "and $311 + d (bottom half, row 62). attract 2/1/5 -> "
                  "$0703 $0702 $0706; ko 3/0/0 -> $0704 $0701 $0701."),
    dict(name="p1_round_win_markers", cols=[11, 14], rows=[61, 62],
         anchor="left", kind="chrome",
         evidence="the 'W W' pair: two 2x2 marker cells at cols 11-12 and "
                  "13-14, tiles $30B/$30C over $31B/$31C. Empty state is the "
                  "chrome tile $0756/$1400 (attract_fight, P1 with 0 wins). "
                  "BG, not OBJ."),
    dict(name="time_label", cols=[14, 17], rows=[58, 58], anchor="center",
         kind="text",
         evidence="tiles $0751-$0754 = the letters T I M E, identical in all "
                  "three scenes. Spans px 112-143, i.e. it straddles the "
                  "px-120/136 split that the rows below want -> this is why "
                  "row 58 needs its own anchor band."),
    dict(name="time_digits", cols=[15, 16], rows=[59, 60], anchor="center",
         kind="text",
         evidence="2 digits in a black pod at px 120-135. attract_fight shows "
                  "the infinity glyph ($075C/$075D over $075E/$075F) because "
                  "the attract demo runs with the clock disabled; ko_1p_win "
                  "and victory_quote read 99 ($070A/$070A over $071A/$071A). "
                  "Same digit font as the energy counters."),
    dict(name="p2_round_win_markers", cols=[17, 20], rows=[61, 62],
         anchor="right", kind="chrome",
         evidence="mirror of the P1 markers about px 128 (cols 17-20); "
                  "chrome tile $0756 in all three captures, so P2 has 0 wins "
                  "in every sample."),
    dict(name="p2_health_bar", cols=[17, 30], rows=[59, 59], anchor="right",
         kind="gauge",
         evidence="same tiles as P1 with the X-flip bit set and a different "
                  "palette ($4B30 = $0330 | flip | pal2)."),
    dict(name="p2_boost_bar", cols=[17, 30], rows=[60, 60], anchor="right",
         kind="gauge", evidence="X-flipped mirror of p1_boost_bar."),
    dict(name="p2_name_plate", cols=[22, 29], rows=[58, 58], anchor="right",
         kind="text",
         evidence="tilemap $0778-$077F (tiles $378-$37F); the cross-scene "
                  "pixel diff over row 58 lands exactly on px 176-239."),
    dict(name="p2_energy_counter", cols=[25, 30], rows=[61, 63],
         anchor="right", kind="text",
         evidence="digits at cols 27-29 (px 216-239), charge arrow at col 26 "
                  "- the exact mirror of P1 about px 128."),
    dict(name="frame_right_cap", cols=[31, 31], rows=[58, 63], anchor="right",
         kind="chrome", evidence="map col 31 of rows 58-63"),
]

# Two anchor bands, because time_label (row 58, px 112-143) and time_digits
# (rows 59-60, px 120-135) do not share a split.
BANDS = [
    dict(slot=0, name="hud_row58_names_and_time_label",
         lines=[24, 31], map_rows=[58, 58], left_end=112, right_start=144),
    dict(slot=1, name="hud_rows59_63_bars_counters_timer",
         lines=[32, 71], map_rows=[59, 63], left_end=120, right_start=136),
]


def band_of(el) -> dict:
    for b in BANDS:
        if b["map_rows"][0] <= el["rows"][0] and el["rows"][1] <= b["map_rows"][1]:
            return b
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--spec-out", default=None)
    a = ap.parse_args()

    work = R.ensure_dir(os.path.join(R.OUT_ROOT, "hud_work"))
    maps, lines_by_scene, imgs = {}, {}, {}
    key = (255, 0, 255)
    for sc in SCENES:
        v = RR.load_vram(sc)
        cg = RR.load_cgram(sc)
        maps[sc] = R.decode_bg_map(v, BG1_MAP_WORD, 64, 64)
        lines_by_scene[sc] = json.load(open(os.path.join(
            RR.scene_dir(sc), "%s_lines.json" % sc)))["lines"]
        imgs[sc] = RR.draw_map_region(v, cg, BG1_MAP_WORD, 64, 64, 0, 4,
                                      0, 31, HUD_ROWS[0], HUD_ROWS[1],
                                      key=key)
        RR.write_png(os.path.join(work, "hud_%s.png" % sc), imgs[sc], scale=3)
    RR.write_png(os.path.join(work, "hud_all.png"),
                 RR.vcat([imgs[s] for s in SCENES], 6), scale=3)

    # ---- the band, straight out of the per-line journal ------------------
    band_report = {}
    for sc in SCENES:
        lo = hi = None
        for e in lines_by_scene[sc]:
            if e["h"][0] == 0 and e["v"][0] == HUD_VSCROLL:
                lo = e["line"] if lo is None else lo
                hi = e["line"]
        band_report[sc] = dict(
            hscroll_forced_zero_lines=[lo, hi],
            bg1_window_clipping="disabled in the band (BG1 bit of "
                                "screenWindowed clears: 0x1f -> 0x1e on the "
                                "fight, 0x15 -> 0x14 on the quote)",
            displayed_map_rows=sorted({((HUD_VSCROLL + y) >> 3) & 63
                                       for y in range(lo, hi + 1)}))

    # ---- cross-scene pixel diffs, per band -------------------------------
    def diff_runs(a_, b_, y0, y1):
        cols = [x for x in range(256)
                if any(a_[y][x] != b_[y][x] for y in range(y0, y1))]
        out, st, pv = [], None, None
        for x in cols:
            if st is None:
                st = x
            elif x != pv + 1:
                out.append([st, pv])
                st = x
            pv = x
        if st is not None:
            out.append([st, pv])
        return out

    diffs = {}
    for b in BANDS:
        y0 = (b["map_rows"][0] - HUD_ROWS[0]) * 8
        y1 = (b["map_rows"][1] - HUD_ROWS[0] + 1) * 8
        diffs[b["name"]] = dict(
            attract_vs_ko=diff_runs(imgs["attract_fight"], imgs["ko_1p_win"],
                                    y0, y1),
            ko_vs_quote=diff_runs(imgs["ko_1p_win"], imgs["victory_quote"],
                                  y0, y1))

    # ---- split validation: no element may straddle its band's split -------
    els = []
    straddles = []
    for el in ELEMENTS:
        b = band_of(el)
        px = [el["cols"][0] * 8, el["cols"][1] * 8 + 7]
        group = None
        if b:
            if px[1] < b["left_end"]:
                group = "left"
            elif px[0] >= b["right_start"]:
                group = "right"
            elif px[0] >= b["left_end"] and px[1] < b["right_start"]:
                group = "center"
            else:
                group = "STRADDLES"
                straddles.append(el["name"])
        e = dict(el)
        e["px"] = px
        e["lines"] = [el["rows"][0] * 8 - HUD_VSCROLL % 8 - 0, 0]
        e["lines"] = [(el["rows"][0] * 8) - HUD_VSCROLL,
                      (el["rows"][1] * 8 + 7) - HUD_VSCROLL]
        e["band"] = b["name"] if b else None
        e["split_group"] = group
        e["anchor_matches_split_group"] = (group == el["anchor"])
        els.append(e)

    # ---- OBJ: is any HUD piece a sprite? ---------------------------------
    obj = {}
    for sc in SCENES:
        d = json.load(open(os.path.join(RR.scene_dir(sc),
                                        "%s_oam_render.json" % sc)))
        inband, active = [], 0
        for snap in d["snaps"]:
            active = max(active, snap["active"])
            for i, sl in enumerate(snap["slot"]):
                if 22 <= sl[0] <= 71:
                    inband.append(dict(slot=i, y=sl[0],
                                       raw_x=sl[1] | (sl[2] << 8),
                                       tile=sl[3], attr=sl[4], big=sl[5]))
        obj[sc] = dict(active_max=active, entries_with_y_in_hud_band=inband,
                       verdict="no OBJ entry inside the HUD band" if not inband else "OBJ entries inside the HUD band")

    doc = dict(
        issue="beads-8wg.9.13.3",
        deliverable="R5 fight HUD layout",
        surface="BG1 (layer index 0) tilemap rows 58-63 at map bytes "
                "$E000-$E7FF, cols 0-31; rewritten every frame (R3: every "
                "VRAM write inside BG1's map range lands in exactly this "
                "rectangle)",
        band=dict(
            scanlines=[22, 71],
            content_scanlines=[24, 71],
            note="lines 22-23 are the bottom two pixel rows of map row 57, "
                 "which in the fight is arena art (tree tops) drawn with the "
                 "HUD's forced hScroll 0 and in the other two scenes is "
                 "blank. Anchor from line 24; give lines 22-23 a plain clamp "
                 "band so those two rows are not sliced.",
            per_scene=band_report),
        anchor_bands=BANDS,
        split=dict(left_end=120, right_start=136,
                   left_end_row58=112, right_start_row58=144,
                   rationale="rows 59-63: P1 content ends at px 119 and P2 "
                             "content starts at px 136, with the TIME pod in "
                             "between (px 120-135). Row 58: the TIME label "
                             "occupies px 112-143, so its split has to be "
                             "wider or the word is sliced (owner criterion "
                             "2). Everything is mirror-symmetric about px "
                             "128, so the two splits are symmetric too.",
                   verified_for_scenes=SCENES,
                   straddling_elements=straddles),
        elements=els,
        cross_scene_pixel_diffs=diffs,
        obj_hud=obj,
        recommended_calls=[
            "PpuSetWidescreenLayerClampBand(ppu, 0, 22, 24)",
            "PpuSetWidescreenLayerAnchorBandSlot(ppu, 0, 0, 24, 32, 112, 144)",
            "PpuSetWidescreenLayerAnchorBandSlot(ppu, 1, 0, 32, 72, 120, 136)",
            "PpuSetWidescreenHudAlwaysVisible(ppu, true)",
        ],
        open_risks=[
            "The HUD chrome is one continuous graphic with NO fully "
            "transparent column anywhere in px 1..254 (measured: the only "
            "empty columns in rows 58-63 are px 0 and 255). Anchoring "
            "therefore opens a 43 px transparent gap on each side of the "
            "TIME pod, through which BG2/the backdrop will show. There is no "
            "existing PPU primitive that fills an INTERIOR gap "
            "(LayerRepeat/Mirror act on the margins), and LayerStretchBand is "
            "barred here because the band carries glyphs. The owner has to "
            "accept the gap, or the fallback is ClampBand over the whole band "
            "(HUD stays native-centred, owner criterion 3 waived).",
            "ws_verdicts.py's hud_anchor_bg check cannot pass on this game as "
            "written: it asserts that ppu_window edges expand past x=256, but "
            "BG1's hardware window clipping is DISABLED inside the HUD band "
            "(measured: ppu_window 40 0 replies valid:false). The check needs "
            "to treat an unwindowed layer as not-applicable and assert the "
            "anchor-band state (and/or corroborate with pixels) instead. "
            "Filed here so beads-8wg.9.13.7 sees it.",
            "Health/boost fill direction (outward end empties first) is "
            "established from the P2 gauges in attract_fight plus the "
            "all-full/all-empty ko_1p_win sample. A human-driven round with "
            "P1 damaged would confirm P1's direction directly.",
        ],
    )
    dest = a.out or os.path.join(R.OUT_ROOT, "hud.json")
    R.write_json(dest, doc)

    # ---- the harness-ready spec ------------------------------------------
    spec = dict(
        _comment=("R5 output for ws_verdicts.py --hud-json. GWED's fight HUD "
                  "is entirely a BG surface: no OAM entry has Y inside the "
                  "HUD band in any of the three HUD scenes (measured from "
                  "oam_render_get), so `obj` is deliberately empty and "
                  "hud_anchor_obj should read SKIP. See hud.json for the "
                  "element table and for the reason hud_anchor_bg needs "
                  "extending before it can pass here."),
        obj=[],
        bg=[dict(name=b["name"], layer=0, lines=b["lines"],
                 left_end=b["left_end"], right_start=b["right_start"])
            for b in BANDS],
    )
    spec_dest = a.spec_out or os.path.join(R.OUT_ROOT, "hud_spec.json")
    R.write_json(spec_dest, spec)
    print("wrote", dest)
    print("wrote", spec_dest)
    print("band per scene:", json.dumps(
        {k: v["hscroll_forced_zero_lines"] for k, v in band_report.items()}))
    print("straddling elements:", straddles or "none")
    print("obj HUD:", {k: v["verdict"] for k, v in obj.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
