#!/usr/bin/env python3
"""GWED 16:9 widescreen VERDICT harness -- Beads beads-8wg.9.13.7.

Five checks, one per owner success criterion, each a subcommand:

  center-parity   presentation-only proof.  The same scene entered twice, once
                  with SNESRECOMP_WS_EXTRA=0 and once with =43, must satisfy
                  wide[:, 43:299] == native[:, 0:256] BYTE-EXACT over >=60
                  frames.  If that holds, nothing is stretched, nothing is
                  resampled, and the widescreen path did not perturb the guest
                  (criteria 1 and 2, and WIDESCREEN_PATTERNS P16's pixel half).
  margins         criterion 4.  One fresh process per SNESRECOMP_LAYER_MASK
                  (the mask latches at the first PpuBeginDrawing), measuring
                  each layer's margin fill against the DOMINANT BACKDROP
                  colour, plus a seam detector at the old 4:3 boundaries.
  hud-anchor      criterion 3.  HUD elements must ride the 16:9 edges: OBJ HUD
                  slots shift by exactly -43 / +43 / 0 with Y unchanged; a BG
                  HUD's window edges expand to [-43, 299).
  text-letterbox  criteria 1 and 2 for text and menu screens: uniform margins,
                  a centred PPU budget with no layer extension, and a centre
                  identical to the 4:3 sibling.
  sprite-nocull   criterion 5.  Sprites whose X falls in the margins must both
                  EXIST in the render-consumed OAM ring and DRAW pixels in the
                  OBJ-isolated capture.  The two stages separate a guest-side
                  cull (object_render_or_cull) from a host-side one
                  (object_ppu_or_presenter).

Every subcommand prints `PASS/FAIL/SKIP <check> <scenario>` lines and writes
`summary.json` (DKC2 report shape).  Exit 0 = all checks passed, 1 = a check
failed, 2 = harness error (the measurement did not happen, which is NOT a
verdict either way).

Scene identity is asserted from the WRAM gate proven in recon
(`--gate-json analysis/widescreen/recon/gate.json`), never from framebuffer
pixels.  Without a gate spec, pass `--no-gate`: the scripts still run and the
report is stamped `gate_verified: false` / scene identity UNVERIFIED.

RUN THESE FROM POWERSHELL.  Under the agent harness's Bash tool the game exe
dies in the loader before printing anything (0xC0000079 / 0xC0000135); the
identical subprocess call from PowerShell starts the game and opens the port.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ws_metrics as M  # noqa: E402


# ── frame-series capture ────────────────────────────────────────────────────

def framebmp_shots(bmp_dir: str) -> list:
    """The env dumper's output, labelled by the guest frame it presented."""
    out = []
    for name in sorted(os.listdir(bmp_dir)):
        if not (name.startswith("frame_") and name.endswith(".bmp")):
            continue
        f = int(name[6:-4])
        path = os.path.join(bmp_dir, name)
        out.append({"index": len(out), "frame": f, "path": path})
    return out


def capture_side(args, gate, name: str, ws_extra: int, count: int,
                 start_frame: int, want_backdrop: bool = False) -> dict:
    """One process: enter the scene and dump `count` consecutive frames.

    Frames come from `runner/src/widescreen.c`'s SNESRECOMP_FRAME_BMP_DIR
    dumper rather than from the debug server's `screenshot`, because only the
    dumper is FRAME-EXACT: it writes from inside the present path and names
    each file after the guest frame it presented.  `screenshot` copies
    whatever was most recently presented and reads `snes_frame_counter`
    afterwards, so its label can run several frames ahead of its content --
    measured on this build: a poll at frame 917 produced a reply labelled 920.
    Pairing a byte-exact comparison on that label reports a "1-pixel palette
    difference" that is really a 3-frame offset.  The debug server is still
    connected in the same process, for the WRAM gate and the PPU state.
    """
    out = M.ensure_dir(os.path.join(args.out, name))
    bmp_dir = M.ensure_dir(os.path.join(out, "bmp"))
    env = {"SNESRECOMP_FRAME_BMP_DIR": bmp_dir,
           "SNESRECOMP_FRAME_BMP_START": str(start_frame),
           "SNESRECOMP_FRAME_BMP_STEP": "1",
           "SNESRECOMP_FRAME_BMP_END": str(start_frame + count - 1)}
    with M.Instance(args.port, args.build, args.rom, ws_extra,
                    log_path=os.path.join(out, "stderr.log"),
                    extra_env=env) as inst:
        info = M.reach_scene(inst.c, args.scenario, gate, args.states_dir,
                             args.settle, args.load_at)
        # Free-run past the end of the dump window, then read the registers.
        M.wait_frame(inst.c, start_frame + count + 2)
        ppu = inst.c.j("get_ppu_state")
        backdrop = M.cgram_backdrop_rgb(inst.c) if want_backdrop else None
    shots = framebmp_shots(bmp_dir)
    if len(shots) < count:
        raise M.HarnessError(
            "%s dumped %d/%d frames into %s: the dump window [%d,%d] did not "
            "line up with the guest frame counter"
            % (name, len(shots), count, bmp_dir, start_frame,
               start_frame + count - 1))
    return {"entry": info, "ppu": ppu, "backdrop": backdrop, "shots": shots,
            "bmp_dir": bmp_dir, "dump_window": [start_frame,
                                                start_frame + count - 1]}


def resolve_start_frame(args, gate) -> dict:
    """The first guest frame of the dump window, for either entry kind.

    A savestate entry needs a probe run: the guest frame counter after
    `load_state` is whatever the state carries, which the harness cannot know
    in advance.  Because the load happens at a FIXED GUEST FRAME
    (`--load-at`), the post-load counter is reproducible, so one throwaway
    process is enough to learn it for the two measured ones.
    """
    if args.scenario.startswith("boot_attract"):
        target = (int(args.scenario.split(":", 1)[1]) if ":" in args.scenario
                  else M.BOOT_ATTRACT_DEFAULT_FRAME)
        return {"start": target, "method": "boot_attract target frame"}
    if args.scenario.startswith("state:"):
        name = args.scenario.split(":", 1)[1]
        with M.Instance(args.port, args.build, args.rom, 0,
                        log_path=os.path.join(args.out, "probe.log")) as inst:
            M.wait_frame(inst.c, args.load_at)
            M.load_state(inst.c, name, args.states_dir)
            post = M.frame(inst.c)
        return {"start": post + args.settle, "post_load_frame": post,
                "method": "probe run: load_state at guest frame %d -> counter "
                          "%d, plus %d settle frames"
                          % (args.load_at, post, args.settle)}
    raise M.HarnessError(
        "scenario %r has no deterministic dump window: it is entered by "
        "polling the WRAM gate, whose arrival frame varies. Use it with the "
        "`margins` / `sprite-nocull` checks, which sample rather than compare "
        "frame-for-frame." % args.scenario)


