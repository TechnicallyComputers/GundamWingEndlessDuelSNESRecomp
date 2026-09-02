"""HUD verification renders for the elastic anchor band (Beads beads-8wg.9.13.5).

Captures the three HUD scenes -- the attract fight, the KO / "1P WIN" screen
and the victory quote -- in both the authentic 4:3 frame and the 16:9 frame,
and writes PNGs at 3x with the 7:6 CRT pixel aspect applied, plus a zoomed
strip of the HUD band alone so the anchoring and the elastic seams can be
judged by eye.

This is a rendering aid, not a verdict: ws_verdicts.py hud-anchor is what
asserts. Run from PowerShell with the native Windows interpreter.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ws_metrics as M  # noqa: E402

# Scene table, mirroring ws_recon.py's: the two frozen HUD screens are banked
# savestates, the live fight is reached by polling the WRAM gate.
# `after` is how the moment inside the state is chosen. A plain frame count is
# what recon used, but it is not stable: pre_stage_ending_dialogue + 400 frames
# now lands on the per-pilot epilogue art, not the KO banner. Where a scene
# names a WRAM gate instead, the capture polls for it -- mode/sub-mode plus
# BG1 actually pointing at the arena tilemap, which is the same conjunction
# gwed_display.c's own resolver uses, so the shot is taken on a frame the HUD
# policy is provably live on.
SCENES = [
    ("attract_fight", None, {}),
    ("ko_1p_win", "pre_stage_ending_dialogue",
     {"mode": 0x0010, "sub": 0x001E, "arena": True, "settle": 8,
      "limit": 900}),
    ("victory_quote", "pre_quote",
     {"mode": 0x0010, "sub": 0x0014, "arena": True, "settle": 8,
      "limit": 900}),
]

MODE_ADDR, SUBMODE_ADDR = 0x1000, 0x1004

# The definitive "the fight HUD is on this screen" test, and the reason a
# mode-word match is not enough: $7E:1000/$1004 read "battle family / round
# end" during the blanked inter-stage transition too, and a capture taken
# there is a black frame (measured). BG1's tilemap row 58 columns 14..17 hold
# the four tiles that spell TIME, identical in every HUD scene (recon B), at
# map word 0x734E of the 64x64 map based at word 0x6800 -- byte 0xE69C. That
# is emulated VRAM, not a rendered pixel, so it is a legal scene gate.
TIME_LABEL_VRAM_BYTE = 0xE69C
TIME_LABEL_TILES = (0x0751, 0x0752, 0x0753, 0x0754)


def time_label_present(c):
    hexs = c.j("dump_vram %x 8" % TIME_LABEL_VRAM_BYTE)["hex"]
    b = bytes.fromhex(hexs)
    words = tuple(b[i] | (b[i + 1] << 8) for i in range(0, 8, 2))
    return words == TIME_LABEL_TILES


def wait_hud_scene(c, spec):
    """Poll the WRAM mode words and BG1's configuration for a HUD frame.

    Never samples pixels (standing rule) and never pauses the guest: it
    free-runs and reads.
    """
    limit = spec.get("limit", 900)
    start = M.frame(c)
    seen = set()
    while M.frame(c) - start < limit:
        mode = M.read_ram(c, MODE_ADDR, 2)
        sub = M.read_ram(c, SUBMODE_ADDR, 2)
        seen.add((mode, sub))
        if mode == spec["mode"] and sub == spec["sub"]:
            ok = True
            if spec.get("arena"):
                ppu = c.j("get_ppu_state")
                ok = (int(str(ppu["bgXsc"][0]), 16) == 0x6B and
                      (int(str(ppu["bgTileAdr"]), 16) & 0xF) == 0 and
                      # not force-blank, and the HUD really is loaded
                      not (int(str(ppu["inidisp"]), 16) & 0x80) and
                      time_label_present(c))
            if ok:
                M.wait_frame(c, M.frame(c) + spec.get("settle", 8))
                return M.frame(c)
        M.wait_frame(c, M.frame(c) + 2)
    raise M.HarnessError("the HUD gate (mode=0x%04x sub=0x%04x) was never "
                         "satisfied within %d frames of the load; saw %s"
                         % (spec["mode"], spec["sub"], limit,
                            sorted("0x%04x/0x%04x" % k for k in seen)))

# The HUD band, from recon (analysis/widescreen/recon/hud.json).
BAND_Y0, BAND_Y1 = 22, 73


def png(rows, width, height, path, scale=3, par_num=7, par_den=6):
    """Nearest-neighbour upscale with the CRT pixel aspect, then write a PNG."""
    from PIL import Image
    im = Image.frombytes("RGB", (width, height), b"".join(rows))
    im = im.resize((width * scale * par_num // par_den, height * scale),
                   Image.NEAREST)
    im.save(path)
    return path


def capture(scene, state, spec, args, extra, tag, layer_mask=None):
    out = M.ensure_dir(args.out)
    suffix = tag if layer_mask is None else "%s_bg1" % tag
    bmp = os.path.join(out, "%s_%s.bmp" % (scene, suffix))
    with M.Instance(args.port, args.build, args.rom, extra,
                    layer_mask=layer_mask,
                    log_path=os.path.join(out, "%s_%s.log"
                                          % (scene, suffix))) as inst:
        c = inst.c
        if state:
            M.wait_frame(c, args.load_at)
            M.load_state(c, state, args.states_dir)
            wait_hud_scene(c, spec)
        else:
            gate = M.load_gate(args.gate_json, False)
            M.reach_scene(c, "attract_fight", gate, args.states_dir, 180, 200)
        f = M.frame(c)
        c.j("screenshot " + M.fwd(bmp))
    w, h, rows = M.read_bmp_rgb(bmp)
    return {"bmp": bmp, "frame": f, "w": w, "h": h, "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True)
    ap.add_argument("--rom", required=True)
    ap.add_argument("--states-dir", required=True)
    ap.add_argument("--gate-json",
                    default=os.path.join(os.path.dirname(
                        os.path.dirname(os.path.dirname(
                            os.path.abspath(__file__)))),
                        "analysis", "widescreen", "recon", "gate.json"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=4477)
    ap.add_argument("--ws-extra", type=int, default=43)
    ap.add_argument("--load-at", type=int, default=200)
    ap.add_argument("--scenes", default="attract_fight,ko_1p_win,victory_quote")
    ap.add_argument("--isolate", action="store_true",
                    help="also capture BG1-isolated frames (SNESRECOMP_LAYER_"
                         "MASK), which is where the chrome's own stretch is "
                         "judged")
    args = ap.parse_args()
    M.require_windows_python()
    want = args.scenes.split(",")

    made = []
    for scene, state, spec in SCENES:
        if scene not in want:
            continue
        # The composite is what a player sees; the BG1-isolated frame is what
        # shows whether the LAYER's own chrome stretched cleanly, with none of
        # the arena behind it to confuse the eye.
        plan = [("4x3", 0, None), ("wide", args.ws_extra, None)]
        if args.isolate:
            plan += [("4x3", 0, M.LAYER_MASKS["bg1"]),
                     ("wide", args.ws_extra, M.LAYER_MASKS["bg1"])]
        for tag, extra, mask in plan:
            # One retry: these are long free-runs and the debug connection
            # occasionally drops mid-poll. A retry is honest here because the
            # capture is re-driven from a fresh process through the same WRAM
            # gate, not resumed from a half-known state.
            for attempt in range(2):
                try:
                    cap = capture(scene, state, spec, args, extra, tag, mask)
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt:
                        raise
                    print("  retrying %s %s after %r" % (scene, tag, e))
            suffix = tag if mask is None else "%s_bg1" % tag
            base = os.path.join(args.out, "%s_%s" % (scene, suffix))
            made.append(png(cap["rows"], cap["w"], cap["h"],
                            base + "_frame.png"))
            band = cap["rows"][BAND_Y0:BAND_Y1]
            made.append(png(band, cap["w"], len(band),
                            base + "_hudstrip.png", scale=6))
            print("%-14s %-9s frame=%d %dx%d -> %s"
                  % (scene, suffix, cap["frame"], cap["w"], cap["h"],
                     os.path.basename(base)))
    print(chr(10).join(made))


if __name__ == "__main__":
    main()
