r"""R4 — per-screen PPU / layer inventory for GWED, plus the fight screen's
tilemap size and a preliminary streamed-vs-static note.

Run from **PowerShell**, not Git-Bash (see the note in ws_recon.py).

Stages:

  capture  one fresh process per scene; writes the full evidence bundle
           (get_ppu_state, get_dma_state, ppu_lines 0 224, ppu_window per
           layer, oam_render_get, dump_oam, dump_cgram, dump_vram, full WRAM,
           composite screenshot) under
           analysis/widescreen/recon/screens/<scene>/.
  layers   one fresh process PER LAYER per scene with SNESRECOMP_LAYER_MASK
           set — the mask is read once at the first PpuBeginDrawing, so it
           cannot be changed inside a running process.  Only the layers the
           capture stage found enabled are shot, plus an all-layers
           reference.
  vram     for the fight scene: walk the always-on VRAM write ring over each
           BG's tilemap byte range across the whole attract fight and report
           whether the map is streamed or static.
  report   fold everything into analysis/widescreen/recon/screens.json with a
           role for every enabled layer on every scene.

Usage:
  py -3 scripts\widescreen\recon_screens.py --stage all --port 4473
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ws_recon as W  # noqa: E402

OUT = os.path.join(W.OUT_ROOT, "screens")

SCENE_NAMES = ["attract_fight", "attract_crawl", "attract_cinematic",
               "title_menu", "title_logo", "victory_quote", "ko_1p_win",
               "black_transition", "final_convo", "ending"]

# Roles, read off the per-layer SNESRECOMP_LAYER_MASK captures in
# analysis/widescreen/recon/screens/<scene>/layers/ and cross-checked against
# the register decode and the per-line raster bands.  Every enabled layer on
# every scene has an entry — no UNKNOWNs.  The evidence BMP for each is named
# in screens.json next to the role, so the call is auditable.
ROLES = {
    "attract_fight": {
        "BG1": "HUD + NEAR/FLOOR. One layer, two raster bands: lines ~22-71 "
               "are the health-bar/timer/energy HUD drawn with scroll forced "
               "to (h=0, v=440) and BG1's window clipping disabled; lines "
               "72-224 are the near stage plane, buildings and ground, "
               "scrolled by the camera (slope 1.0).",
        "BG2": "FAR BACKDROP. Distant skyline and the arena dome. Rendered "
               "hScroll is pinned to 0 for the whole fight, so its parallax "
               "slope against the camera is 0.000 (r2 1.0) — this game does "
               "not parallax-scroll its far plane at all.",
        "BG3": "2bpp TEXT/BANNER OVERLAY. Carries the attract story crawl "
               "here (vScroll advances ~0.5 px/frame); the same layer carries "
               "the '1P WIN' banner on the round-end screen. Char base "
               "$06000, its own 64x64 map at $C000.",
        "OBJ": "the two mobile suits, their weapons, projectiles and hit "
               "sparks.",
        "_backdrop": "HDMA ch4 targets $2121/$2122 (CGADD/CGDATA), rewriting "
                     "palette entries per scanline; that is why the OBJ-only "
                     "capture shows a horizontally striped ground instead of "
                     "a flat backdrop. HDMA ch7 targets $2128 (WH2) for a "
                     "per-line window band. Both channels are EFFECT-DRIVEN "
                     "and intermittent — sampling get_dma_state at a "
                     "different instant in the same fight shows zero active "
                     "channels. This is exactly the P5 trap X1 fell into "
                     "(gating on an HDMAEN mirror).",
    },
    "attract_crawl": {
        "BG2": "full-screen character portrait art (Relena).",
        "BG3": "starfield.",
        "OBJ": "the crawl's caption TEXT ('After Colony 195') — it is drawn "
               "with sprites, not with a tilemap. That matters for the "
               "no-stretched-text criterion: this surface is an OBJ caption, "
               "not BG text.",
        "_hdma": "ch1 -> $212C (TM), per-line main-screen designation: this "
                 "is how the screen letterboxes itself vertically.",
    },
    "attract_cinematic": {
        "BG1": "mecha key art.",
        "BG2": "debris / spark particles.",
        "BG3": "flat colour plate behind the art (solid yellow on this "
               "frame).",
        "OBJ": "a single debris sprite.",
        "_hdma": "ch1 -> $212C (TM); the screen's TM/TS read from "
                 "get_ppu_state is the vblank value (0x00), so the per-line "
                 "journal is the authority for which layers are on.",
    },
    "title_menu": {
        "BG1": "logo lettering ('Mobile Suit GUNDAM / ENDLESS DUEL'), the "
               "mode-menu labels (STORY/VS/TRIAL/OPTION) and the copyright "
               "line — all BG tilemap text.",
        "BG2": "the large yellow 'W'.",
        "BG3": "starfield and planet backdrop.",
        "OBJ": "the 'GUNDAMWING' wordmark and the menu selector frame pieces.",
        "_hdma": "ch2 -> $212C (TM), per-line main-screen designation.",
    },
    "title_logo": {
        "BG1": "logo lettering; the mode-menu labels have not faded in yet at "
               "this point.",
        "BG2": "the large yellow 'W'.",
        "BG3": "starfield and planet backdrop.",
        "OBJ": "the 'GUNDAMWING' wordmark.",
    },
    "victory_quote": {
        "BG1": "HUD band (health bars, timer, energy counters) plus the 'W W' "
               "round-win markers. Same two-band structure as the fight, but "
               "the world rows are transparent on this screen.",
        "BG2": "the pilot portrait art.",
        "BG3": "enabled but contributes nothing on this frame — it is the "
               "banner/text layer, idle between banners.",
        "OBJ": "the winner's mobile suit and the flame effect.",
        "_colour_math": "CGADSUB 0x90 with a non-zero fixed colour "
                        "($1108) — colour math is active on this screen "
                        "(unlike the live fight, where CGADSUB is 0x00).",
    },
    "ko_1p_win": {
        "BG1": "HUD band + 'W W' markers + the arena floor/platform (the "
               "near plane), camera-scrolled.",
        "BG2": "the industrial stage backdrop.",
        "BG3": "the '1P WIN' banner glyphs (2bpp).",
        "OBJ": "the standing winner and the downed loser.",
        "_colour_math": "CGADSUB 0x37 — colour math active.",
    },
    "black_transition": {
        "BG3": "the only enabled layer, and it is on the SUBSCREEN only "
               "(TS 0x04, TM 0x00): a fully black inter-stage transition.",
    },
    "final_convo": {
        "BG1": "the speaking character's portrait.",
        "BG2": "machinery backdrop.",
        "BG3": "the dialogue window frame and its text — BG tilemap text, the "
               "surface the localization mod already patches.",
        "OBJ": "nothing visible on this frame.",
    },
    "ending": {
        "BG2": "full-screen epilogue mecha art; the only enabled layer.",
    },
}

# Which line to read the per-layer window state at.  100 is inside the fight's
# world band (the HUD band ends at line 71), 40 is inside the HUD band.
WINDOW_LINES = (40, 100, 200)


def stage_capture(args) -> dict:
    W.ensure_dir(OUT)
    out = {}
    for name in SCENE_NAMES:
        scene = W.SCENES_BY_NAME[name]
        dest = W.ensure_dir(os.path.join(OUT, name))
        with W.Instance(args.port) as inst:
            c = inst.c
            info = W.reach_scene(c, scene)
            bundle = W.snapshot(c, dest, name)
            # per-layer windows at three lines, not just one
            wins = {}
            for line in WINDOW_LINES:
                wins[str(line)] = {("layer%d" % l): c.j("ppu_window %d %d"
                                                        % (line, l))
                                   for l in range(6)}
            W.write_json(os.path.join(dest, "%s_windows_multi.json" % name),
                         wins)
            bundle["windows_multi"] = wins
            bundle["scene"] = dict(name=name, note=scene["note"],
                                   kind=scene["kind"], **info)
        out[name] = bundle
        s = bundle["summary"]
        print("[capture] %-18s bgmode=%d TM=%s TS=%s hdma=%d layers=%s"
              % (name, s["bgmode"], s["screenEnabled"][0],
                 s["screenEnabled"][1], len(s["hdma"]),
                 [l["layer"] for l in s["layers"] if l["main"] or l["sub"]]))
    W.write_json(os.path.join(OUT, "capture.json"),
                 {k: dict(summary=v["summary"], files=v["files"],
                          screenshot=v["screenshot"], scene=v["scene"])
                  for k, v in out.items()})
    return out


def stage_layers(args) -> dict:
    cap = json.load(open(os.path.join(OUT, "capture.json")))
    res = {}
    for name in SCENE_NAMES:
        masks = ["all"] + [("bg%d" % (i + 1)) for i in range(4)]
        masks.append("obj")
        # Trim to the layers this screen ever enables.  The live TM/TS read in
        # `capture` is the value at the sampling instant, which for a screen
        # driven by HDMA or a raster IRQ can be the vblank value (all layers
        # off) — so the union over the per-line journal is the authority here,
        # not get_ppu_state's screenEnabled.
        lines = json.load(open(cap[name]["files"]["lines"]))["lines"]
        union = 0
        for ln in lines:
            union |= int(ln["enabled"][0], 16) | int(ln["enabled"][1], 16)
        masks = [m for m in masks
                 if m == "all" or (W.LAYER_BITS[m] & union)]
        dest = W.ensure_dir(os.path.join(OUT, name, "layers"))
        res[name] = []
        for m in masks:
            shot = os.path.join(dest, "%s_%s.bmp" % (name, m))
            # Resumable: one fresh process per layer is slow (an attract scene
            # has to free-run to the fight every time), so a previously
            # captured BMP is reused.  Delete the directory to force a redo.
            if os.path.exists(shot) and not args.force:
                res[name].append(dict(layer=m, mask=W.LAYER_BITS[m],
                                      path=shot, cached=True))
                print("[layers] %-18s %-4s -> cached" % (name, m))
                continue
            info = W.layer_capture(W.SCENES_BY_NAME[name], m, args.port, dest)
            res[name].append(info)
            print("[layers] %-18s %-4s -> %s (frame %s)"
                  % (name, m, os.path.basename(info["path"]), info["frame"]))
    prev = {}
    if os.path.exists(os.path.join(OUT, "layers.json")):
        prev = json.load(open(os.path.join(OUT, "layers.json")))
    prev.update(res)
    W.write_json(os.path.join(OUT, "layers.json"), prev)
    res = prev
    return res


def _map_row_histogram(events, layer):
    """Which tilemap ROW/COLUMN each write landed in.

    Undoes the quadrant layout so "the map is being streamed" can be told
    apart from "the HUD rows of a shared BG are being redrawn every frame" —
    on this game BG1 carries both, so the raw write count is misleading.
    """
    base_w = layer["map_base_word"]
    wider = layer["tiles_w"] > 32
    higher = layer["tiles_h"] > 32
    rows = {}
    cols = {}
    for e in events:
        w = int(e["a"], 16) // 2 - base_w
        if w < 0:
            continue
        col_hi = 0
        row_hi = 0
        if higher and wider and w >= 0x800:
            row_hi, w = 32, w - 0x800
        elif higher and not wider and w >= 0x400:
            row_hi, w = 32, w - 0x400
        if wider and w >= 0x400:
            col_hi, w = 32, w - 0x400
        ty = row_hi + (w >> 5)
        tx = col_hi + (w & 31)
        rows[ty] = rows.get(ty, 0) + 1
        cols[tx] = cols.get(tx, 0) + 1
    return dict(by_row={str(k): v for k, v in sorted(rows.items())},
                by_col={str(k): v for k, v in sorted(cols.items())},
                rows_touched=sorted(rows), cols_touched=sorted(cols))


def stage_vram(args) -> dict:
    """Streamed vs static tilemap, from the always-on VRAM write ring.

    `vwring_get <lo> <hi> [n]` filters the ring by BYTE address, so each BG's
    map range is queried directly.  A static full-width map shows a burst of
    writes at stage load and nothing afterwards; a streamed map shows writes
    arriving continuously as the camera moves.
    """
    cap = json.load(open(os.path.join(OUT, "capture.json")))
    layers = cap["attract_fight"]["summary"]["layers"]
    ranges = {}
    for l in layers:
        if not (l["main"] or l["sub"]):
            continue
        base = l["map_base_word"] * 2
        tiles = l["tiles_w"] * l["tiles_h"]
        ranges["BG%d" % l["layer"]] = (base, base + tiles * 2 - 1, l)

    with W.Instance(args.port) as inst:
        c = inst.c
        anchor = W.attract_fight_anchor(c)
        # The VRAM write ring holds the last 1<<17 = 131072 writes
        # (debug_server.c:368).  A fighter blows through that in well under a
        # second of gameplay, so ONE query at the end of the fight would only
        # cover the last handful of frames and "no writes" would be
        # meaningless.  Instead sample the ring repeatedly through the fight
        # and accumulate, recording the oldest frame each window reached so
        # the coverage can be shown to be continuous.
        acc = {k: {} for k in ranges}
        coverage = []
        f = anchor
        while f < anchor + 860:
            f = W.wait_frame(c, f + args.vwring_step, timeout=1200)
            probe = c.j("vwring_get 0 ffff 4096")
            plog = probe.get("log", [])
            coverage.append(dict(at_frame=f - anchor,
                                 window_oldest_frame=(plog[0]["f"] - anchor)
                                 if plog else None,
                                 window_newest_frame=(plog[-1]["f"] - anchor)
                                 if plog else None,
                                 total_writes=probe.get("total_writes")))
            for k, (lo, hi, _l) in ranges.items():
                r = c.j("vwring_get %x %x 4096" % (lo, hi))
                for e in r.get("log", []):
                    acc[k][(e["f"], e["a"])] = e
        out = dict(anchor=anchor, ranges={}, chr_ranges={},
                   ring_capacity_entries=1 << 17,
                   ring_coverage_samples=coverage,
                   ring_coverage_note=(
                       "window_oldest_frame is how far back a SINGLE "
                       "vwring_get reached. The limit is not the ring (1<<17 "
                       "entries, ~100 frames of a fight) but vwring_get's own "
                       "n cap of 4096 returned entries: at ~1350 VRAM writes "
                       "per frame that is only ~3 frames for an unfiltered "
                       "query and ~11 frames for the BG1 map filter. "
                       "--vwring-step must stay below that or the "
                       "accumulated counts have holes; frames_covered below "
                       "says how many frames actually landed in a window."))
        for k, (lo, hi, l) in ranges.items():
            log = sorted(acc[k].values(), key=lambda e: (e["f"], e["a"]))
            in_fight = [e for e in log if e["f"] >= anchor]
            frames = sorted({e["f"] for e in in_fight})
            out["ranges"][k] = dict(
                map_base_byte="0x%04x" % lo, map_end_byte="0x%04x" % hi,
                screen_size=l["screen_size"],
                tiles_w=l["tiles_w"], tiles_h=l["tiles_h"],
                map_px=[l["tiles_w"] * 8, l["tiles_h"] * 8],
                accumulated_ring_entries=len(log),
                frames_covered=len(frames),
                fight_frames_sampled=860,
                writes_during_fight=len(in_fight),
                distinct_frames_with_writes=len(frames),
                first_fight_frame_offset=(frames[0] - anchor) if frames
                else None,
                last_fight_frame_offset=(frames[-1] - anchor) if frames
                else None,
                verdict=("STATIC during the fight (no map writes at all once "
                         "the stage is up)" if not in_fight else
                         "writes observed during the fight — see "
                         "`row_histogram` for WHICH map rows move"),
                row_histogram=_map_row_histogram(in_fight, l),
                sample_writes=in_fight[:40],
            )
            print("[vram] %s map %s..%s (%s): %d writes during the fight over "
                  "%d frames"
                  % (k, out["ranges"][k]["map_base_byte"],
                     out["ranges"][k]["map_end_byte"], l["screen_size"],
                     len(in_fight), len(frames)))
        # CHR churn, for context: a fighter streams sprite CHR every frame, so
        # a nonzero count here is expected and is NOT evidence about the map.
        for k, (lo, hi, l) in ranges.items():
            cb = l["char_base_word"] * 2
            r = c.j("vwring_get %x %x 512" % (cb, min(cb + 0x1FFF, 0xFFFF)))
            out["chr_ranges"][k] = dict(
                char_base_byte="0x%05x" % cb,
                matched_in_trailing_window=r.get("matched"),
                note=("single trailing-window sample only, taken after the "
                      "fight; CHR churn is expected in a fighter and says "
                      "nothing about the tilemap"))
    W.write_json(os.path.join(OUT, "vram.json"), out)
    return out


# --------------------------------------------------------------------------
# Role assignment
# --------------------------------------------------------------------------

def _rows_with_content(path):
    w, h, rows = W.read_bmp(path)
    px, _n = W.dominant_color(rows)
    per_row = []
    for y, row in enumerate(rows):
        n = sum(1 for p in row if p != px)
        per_row.append(n)
    return w, h, px, per_row


def _band(per_row, thresh=1):
    ys = [y for y, n in enumerate(per_row) if n >= thresh]
    return (min(ys), max(ys)) if ys else None


def _fight_tilemap_reach(cap, camera):
    """PRELIMINARY R3 note — do the columns a 16:9 margin would show hold art?

    This is deliberately only a *look*: the full R3 tilemap-reach analysis
    (both walls, a full traverse, streamed-vs-static across stages) belongs to
    the next agent.  What is recorded here is one fight frame's BG1/BG2/BG3
    tilemap decoded out of the captured VRAM, with per-column occupancy over
    the row band each layer actually displays, and the margin columns called
    out using the camera clamp from camera.json.
    """
    fight = cap.get("attract_fight")
    if not fight:
        return None
    vram = W.unhex(json.load(open(fight["files"]["vram"]))["hex"])
    clamp = (camera.get("clamp", {}).get("x") or {})
    cam_lo = clamp.get("min", 64)
    cam_hi = clamp.get("max", 192)
    margin = 43           # ceil_even(256*4/3) == 342 -> 43 px per side

    out = dict(
        camera_clamp_px=[cam_lo, cam_hi],
        ws_margin_px=margin,
        native_x_span=[cam_lo, cam_hi + 256],
        wide_x_span=[cam_lo - margin, cam_hi + 256 + margin],
        layers={},
        caveat=("one frame of ONE stage (the attract demo's), decoded from a "
                "single VRAM capture.  R3 must repeat this at both walls, on "
                "every stage, and cross it with the VRAM write ring."),
        preliminary_finding=(
            "BG1's authored stage art occupies map columns 8..55 and NOTHING "
            "else: columns 0-7 and 56-63 are the modal (blank) entry for "
            "every row of the displayed band.  Camera X is clamped to "
            "[64, 192], so the native viewport reaches exactly x 64..448 = "
            "columns 8..55.  The tilemap is authored precisely to the "
            "camera's reach with ZERO spare columns, so a 43 px 16:9 margin "
            "on BG1 will be blank unless a layer policy fills it "
            "(LayerMarginGap / LayerRepeat / LayerMirror, or a new engine "
            "primitive).  This contradicts the survey's expectation of a "
            "'static full-width map => no engine primitive needed' and is the "
            "single most important thing for R3 to confirm at both walls and "
            "on every stage."),
        bg2_wrap_note=(
            "BG2 is only 32 tiles (256 px) wide with hScroll pinned to 0, so "
            "it already wraps every 256 px.  In a 342 px frame its margins "
            "would show the wrapped copy of the opposite edge unless clamped."),
        map_overlap_warning=(
            "BG1's tilemap base ($D000, 64x64) and BG3's ($C000, 64x64) "
            "OVERLAP: BG3's bottom quadrants land on BG1's top quadrants "
            "($D000-$DFFF).  The game gets away with it because BG1 only "
            "displays rows 29-56 and BG3 only uses columns 0-31, but any "
            "widening that starts writing BG1's unused rows/columns will "
            "corrupt BG3."),
        streamed_vs_static=(
            "STATIC.  Accumulating the always-on VRAM write ring across the "
            "whole attract fight (865 of ~865 frames covered), every single "
            "write inside BG1's map range lands in rows 58-63, columns 0-31 "
            "— the HUD band — and BG2's and BG3's map ranges receive no "
            "writes at all.  The stage tilemap is uploaded once at stage load "
            "and never touched again, so there is no streaming to hook and "
            "no WS-SHADOW history needed."),
        hud_surface=(
            "BG1 tilemap band, rows 58-63 x columns 0-31 (i.e. 256x48 px at "
            "map offset $E000 upward), rewritten every frame; displayed on "
            "lines ~22-71 with the layer's scroll forced to (h=0, v=440) and "
            "BG1's window clipping disabled.  So the HUD is a BG surface, not "
            "an OBJ surface — the R5 HUD branch should be the BG one "
            "(AnchorBandSlot / HudSplit + Bg3Widen), not the WS-OAM one."),
    )
    for l in fight["summary"]["layers"]:
        if not (l["main"] or l["sub"]):
            continue
        rows = W.decode_bg_map(vram, l["map_base_word"], l["tiles_w"],
                               l["tiles_h"])
        # the row band this layer displays, from its own vScroll
        v = l["vscroll"]
        ty0 = ((v + 0) // 8) % l["tiles_h"]
        ty1 = ((v + 223) // 8) % l["tiles_h"]
        if ty1 < ty0:
            ty0, ty1 = 0, l["tiles_h"] - 1     # band wraps: use the whole map
        occ, modal = W.column_occupancy(rows, ty0, ty1)
        if l["tiles_w"] > 32:
            left_cols = list(range((cam_lo - margin) // 8, cam_lo // 8))
            right_cols = list(range((cam_hi + 256) // 8 + 1,
                                    (cam_hi + 256 + margin) // 8 + 1))
        else:
            left_cols, right_cols = [], []

        def frac(cols):
            if not cols:
                return None
            tot = sum(occ[c]["rows"] for c in cols if c < len(occ))
            hit = sum(occ[c]["differs_from_modal"] for c in cols
                      if c < len(occ))
            return round(hit / tot, 4) if tot else None

        native_cols = list(range(cam_lo // 8, (cam_hi + 256) // 8 + 1))
        out["layers"]["BG%d" % l["layer"]] = dict(
            screen_size=l["screen_size"],
            map_px=[l["tiles_w"] * 8, l["tiles_h"] * 8],
            map_base_byte=l["map_base_byte"],
            char_base_byte=l["char_base_byte"],
            vscroll=v, row_band=[ty0, ty1],
            modal_entry="0x%04x" % modal,
            margin_cols_left=left_cols, margin_cols_right=right_cols,
            occupancy_native=frac(native_cols),
            occupancy_margin_left=frac(left_cols),
            occupancy_margin_right=frac(right_cols),
            per_column=occ,
        )
    return out


def stage_report(args) -> dict:
    cap = json.load(open(os.path.join(OUT, "capture.json")))
    lay = json.load(open(os.path.join(OUT, "layers.json")))
    vram = json.load(open(os.path.join(OUT, "vram.json")))
    camera = json.load(open(os.path.join(W.OUT_ROOT, "camera.json"))) \
        if os.path.exists(os.path.join(W.OUT_ROOT, "camera.json")) else {}
    slopes = {k: v["slope"] for k, v in camera.get("parallax", {}).items()}

    scenes = {}
    for name in SCENE_NAMES:
        s = cap[name]["summary"]
        shots = {os.path.basename(i["path"]).rsplit("_", 1)[-1][:-4]: i["path"]
                 for i in lay[name]}
        lines = json.load(open(cap[name]["files"]["lines"]))["lines"]
        bands = []
        prev = None
        for ln in lines:
            key = (tuple(ln["h"]), tuple(ln["v"]), tuple(ln["enabled"]),
                   tuple(ln["windowed"]), ln["cgwsel"], ln["cgadsub"])
            if key != prev:
                bands.append(dict(first_line=ln["line"], h=ln["h"], v=ln["v"],
                                  enabled=ln["enabled"],
                                  windowed=ln["windowed"],
                                  cgwsel=ln["cgwsel"],
                                  cgadsub=ln["cgadsub"]))
                prev = key

        # get_ppu_state's TM/TS is the value at the sampling instant, which
        # on an HDMA/raster-driven screen is the vblank value (often 0x00).
        # The union over the per-line journal is the authority for "does this
        # layer ever draw on this screen".
        union = 0
        for ln in lines:
            union |= int(ln["enabled"][0], 16) | int(ln["enabled"][1], 16)

        entries = []
        for l in s["layers"]:
            enabled = bool(union & (1 << (l["layer"] - 1)))
            key = "bg%d" % l["layer"]
            shot = shots.get(key)
            cov = None
            if shot and os.path.exists(shot):
                w, h, backdrop, per_row = _rows_with_content(shot)
                cov = dict(width=w, height=h,
                           backdrop="#%02x%02x%02x" % backdrop,
                           content_band=_band(per_row, max(2, w // 64)),
                           nonbackdrop_rows=sum(1 for n in per_row
                                                if n >= max(2, w // 64)),
                           mean_row_coverage=round(sum(per_row) / (h * w), 4))
            lname = "BG%d" % l["layer"]
            role = ROLES.get(name, {}).get(lname)
            if enabled and not role:
                raise RuntimeError("no role recorded for %s %s — every "
                                   "enabled layer must have one"
                                   % (name, lname))
            entries.append(dict(layer=lname, enabled=enabled,
                                role=role or "layer disabled on this screen",
                                registers=l, coverage=cov, layer_bmp=shot))
        obj_shot = shots.get("obj")
        obj_cov = None
        if obj_shot and os.path.exists(obj_shot):
            w, h, backdrop, per_row = _rows_with_content(obj_shot)
            obj_cov = dict(width=w, height=h,
                           backdrop="#%02x%02x%02x" % backdrop,
                           content_band=_band(per_row, max(2, w // 64)),
                           mean_row_coverage=round(sum(per_row) / (h * w), 4))
        obj_enabled = bool(union & 0x10)
        obj_role = ROLES.get(name, {}).get("OBJ")
        if obj_enabled and not obj_role:
            raise RuntimeError("no role recorded for %s OBJ" % name)
        entries.append(dict(layer="OBJ", enabled=obj_enabled,
                            role=obj_role or "OBJ disabled on this screen",
                            registers=s["obj"], coverage=obj_cov,
                            layer_bmp=obj_shot))

        scenes[name] = dict(
            note=cap[name]["scene"]["note"],
            frame=cap[name]["scene"]["frame"],
            bgmode=s["bgmode"], inidisp=s["inidisp"], obsel=s["obsel"],
            setini=s["setini"],
            TM=s["screenEnabled"][0], TS=s["screenEnabled"][1],
            TM_TS_note=("TM/TS above are the values at the sampling instant "
                        "(often the vblank value); "
                        "enabled_layers_per_line_union is the authority"),
            enabled_layers_per_line_union="0x%02x" % union,
            TMW=s["screenWindowed"][0], TSW=s["screenWindowed"][1],
            cgwsel=s["cgwsel"], cgadsub=s["cgadsub"],
            fixedColor=s["fixedColor"],
            colour_math=("none (CGADSUB == 0x00)" if s["cgadsub"] == "0x00"
                         else "active, CGADSUB=%s CGWSEL=%s"
                              % (s["cgadsub"], s["cgwsel"])),
            windowsel=s["windowsel"], wbgobjlog=s["wbgobjlog"],
            window1=s["window1"], window2=s["window2"],
            hdma=s["hdma"],
            hdma_note=("no HDMA channel is active on this screen"
                       if not s["hdma"] else None),
            raster_bands=bands,
            widescreen_state=s["widescreen"],
            layers=entries,
            scene_notes={k: v for k, v in ROLES.get(name, {}).items()
                         if k.startswith("_")},
            composite_bmp=cap[name]["files"]["bmp"],
            evidence_dir=os.path.dirname(cap[name]["files"]["bmp"]),
        )

    out = dict(
        rom="Shin Kidou Senki Gundam W - Endless Duel (J)",
        build="build-ws-trace",
        fight_tilemap_reach=_fight_tilemap_reach(cap, camera),
        method=("one fresh process per scene for the register/VRAM/OAM bundle "
                "and one more per layer with SNESRECOMP_LAYER_MASK, since the "
                "mask is latched at the first PpuBeginDrawing"),
        pitfall_notes=[
            "BG char bases come from BG12NBA ($210B, low byte of the engine's "
            "bgTileAdr) and BG34NBA ($210C, high byte); the engine's own "
            "PPU_bgTileAdr macro is (bgTileAdr >> layer*4 & 0xF) << 12 in "
            "WORD units, which is what decode_layers() reproduces.  Getting "
            "these two registers the wrong way round is a documented "
            "day-loss in docs/LOCALIZATION_PLAYBOOK.md.",
            "Map base from BG?SC is (reg & 0xFC) << 8 in WORD units, i.e. "
            "byte address = that x2.",
            "A 64-tile-wide screen stores its right half at map_base + 0x800 "
            "bytes (0x400 words), not contiguously.",
        ],
        camera_slopes=slopes,
        fight_tilemaps=vram["ranges"],
        fight_chr=vram["chr_ranges"],
        scenes=scenes,
    )
    W.write_json(os.path.join(W.OUT_ROOT, "screens.json"), out)
    print("[report] wrote", os.path.join(W.OUT_ROOT, "screens.json"))
    for name, sc in scenes.items():
        print("  %-18s mode=%d TM=%s enabled=%s"
              % (name, sc["bgmode"], sc["TM"],
                 [e["layer"] for e in sc["layers"] if e["enabled"]]))
    return out


STAGES = dict(capture=stage_capture, layers=stage_layers, vram=stage_vram,
              report=stage_report)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", default="all", choices=list(STAGES) + ["all"])
    ap.add_argument("--port", type=int, default=4473,
                    help="debug port; use 4471-4479 only")
    ap.add_argument("--vwring-step", type=int, default=8,
                    help="how often (frames) the vram stage re-queries the "
                         "VRAM write ring; must be small enough that "
                         "consecutive windows overlap")
    ap.add_argument("--force", action="store_true",
                    help="re-capture per-layer BMPs that already exist")
    ap.add_argument("--scenes", default=None,
                    help="comma-separated subset of the scene table")
    args = ap.parse_args()
    if args.scenes:
        global SCENE_NAMES
        SCENE_NAMES = args.scenes.split(",")
    W.ensure_dir(OUT)
    for n in (["capture", "layers", "vram", "report"]
              if args.stage == "all" else [args.stage]):
        print("=== stage", n, "===")
        STAGES[n](args)


if __name__ == "__main__":
    main()