def parity_verdict(par, control, want_frames, rep):
    """Turn a centre-window comparison into a verdict, with a CONTROL.

    A byte-exact comparison between two PROCESSES assumes the guest executes
    reproducibly, and on this engine it does not always: the APU is paced by
    the audio thread, so a scene that is still running (as opposed to a frozen
    menu) can diverge between two runs of the SAME configuration.  Without a
    control, that shows up as a widescreen defect -- a false accusation.

    The control is a second 4:3 run compared against the first.  A parity
    mismatch is only a widescreen defect if it starts EARLIER than the
    control's own divergence; if the control diverges just as soon, the scene
    is simply not frame-reproducible and the parity claim is reported over the
    prefix where it is testable at all.
    """
    par["control"] = None if control is None else {
        "identical_prefix_frames": control["identical_prefix_frames"],
        "frame_fraction": control["frame_fraction"],
        "first_mismatch": control["first_mismatch"]}
    clean = (par["frames_identical"] == par["frames_compared"]
             and par["frames_compared"] >= want_frames)
    if clean:
        par["status"] = "PASS"
        par["detail"] = ("wide centre == native byte-exact on %s frames "
                         "(%s rows)" % (par["frame_fraction"],
                                        par["row_fraction"]))
        return par
    if control is None:
        par["status"] = "FAIL"
        par["detail"] = ("centre window differs after %d identical frames "
                         "(%s frames match); no --control run, so guest "
                         "reproducibility is unproven"
                         % (par["identical_prefix_frames"],
                            par["frame_fraction"]))
        return par
    cp = control["identical_prefix_frames"]
    pp = par["identical_prefix_frames"]
    if cp >= want_frames:
        par["status"] = "FAIL"
        par["detail"] = ("centre window differs from frame %d while the "
                         "4:3-vs-4:3 control is byte-identical for all %d "
                         "frames: the difference is the widescreen path"
                         % (pp, cp))
        return par
    if pp >= cp:
        # A prefix of zero identical frames is not evidence of anything: the
        # scene's own timeline differs between processes from the first
        # compared frame, so there is nothing for the parity claim to stand
        # on.  Reporting PASS on zero frames would be a verdict without a
        # measurement.
        par["status"] = "SKIP" if cp == 0 else "PASS"
        par["detail"] = ("centre window is byte-exact for %d frames; the "
                         "4:3-vs-4:3 control diverges at %d, so this scene is "
                         "not frame-reproducible across processes and the "
                         "parity claim holds only over that prefix "
                         "(widescreen is no worse than the control)" % (pp, cp))
        if cp == 0:
            par["detail"] += (" -- prefix is EMPTY, so this scenario yields no "
                              "centre-parity evidence at all; use a frozen "
                              "entry state")
        rep.find("guest_execution_not_reproducible", "strong",
                 "two identically configured 4:3 runs of this scene diverge "
                 "at frame %d of %d, so a frame-exact cross-process pixel "
                 "comparison cannot reach %d frames here. Use a frozen entry "
                 "state, or read the prefix."
                 % (cp, control["frames_compared"], want_frames),
                 control_first_mismatch=control["first_mismatch"])
        return par
    par["status"] = "FAIL"
    par["detail"] = ("centre window diverges at frame %d, EARLIER than the "
                     "4:3-vs-4:3 control's %d: the widescreen path is "
                     "responsible for the extra divergence" % (pp, cp))
    return par


def compare_center(native_shots: list, wide_shots: list, ws_extra: int) -> dict:
    """wide[:, extra:extra+256] == native[:, 0:256], byte-exact.

    Pairs on the GUEST FRAME NUMBER when the two runs' frame sets overlap
    (they do for a cold boot, which is deterministic), and falls back to
    pairing by index-since-entry for savestate entries where the two processes
    may have loaded at different host moments.  Which pairing was used is
    always recorded -- an unstated pairing is an unfalsifiable comparison.
    """
    by_frame_n = {s["frame"]: s for s in native_shots}
    by_frame_w = {s["frame"]: s for s in wide_shots}
    common = sorted(set(by_frame_n) & set(by_frame_w))
    if len(common) >= min(len(native_shots), len(wide_shots)) // 2:
        pairing = "guest_frame"
        pairs = [(by_frame_n[f], by_frame_w[f]) for f in common]
    else:
        pairing = "index_since_entry"
        pairs = list(zip(native_shots, wide_shots))

    total_rows = matching_rows = 0
    matching_frames = 0
    first_mismatch = None
    identical_prefix = None
    widths = set()
    for pair_index, (nat, wide) in enumerate(pairs):
        nw, nh, nrows = M.read_bmp_rgb(nat["path"])
        ww, wh, wrows = M.read_bmp_rgb(wide["path"])
        widths.add((nw, ww))
        if nw != M.NATIVE_WIDTH:
            raise M.HarnessError("the 4:3 side rendered %d px wide, not %d "
                                 "(SNESRECOMP_WS_EXTRA=0 did not take)"
                                 % (nw, M.NATIVE_WIDTH))
        if ww != nw + 2 * ws_extra:
            raise M.HarnessError("the wide side rendered %d px wide, expected "
                                 "%d (extra=%d did not take)"
                                 % (ww, nw + 2 * ws_extra, ws_extra))
        if nh != wh:
            raise M.HarnessError("height mismatch %d vs %d" % (nh, wh))
        frame_ok = True
        for y in range(nh):
            total_rows += 1
            crop = wrows[y][ws_extra * 3:(ws_extra + M.NATIVE_WIDTH) * 3]
            if crop == nrows[y]:
                matching_rows += 1
                continue
            frame_ok = False
            if first_mismatch is None:
                bad_x = next((x for x in range(M.NATIVE_WIDTH)
                              if crop[x * 3:x * 3 + 3]
                              != nrows[y][x * 3:x * 3 + 3]), None)
                first_mismatch = {
                    "native_frame": nat["frame"], "wide_frame": wide["frame"],
                    "y": y, "first_x_native": bad_x,
                    "first_x_wide": None if bad_x is None else bad_x + ws_extra,
                    "native_pixel": nrows[y][bad_x * 3:bad_x * 3 + 3].hex()
                    if bad_x is not None else None,
                    "wide_pixel": crop[bad_x * 3:bad_x * 3 + 3].hex()
                    if bad_x is not None else None,
                    "native_bmp": nat["path"], "wide_bmp": wide["path"]}
        matching_frames += 1 if frame_ok else 0
        if not frame_ok and identical_prefix is None:
            identical_prefix = pair_index
    if identical_prefix is None:
        identical_prefix = len(pairs)
    return {"pairing": pairing, "pairs": len(pairs),
            "identical_prefix_frames": identical_prefix,
            "widths": sorted("native=%d wide=%d" % w for w in widths),
            "frames_identical": matching_frames,
            "frames_compared": len(pairs),
            "frame_fraction": "%d/%d" % (matching_frames, len(pairs)),
            "rows_identical": matching_rows, "rows_compared": total_rows,
            "row_fraction": "%d/%d" % (matching_rows, total_rows),
            "first_mismatch": first_mismatch}


# ── center-parity ───────────────────────────────────────────────────────────

def cmd_center_parity(args, gate) -> int:
    out = M.ensure_dir(args.out)
    rep = M.Report("center_parity", args.scenario, out, args.build, args.rom,
                   {"SNESRECOMP_WS_EXTRA": ["0", str(args.ws_extra)],
                    "frames": args.frames}, gate)
    window = resolve_start_frame(args, gate)
    rep.doc["dump_window"] = window
    plan = [("native", 0), ("wide", args.ws_extra)]
    if args.control:
        plan.append(("control", 0))
    sides = {name: capture_side(args, gate, name, extra, args.frames,
                                window["start"]) for name, extra in plan}
    rep.doc["sides"] = {k: {"entry": v["entry"],
                            "widescreen": v["ppu"].get("widescreen"),
                            "dump_window": v["dump_window"],
                            "frames_dumped": len(v["shots"])}
                        for k, v in sides.items()}

    res = compare_center(sides["native"]["shots"], sides["wide"]["shots"],
                         args.ws_extra)
    control = (compare_center(sides["native"]["shots"],
                              sides["control"]["shots"], 0)
               if args.control else None)
    parity_verdict(res, control, args.frames, rep)
    rep.add("center_parity_byte_exact", res)
    if res["status"] == "FAIL":
        rep.find("center_window_not_byte_exact", "strong", res["detail"],
                 first_mismatch=res["first_mismatch"])
    rep.write()
    return 1 if rep.failed else 0


# ── margins ─────────────────────────────────────────────────────────────────

def cmd_margins(args, gate) -> int:
    out = M.ensure_dir(args.out)
    layers = [l.strip() for l in args.layers.split(",") if l.strip()]
    for l in layers:
        if l not in M.LAYER_MASKS:
            raise M.HarnessError("unknown layer %r (have %s)"
                                 % (l, ",".join(M.LAYER_MASKS)))
    rep = M.Report("margins", args.scenario, out, args.build, args.rom,
                   {"SNESRECOMP_WS_EXTRA": str(args.ws_extra),
                    "SNESRECOMP_LAYER_MASK": {l: M.LAYER_MASKS[l]
                                              for l in layers}}, gate)
    captures = {}
    for i, layer in enumerate(layers):
        # One fresh process per mask: SNESRECOMP_LAYER_MASK is read ONCE at
        # the first PpuBeginDrawing, so a second mask in the same process is
        # silently ignored.
        port = M.PORT_BASE + (i % (M.PORT_LIMIT - M.PORT_BASE + 1))
        ldir = M.ensure_dir(os.path.join(out, layer))
        with M.Instance(port, args.build, args.rom, args.ws_extra,
                        layer_mask=M.LAYER_MASKS[layer],
                        log_path=os.path.join(ldir, "stderr.log")) as inst:
            info = M.reach_scene(inst.c, args.scenario, gate, args.states_dir,
                                 args.settle, args.load_at)
            samples = []
            for k in range(2):
                if k:
                    # Second sample far enough away that a moving camera has
                    # actually moved.  Camera motion is read from the PPU's
                    # own hScroll (P1 authority), never inferred from pixels.
                    M.wait_frame(inst.c, M.frame(inst.c) + args.sample_gap)
                ppu = inst.c.j("get_ppu_state")
                path = os.path.join(ldir, "%s_s%d_f%06d.bmp"
                                    % (layer, k, M.frame(inst.c)))
                r = inst.c.j("screenshot " + M.fwd(path))
                if not r.get("ok"):
                    raise M.HarnessError("screenshot failed: %r" % r)
                samples.append({"sample": k, "path": path,
                                "frame": r.get("frame"),
                                "width": r.get("width"),
                                "ws_extra": r.get("ws_extra"),
                                "hScroll": ppu.get("hScroll"),
                                "vScroll": ppu.get("vScroll"),
                                "screenEnabled": ppu.get("screenEnabled"),
                                "bgmode": ppu.get("bgmode"),
                                "widescreen": ppu.get("widescreen")})
            shadow = inst.c.j("ws_shadow_stats")
        for s in samples:
            s["metrics"] = M.margin_metrics(s["path"], args.ws_extra)
        motion = [abs(a - b) for a, b in zip(samples[0]["hScroll"],
                                             samples[1]["hScroll"])]
        captures[layer] = {"entry": info, "samples": samples,
                           "hscroll_delta": motion,
                           "camera_motion": any(d > 0 for d in motion),
                           "ws_shadow_stats": shadow}
    rep.doc["layers"] = captures

    # --- margin_non_blank -------------------------------------------------
    per_layer = {}
    failing = []
    for layer, cap in captures.items():
        m = cap["samples"][0]["metrics"]["regions"]
        nat = m["native_view"]["non_backdrop_fraction"]
        left = m["left_margin"]["non_backdrop_fraction"]
        right = m["right_margin"]["non_backdrop_fraction"]
        need = M.MARGIN_SHARE * nat
        present = nat >= M.LAYER_PRESENT_MIN
        entry = {"native_non_backdrop_fraction": round(nat, 5),
                 "left_non_backdrop_fraction": round(left, 5),
                 "right_non_backdrop_fraction": round(right, 5),
                 "required_fraction": round(need, 5),
                 "layer_present_in_native": present,
                 "backdrop_rgb": cap["samples"][0]["metrics"]
                 ["dominant_backdrop_rgb"]}
        if not present:
            entry["verdict"] = "SKIP"
            entry["reason"] = ("native window is %.2f%% non-backdrop (< %.0f%%): "
                               "this layer draws nothing here, so its margins "
                               "carry no information"
                               % (100 * nat, 100 * M.LAYER_PRESENT_MIN))
        elif left >= need and right >= need:
            entry["verdict"] = "PASS"
        else:
            entry["verdict"] = "FAIL"
            failing.append(layer)
            rep.find("background_load_or_render", "strong",
                     "%s paints %.2f%% of the native window but only %.2f%% "
                     "left / %.2f%% right (needs %.2f%%): the layer is not "
                     "being rendered into the margins"
                     % (layer, 100 * nat, 100 * left, 100 * right, 100 * need),
                     layer=layer, frame=cap["samples"][0]["frame"],
                     image=cap["samples"][0]["path"])
        per_layer[layer] = entry
    evaluated = [k for k, v in per_layer.items() if v["verdict"] != "SKIP"]
    rep.add("margin_non_blank", {
        "status": "FAIL" if failing else ("PASS" if evaluated else "SKIP"),
        "detail": ("%d/%d evaluated layers paint both margins%s"
                   % (len(evaluated) - len(failing), len(evaluated),
                      "; empty: " + ",".join(failing) if failing else "")),
        "threshold_margin_share": M.MARGIN_SHARE,
        "threshold_layer_present": M.LAYER_PRESENT_MIN,
        "layers": per_layer})

    # --- native_boundary_seam --------------------------------------------
    seam = {}
    seam_fail = []
    for layer, cap in captures.items():
        if layer not in M.BACKGROUND_LAYERS and layer != "composite":
            continue
        scores = []
        for s in cap["samples"]:
            w, h, rows = M.read_bmp_rgb(s["path"])
            per_side = {}
            for side, x in (("left", args.ws_extra),
                            ("right", args.ws_extra + M.NATIVE_WIDTH)):
                sc = M.edge_score(rows, w, h, x)
                sc["hit"] = (sc["ratio"] >= M.SEAM_RATIO
                             and sc["excess"] >= M.SEAM_EXCESS)
                per_side[side] = sc
            scores.append({"frame": s["frame"], "sides": per_side})
        # A layer whose margins are EMPTY necessarily has a hard
        # discontinuity where its painted native window meets the unpainted
        # margin: that is the pillarbox boundary, not a stale-margin seam.
        # The empty margin is the root defect and margin_non_blank already
        # reports it; promoting its shadow to a second independent failure
        # would double-count one cause and bury the real one.
        empty_margin = per_layer.get(layer, {}).get("verdict") == "FAIL"
        entry = {"samples": scores, "camera_motion": cap["camera_motion"],
                 "hscroll_delta": cap["hscroll_delta"],
                 "implied_by_empty_margin": empty_margin}
        for side in ("left", "right"):
            hits = [sc["sides"][side]["hit"] for sc in scores]
            # An authored vertical edge (a wall, a mast) can land on the old
            # 4:3 boundary for ONE sampled frame.  A stale-margin seam stays
            # pinned to that screen coordinate while the camera moves, so the
            # signal is only promoted to a defect when it survives an adjacent
            # sample WITH camera motion.
            corroborated = (all(hits) and cap["camera_motion"]
                            and not empty_margin)
            entry[side] = {"hits": hits, "corroborated": corroborated,
                           "suppressed_by_empty_margin": bool(
                               empty_margin and all(hits))}
            if empty_margin and all(hits):
                rep.find("native_boundary_seam", "informational",
                         "%s %s boundary discontinuity is the PILLARBOX edge: "
                         "this layer paints nothing in the margin at all, so "
                         "the sharp column at x=%d is a consequence of "
                         "background_load_or_render, not an independent seam"
                         % (layer, side,
                            args.ws_extra if side == "left"
                            else args.ws_extra + M.NATIVE_WIDTH),
                         layer=layer, side=side,
                         score=scores[0]["sides"][side])
            elif corroborated:
                seam_fail.append("%s/%s" % (layer, side))
                rep.find("native_boundary_seam", "strong",
                         "%s %s old-4:3 boundary discontinuity is %.2fx "
                         "nearby column edges on two consecutive samples "
                         "with camera motion"
                         % (layer, side, scores[0]["sides"][side]["ratio"]),
                         layer=layer, side=side,
                         frame=scores[0]["frame"],
                         score=scores[0]["sides"][side])
            elif any(hits):
                rep.find("native_boundary_seam", "weak",
                         "%s %s crossed the seam threshold on %d/%d samples "
                         "(camera_motion=%s) -- not corroborated, most likely "
                         "authored geometry at that column"
                         % (layer, side, sum(hits), len(hits),
                            cap["camera_motion"]),
                         layer=layer, side=side)
        seam[layer] = entry
    rep.add("native_boundary_seam", {
        "status": "FAIL" if seam_fail else "PASS",
        "detail": ("corroborated seams: %s" % ",".join(seam_fail)) if seam_fail
        else ("no corroborated discontinuity at x=%d or x=%d%s"
              % (args.ws_extra, args.ws_extra + M.NATIVE_WIDTH,
                 " (boundary edges on %s are the pillarbox itself -- those "
                 "layers paint no margin at all)"
                 % ",".join(l for l, v in seam.items()
                            if v["implied_by_empty_margin"])
                 if any(v["implied_by_empty_margin"] for v in seam.values())
                 else "")),
        "thresholds": {"ratio": M.SEAM_RATIO, "excess": M.SEAM_EXCESS},
        "layers": seam})

    rep.note("ws_shadow_stats", {l: c["ws_shadow_stats"]
                                 for l, c in captures.items()})
    rep.write()
    return 1 if rep.failed else 0


# ── hud-anchor ──────────────────────────────────────────────────────────────
#
# --hud-json shape (see scripts/widescreen/README.md):
#   {"obj": [{"name": "p1_health", "slots": [0, 8], "anchor": "left"},
#            {"name": "timer", "tiles": [64, 79], "anchor": "center"}],
#    "bg":  [{"name": "hud_band", "layer": 2, "lines": [8, 40]}]}
# anchor left -> X must shift by -extra, right -> +extra, center -> 0.

ANCHOR_SHIFT = {"left": -1, "right": +1, "center": 0}


def _hud_slot_indices(spec: dict, slots: list) -> list:
    if "slots" in spec:
        lo, hi = spec["slots"]
        return [s["slot"] for s in slots if lo <= s["slot"] <= hi]
    if "tiles" in spec:
        lo, hi = spec["tiles"]
        return [s["slot"] for s in slots if lo <= s["tile"] <= hi]
    raise M.HarnessError("hud spec %r needs 'slots' or 'tiles'" % spec)


def cmd_hud_anchor(args, gate) -> int:
    out = M.ensure_dir(args.out)
    rep = M.Report("hud_anchor", args.scenario, out, args.build, args.rom,
                   {"SNESRECOMP_WS_EXTRA": ["0", str(args.ws_extra)],
                    "hud_json": args.hud_json}, gate)
    if not args.hud_json:
        rep.add("hud_anchor_obj", {
            "status": "SKIP",
            "detail": "no --hud-json: which OAM slots or tile ids are HUD, "
                      "and which 16:9 edge each one anchors to, is a recon "
                      "result (R5), not something this script may guess -- "
                      "guessing it would make the check unfalsifiable"})
        rep.add("hud_anchor_bg", {"status": "SKIP",
                                  "detail": "no --hud-json (see above)"})
        rep.write()
        return 0
    with open(args.hud_json) as fh:
        hud = json.load(fh)

    sides = {}
    for name, extra in (("native", 0), ("wide", args.ws_extra)):
        with M.Instance(args.port, args.build, args.rom, extra,
                        log_path=os.path.join(out, "%s.log" % name)) as inst:
            info = M.reach_scene(inst.c, args.scenario, gate, args.states_dir,
                                 args.settle, args.load_at)
            oam = M.oam_slots(inst.c, 1, 128)
            ppu = inst.c.j("get_ppu_state")
            windows = {}
            for band in hud.get("bg", []):
                for line in range(band["lines"][0], band["lines"][1] + 1,
                                  max(1, args.window_step)):
                    windows.setdefault(band["name"], {})[line] = \
                        inst.c.j("ppu_window %d %d" % (line, band["layer"]))
            path = os.path.join(out, "%s_f%06d.bmp" % (name, M.frame(inst.c)))
            inst.c.j("screenshot " + M.fwd(path))
        sides[name] = {"entry": info, "oam": oam, "ppu": ppu,
                       "windows": windows, "bmp": path}
    rep.doc["sides"] = {k: {"entry": v["entry"], "bmp": v["bmp"],
                            "widescreen": v["ppu"].get("widescreen"),
                            "oam_frame": v["oam"]["frame"]}
                        for k, v in sides.items()}

    # --- OBJ branch -------------------------------------------------------
    obj_specs = hud.get("obj", [])
    if obj_specs:
        nat = {s["slot"]: s for s in sides["native"]["oam"]["slots"]}
        wid = {s["slot"]: s for s in sides["wide"]["oam"]["slots"]}
        groups = {}
        bad = []
        for spec in obj_specs:
            expect = ANCHOR_SHIFT[spec["anchor"]] * args.ws_extra
            entries = []
            for slot in _hud_slot_indices(spec, sides["native"]["oam"]["slots"]):
                if slot not in wid:
                    entries.append({"slot": slot, "verdict": "FAIL",
                                    "reason": "absent from the wide capture"})
                    bad.append("%s#%d" % (spec["name"], slot))
                    continue
                dx = wid[slot]["raw_x"] - nat[slot]["raw_x"]
                # 9-bit X wraps; the shift is only meaningful modulo 512.
                dx = ((dx + 256) % 512) - 256
                dy = wid[slot]["y"] - nat[slot]["y"]
                ok = dx == expect and dy == 0
                entries.append({"slot": slot, "native_x": nat[slot]["raw_x"],
                                "wide_x": wid[slot]["raw_x"], "dx": dx,
                                "expected_dx": expect,
                                "native_y": nat[slot]["y"],
                                "wide_y": wid[slot]["y"], "dy": dy,
                                "verdict": "PASS" if ok else "FAIL"})
                if not ok:
                    bad.append("%s#%d" % (spec["name"], slot))
            groups[spec["name"]] = {"anchor": spec["anchor"],
                                    "expected_dx": expect, "slots": entries}
            if not entries:
                groups[spec["name"]]["reason"] = \
                    "no OAM slot matched this spec in the 4:3 capture"
        rep.add("hud_anchor_obj", {
            "status": "FAIL" if bad else "PASS",
            "detail": ("misanchored: %s" % ",".join(bad)) if bad
            else "every HUD slot shifted by exactly its anchor's +/-%d with "
                 "Y unchanged" % args.ws_extra,
            "groups": groups})
        if bad:
            rep.find("hud_not_anchored", "strong",
                     "HUD OAM slots did not shift to the 16:9 edges: %s"
                     % ",".join(bad))
    else:
        rep.add("hud_anchor_obj", {"status": "SKIP",
                                  "detail": "--hud-json declares no 'obj' HUD"})

    # --- BG branch --------------------------------------------------------
    bg_specs = hud.get("bg", [])
    if bg_specs:
        ws = sides["wide"]["ppu"].get("widescreen", {})
        budget_ok = int(ws.get("budget", 0)) == args.ws_extra
        bands = {}
        bad = []
        for band in bg_specs:
            rows = []
            for line, w in sorted(sides["wide"]["windows"]
                                  .get(band["name"], {}).items()):
                n = sides["native"]["windows"].get(band["name"], {}).get(line, {})
                edges = w.get("edges")
                # A HUD band that must reach the 16:9 edges has to have its
                # window edges expanded past the native 256 columns.
                expanded = bool(edges) and (max(edges) > M.NATIVE_WIDTH
                                            or min(edges) < 0)
                rows.append({"line": line, "wide_edges": edges,
                             "native_edges": n.get("edges"),
                             "wide_valid": w.get("valid"),
                             "expanded_past_native": expanded})
            bands[band["name"]] = {"layer": band["layer"], "lines": rows}
            if rows and not any(r["expanded_past_native"] for r in rows):
                bad.append(band["name"])
        detail = ("windows never expand past x=%d on: %s"
                  % (M.NATIVE_WIDTH, ",".join(bad))) if bad else \
                 "HUD band windows expand into the margins"
        if not budget_ok:
            bad.append("widescreen.budget=%s != %d"
                       % (ws.get("budget"), args.ws_extra))
        rep.add("hud_anchor_bg", {
            "status": "FAIL" if bad else "PASS", "detail": detail,
            "widescreen": ws, "budget_ok": budget_ok, "bands": bands})
    else:
        rep.add("hud_anchor_bg", {"status": "SKIP",
                                  "detail": "--hud-json declares no 'bg' HUD"})
    rep.write()
    return 1 if rep.failed else 0


# ── text-letterbox ──────────────────────────────────────────────────────────

def cmd_text_letterbox(args, gate) -> int:
    out = M.ensure_dir(args.out)
    rep = M.Report("text_letterbox", args.scenario, out, args.build, args.rom,
                   {"SNESRECOMP_WS_EXTRA": ["0", str(args.ws_extra)],
                    "frames": args.frames}, gate)
    window = resolve_start_frame(args, gate)
    rep.doc["dump_window"] = window
    plan = [("native", 0), ("wide", args.ws_extra)]
    if args.control:
        plan.append(("control", 0))
    sides = {name: capture_side(args, gate, name, extra, args.frames,
                                window["start"], want_backdrop=True)
             for name, extra in plan}
    rep.doc["sides"] = {k: {"entry": v["entry"], "backdrop": v["backdrop"],
                            "widescreen": v["ppu"].get("widescreen"),
                            "dump_window": v["dump_window"],
                            "frames_dumped": len(v["shots"])}
                        for k, v in sides.items()}

    # --- gate: is this actually a text/menu screen? -----------------------
    g = sides["wide"]["entry"]["gate"]
    if not g.get("verified"):
        rep.add("scene_is_text_or_menu", {
            "status": "SKIP",
            "detail": "scene identity UNVERIFIED (--no-gate): the letterbox "
                      "policy is only correct for a non-live screen and this "
                      "run cannot prove the screen is one"})
    else:
        is_text = not g["is_live_fight"]
        rep.add("scene_is_text_or_menu", {
            "status": "PASS" if is_text else "FAIL",
            "detail": "WRAM gate: mode=%s live=%s -> %s"
                      % (g["mode"], g["is_live"],
                         "text/menu" if is_text else "LIVE FIGHT"),
            "gate": g})
        if not is_text:
            rep.find("scene_misclassified", "strong",
                     "text-letterbox was asked to judge a screen the WRAM "
                     "gate calls a live fight; use the `margins` check there")

    # --- margins uniform, and what colour ---------------------------------
    want_rgb = sides["wide"]["backdrop"]["rgb"]
    rows_by_frame = {}
    uni = []
    bad_uniform = []
    off_backdrop = []
    for s in sides["wide"]["shots"][:args.margin_frames]:
        w, h, rows = M.read_bmp_rgb(s["path"])
        rows_by_frame[s["frame"]] = (w, h, rows)
        lu, lc = M.uniform_band(rows, 0, args.ws_extra)
        ru, rc = M.uniform_band(rows, args.ws_extra + M.NATIVE_WIDTH, w)
        entry = {"frame": s["frame"], "left_uniform": lu, "left_colour": lc,
                 "right_uniform": ru, "right_colour": rc,
                 "cgram0_rgb": want_rgb, "path": s["path"]}
        entry["colours_match_backdrop"] = (lc == want_rgb and rc == want_rgb)
        entry["colours_match_cleared"] = (lc == "#000000" and rc == "#000000")
        uni.append(entry)
        if not (lu and ru):
            bad_uniform.append(s["frame"])
        elif not entry["colours_match_backdrop"]:
            off_backdrop.append((s["frame"], lc, rc))
    rep.add("margins_uniform", {
        "status": "FAIL" if bad_uniform else "PASS",
        "detail": ("non-uniform margins on frames %s" % bad_uniform)
        if bad_uniform else "both margins are a single flat colour on all %d "
                            "sampled frames (no stretching, no slicing)"
                            % len(uni),
        "frames": uni})

    # The pillarbox colour is a SEPARATE assertion from uniformity, because
    # the two plausible correct answers differ: the PPU's backdrop (CGRAM 0)
    # is what a "wider screen showing the same scene" implies, while
    # src/gwed_display.c's Bounded branch deliberately memsets the margin
    # columns to opaque BLACK so a later switch out of World mode cannot leave
    # a stale world frame frozen in the margins.  Both are non-stretching and
    # non-slicing; which one ships is a policy call, so this check reports the
    # measurement and only FAILS when the colour is neither.
    colour_bad = [f for f, lc, rc in off_backdrop
                  if not (lc == "#000000" and rc == "#000000")]
    rep.add("margins_are_backdrop_or_cleared", {
        "status": "FAIL" if colour_bad else "PASS",
        "detail": ("margins are neither CGRAM0 (%s) nor cleared black on "
                   "frames %s" % (want_rgb, colour_bad)) if colour_bad else
                  ("margin colour == CGRAM0 %s" % want_rgb
                   if not off_backdrop else
                   "margins are cleared black (#000000), not CGRAM0 %s -- "
                   "gwed_display.c's Bounded branch memsets them; recorded, "
                   "not failed" % want_rgb),
        "cgram0": sides["wide"]["backdrop"],
        "frames_off_backdrop": off_backdrop[:8]})

    # --- PPU widescreen fields --------------------------------------------
    ws = sides["wide"]["ppu"].get("widescreen", {})
    budget = int(ws.get("budget", -1))
    left = int(ws.get("left", -1))
    right = int(ws.get("right", -1))
    ok = budget == args.ws_extra and left == 0 and right == 0
    rep.add("ppu_centered_budget", {
        "status": "PASS" if ok else "FAIL",
        "detail": "widescreen budget=%d left=%d right=%d (want %d/0/0: the "
                  "centring budget exists but no layer extends)"
                  % (budget, left, right, args.ws_extra),
        "widescreen": ws})
    if not ok:
        rep.find("letterbox_policy_not_applied", "strong",
                 "a text/menu screen must use PpuSetExtraSpaceCentered "
                 "(budget=%d, left=right=0); got budget=%d left=%d right=%d"
                 % (args.ws_extra, budget, left, right))

    # --- centre == 4:3 sibling --------------------------------------------
    par = compare_center(sides["native"]["shots"], sides["wide"]["shots"],
                         args.ws_extra)
    control = (compare_center(sides["native"]["shots"],
                              sides["control"]["shots"], 0)
               if args.control else None)
    parity_verdict(par, control, args.frames, rep)
    rep.add("center_matches_native", par)
    if par["status"] == "FAIL":
        rep.find("center_window_not_byte_exact", "strong", par["detail"],
                 first_mismatch=par["first_mismatch"])
    rep.write()
    return 1 if rep.failed else 0


# ── sprite-nocull ───────────────────────────────────────────────────────────

def cmd_sprite_nocull(args, gate) -> int:
    out = M.ensure_dir(args.out)
    expect = []
    if args.expect_margin_object:
        with open(args.expect_margin_object) as fh:
            expect = json.load(fh)
    rep = M.Report("sprite_nocull", args.scenario, out, args.build, args.rom,
                   {"SNESRECOMP_WS_EXTRA": str(args.ws_extra),
                    "SNESRECOMP_LAYER_MASK": M.LAYER_MASKS["obj"],
                    "expect_margin_object": args.expect_margin_object}, gate)
    # OBJ-isolated capture: a sprite that "exists in OAM" but is painted over
    # by a background would be indistinguishable from a culled one in the
    # composite.  Isolating OBJ makes the second stage decisive.
    with M.Instance(args.port, args.build, args.rom, args.ws_extra,
                    layer_mask=M.LAYER_MASKS["obj"],
                    log_path=os.path.join(out, "obj.log")) as inst:
        info = M.reach_scene(inst.c, args.scenario, gate, args.states_dir,
                             args.settle)
        samples = []
        for k in range(args.samples):
            if k:
                M.wait_frame(inst.c, M.frame(inst.c) + args.sample_gap)
            ppu = inst.c.j("get_ppu_state")
            oam = M.oam_slots(inst.c, 1, 128)
            path = os.path.join(out, "obj_s%d_f%06d.bmp" % (k, M.frame(inst.c)))
            r = inst.c.j("screenshot " + M.fwd(path))
            if not r.get("ok"):
                raise M.HarnessError("screenshot failed: %r" % r)
            samples.append({"sample": k, "path": path, "frame": r.get("frame"),
                            "width": r.get("width"), "obsel": ppu.get("obsel"),
                            "obj_enabled": ppu.get("screenEnabled"),
                            "oam": oam})
    rep.doc["entry"] = info

    histogram = {}
    candidates = []
    for s in samples:
        obsel = M.parse_int(s["obsel"])
        w, h, rows = M.read_bmp_rgb(s["path"])
        metrics = M.margin_metrics(s["path"], args.ws_extra)
        backdrop = bytes.fromhex(metrics["dominant_backdrop_rgb"])
        s["backdrop_rgb"] = metrics["dominant_backdrop_rgb"]
        s["margin_metrics"] = metrics["regions"]
        # Per-scanline backdrop: this title's arena backdrop is an HDMA
        # gradient, so the frame-wide mode calls ~85% of an OBJ-isolated
        # capture "ink" and the test decides nothing. See M.row_backdrops.
        row_bd = M.row_backdrops(rows, w)
        for slot in s["oam"]["slots"]:
            interp = M.signed_x_interpretations(slot["raw_x"], args.ws_extra)
            histogram[interp["signed"]] = histogram.get(interp["signed"], 0) + 1
            # Position by the reading the PPU ACTUALLY uses at this margin
            # (PpuDecodeOamX wraps at 256+extra, not at 256), or the sprite
            # the emitter just started emitting gets looked for 512 px away
            # and scored as "in the margin but blank".
            sx = interp["engine"]
            in_left = -args.ws_extra <= sx < 0
            in_right = M.NATIVE_WIDTH <= sx < M.NATIVE_WIDTH + args.ws_extra
            if not (in_left or in_right):
                continue
            if not (0 <= slot["y"] < M.FRAME_HEIGHT):
                continue
            sw, sh = M.obj_size_for(obsel, slot["big"])
            # Frame coordinates: native X 0 sits at framebuffer column extra.
            fx = args.ws_extra + sx
            ink = M.rect_has_ink(rows, w, h, fx, slot["y"], sw, sh, backdrop,
                                 backdrop_rows=row_bd)
            candidates.append({
                "frame": s["frame"], "slot": slot["slot"], "y": slot["y"],
                "tile": slot["tile"], "size": [sw, sh],
                "x_interpretations": interp,
                "margin": "left" if in_left else "right",
                "x_reading_ambiguous": interp["ambiguous"],
                "framebuffer_x": fx, "ink": ink,
                "draws": ink.get("ink", 0) > 0, "image": s["path"]})

    for s in samples:
        s.pop("oam", None)          # the raw 128-slot dump is huge; keep files
    rep.doc["samples"] = samples
    rep.doc["signed_x_histogram"] = {str(k): v for k, v in
                                     sorted(histogram.items())}
    rep.doc["margin_candidates"] = candidates

    # --- stage 1: does the OAM entry EXIST? -------------------------------
    missing = []
    for want in expect:
        hit = [c for c in candidates
               if (("slot" not in want or c["slot"] == want["slot"])
                   and ("tile" not in want or c["tile"] == want["tile"])
                   and ("margin" not in want or c["margin"] == want["margin"]))]
        if not hit:
            missing.append(want)
    if expect:
        rep.add("object_render_or_cull", {
            "status": "FAIL" if missing else "PASS",
            "detail": ("%d declared margin objects never appeared in the "
                       "render-consumed OAM ring" % len(missing)) if missing
            else "every declared margin object is present in the OAM ring",
            "declared": expect, "missing": missing})
        for want in missing:
            rep.find("object_render_or_cull", "strong",
                     "a declared margin object (%r) is absent from the "
                     "render-consumed OAM ring: the guest or the emitter "
                     "culled it before the PPU saw it" % want)
    else:
        rep.add("object_render_or_cull", {
            "status": "SKIP",
            "detail": "no --expect-margin-object: without a declaration of "
                      "what SHOULD be in the margin, an empty margin is "
                      "indistinguishable from a scene with nothing there. "
                      "Observed margin OAM entries are recorded either way.",
            "observed_margin_entries": len(candidates),
            "signed_x_range": [min(histogram) if histogram else None,
                               max(histogram) if histogram else None]})

    # --- stage 2: does it DRAW? -------------------------------------------
    blank = [c for c in candidates if not c["draws"]]
    if candidates:
        rep.add("object_ppu_or_presenter", {
            "status": "FAIL" if blank else "PASS",
            "detail": ("%d/%d margin OAM entries drew no non-backdrop pixels"
                       % (len(blank), len(candidates))) if blank
            else "all %d margin OAM entries painted pixels" % len(candidates),
            "candidates": len(candidates), "blank": blank[:12]})
        for c in blank:
            rep.find("object_ppu_or_presenter", "strong",
                     "OAM slot %d at signed X %d (frame %s) is in the margin "
                     "and on-screen vertically, but the OBJ-isolated capture "
                     "has no non-backdrop pixel in its %dx%d rect: the PPU or "
                     "the presenter dropped it, not the guest"
                     % (c["slot"], c["x_interpretations"]["signed"],
                        c["frame"], c["size"][0], c["size"][1]),
                     frame=c["frame"], image=c["image"])
    else:
        rep.add("object_ppu_or_presenter", {
            "status": "SKIP",
            "detail": "no OAM entry landed in [-%d,0) or [%d,%d) with an "
                      "on-screen Y in %d sampled frames -- nothing to draw"
                      % (args.ws_extra, M.NATIVE_WIDTH,
                         M.NATIVE_WIDTH + args.ws_extra, len(samples))})
    rep.write()
    return 1 if rep.failed else 0


# ── driver ──────────────────────────────────────────────────────────────────

def add_common(p):
    p.add_argument("--build", default=M.DEFAULT_BUILD,
                   help="exe directory (needs SNESRECOMP_ENABLE_TRACE=ON)")
    p.add_argument("--rom", default=M.DEFAULT_ROM)
    p.add_argument("--states-dir", default=M.DEFAULT_STATES_DIR)
    p.add_argument("--scenario", default="boot_attract",
                   help="boot_attract[:frame] | attract_fight | state:<name>")
    p.add_argument("--settle", type=int, default=M.STATE_SETTLE_DEFAULT,
                   help="guest frames to free-run after a savestate entry")
    p.add_argument("--ws-extra", type=int, default=M.DEFAULT_WS_EXTRA,
                   help="pinned per-side margin (43 = 16:9 at 7:6 PAR)")
    p.add_argument("--port", type=int, default=M.PORT_BASE,
                   help="debug-server port (this harness owns 4481-4489)")
    p.add_argument("--gate-json", default=M.DEFAULT_GATE_JSON,
                   help="recon gate spec; scene identity comes from WRAM")
    p.add_argument("--no-gate", action="store_true",
                   help="run without a gate spec; scene identity is reported "
                        "UNVERIFIED instead of asserted")
    p.add_argument("--out", default=None, help="evidence directory")
    p.add_argument("--sample-gap", type=int, default=24,
                   help="guest frames between the two corroborating samples")
    p.add_argument("--control", dest="control", action="store_true",
                   default=True,
                   help="also run a second 4:3 process and compare it "
                        "against the first, so a scene that is not "
                        "frame-reproducible across processes cannot be "
                        "blamed on widescreen (default on)")
    p.add_argument("--no-control", dest="control", action="store_false")
    p.add_argument("--load-at", type=int, default=M.LOAD_AT_FRAME,
                   help="fixed guest frame at which a savestate is loaded; "
                        "loading on connection instead would put the two "
                        "compared processes at two different guest moments")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("center-parity", help="wide centre == 4:3, byte-exact")
    add_common(p)
    p.add_argument("--frames", type=int, default=60,
                   help="frames compared per side (spec minimum 60)")
    p.set_defaults(fn=cmd_center_parity)

    p = sub.add_parser("margins", help="every layer paints both margins")
    add_common(p)
    p.add_argument("--layers", default="composite,bg1,bg2,bg3,bg4,obj")
    p.set_defaults(fn=cmd_margins)

    p = sub.add_parser("hud-anchor", help="HUD rides the 16:9 edges")
    add_common(p)
    p.add_argument("--hud-json", default=None,
                   help="HUD spec (slot/tile ranges + anchor); without it the "
                        "check reports SKIP with the reason")
    p.add_argument("--window-step", type=int, default=8,
                   help="scanline stride when sampling ppu_window on a band")
    p.set_defaults(fn=cmd_hud_anchor)

    p = sub.add_parser("text-letterbox", help="text/menu screens pillarbox")
    add_common(p)
    p.add_argument("--frames", type=int, default=60)
    p.add_argument("--margin-frames", type=int, default=8,
                   help="frames whose margins are checked for uniformity")
    p.set_defaults(fn=cmd_text_letterbox)

    p = sub.add_parser("sprite-nocull", help="margin sprites exist and draw")
    add_common(p)
    p.add_argument("--samples", type=int, default=4,
                   help="OAM+OBJ capture samples spread over the scene")
    p.add_argument("--expect-margin-object", default=None,
                   help="JSON list of objects that MUST appear in a margin; "
                        "without it a missing entry cannot be a failure")
    p.set_defaults(fn=cmd_sprite_nocull)

    args = ap.parse_args(argv)
    try:
        M.require_windows_python()
    except M.HarnessError as e:
        print("HARNESS-ERROR %s" % e, file=sys.stderr)
        return 2
    if args.out is None:
        args.out = os.path.join(M.VERIFY_ROOT, M.utc_stamp(),
                                "%s_%s" % (args.cmd,
                                           args.scenario.replace(":", "_")))
    try:
        gate = M.load_gate(None if args.no_gate else args.gate_json,
                           allow_missing=True)
        if not gate.verified:
            print("NOTE scene identity UNVERIFIED: no gate spec "
                  "(%s)" % (args.gate_json or "--no-gate"))
        return args.fn(args, gate)
    except M.HarnessError as e:
        print("HARNESS-ERROR %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
