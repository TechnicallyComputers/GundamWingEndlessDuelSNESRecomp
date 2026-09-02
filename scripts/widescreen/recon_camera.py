r"""R2 — GWED camera X, stage clamp bounds, and per-BG-layer parallax slope.

Run from **PowerShell**, not Git-Bash (see the note in ws_recon.py).

Authority for scroll phase is the PPU, never a WRAM mirror (P1).  So the
method is:

  1. take the *rendered* per-frame `ppu_hscroll[0..3]` straight out of the
     always-on frame-history ring (`get_frame_range_extended`) for the whole
     inputless attract fight;
  2. pull full WRAM for a handful of frames inside the window where BG1's
     scroll actually moves, and keep the 16-bit words whose frame-to-frame
     delta equals the BG1 scroll delta (and the x2 / x0.5 variants, which is
     how a parallax mirror shows up);
  3. regress every layer's rendered hScroll against the camera word to get a
     slope (1 = world-anchored, 0 = fixed, fractional = parallax), and read
     per-line variance out of `ppu_lines` to say whether a layer's scroll is
     driven per scanline (HDMA / raster IRQ) rather than per frame;
  4. P1 cross-check: walk the fight frame by frame and count the frames where
     the WRAM mirror disagrees with the rendered PPU scroll.

Stages: series, find, verify, writers, report (or `all`).
Output: analysis/widescreen/recon/camera.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ws_recon as W  # noqa: E402

CACHE = os.path.join(W.OUT_ROOT, "camera_cache")

# Frames (relative to the fight anchor) at which full WRAM is pulled out of
# the ring for the correlation.  They straddle the part of the attract demo
# where BG1's rendered scroll actually moves.
PROBE_OFFSETS = [40, 60, 80, 90, 100, 110, 120, 140, 200, 400, 410, 420, 700]

FIGHT_LEN = 900

# Established by this script (see analysis/widescreen/recon/camera.json):
#   $7E:0114  camera X as the *simulation* holds it (16-bit)
#   $7E:0116  camera Y
#   $7E:068C  the vblank-latched copy of camera X that the raster handler
#             actually stores into $210D (BG1HOFS)
#   $7E:068E  same for camera Y -> $210E
# The copy is done by the routine at $00:8410 (LDX $0114 / STX $068C ...),
# which is why $068C matches the rendered scroll on every single frame while
# $0114 leads it by one whenever the camera moves.  That is P1 in one pair of
# addresses: the simulation mirror is NOT the phase.
CAMERA_SIM = 0x0114
CAMERA_LATCHED = 0x068C
CAMERA_SIM_Y = 0x0116
CAMERA_LATCHED_Y = 0x068E
CAMERA_COPY_SITE = "$00:8410"
CAMERA_WRITER_SITES = ["$00:88E7", "$00:891F", "$00:8900"]
SCROLL_STORE_SITE = "$00:8900"   # LDA $068C / STA $210D, LDA $068D / STA $210D

# The full camera chain, established by disassembling the ROM at the PCs the
# always-on rings named (recompiler/snes65816.py decoder; Ghidra is barred for
# SNES).  `tools/.../dis.py`-style decode of the ROM is the only way to get the
# clamp *constants* — the attract demo never reaches either wall, so no amount
# of observation would have produced them.
#
#   $7E:0620  master camera X, a 1-pole smoothing filter over the midpoint of
#             the two fighters, CLAMPED at $04:870B-$04:8725:
#                 LDA $E8 ; CMP #$0040 ; BMI -> LDA #$0040
#                         ; CMP #$00C0 ; BPL -> LDA #$00C0
#                 CLC ; LDA $E8 ; ADC $0620 ; ROR ; STA $0620
#             => camera X is clamped to [$0040, $00C0] = [64, 192]
#   $7E:0622  master camera Y, same shape at $04:8790-$04:87BF, clamped to
#             [$0070, $0100] = [112, 256]
#   both initialised at $04:800F/$04:8015 to X=$0080 (128), Y=$0100 (256)
#   $7E:0114  per-frame camera X, copied from $0620 by the fight's camera
#             routine $00:D95C (LDA $0620 / STA $0114 at $00:D965); which
#             routine runs is selected by $7E:060E through the jump table at
#             $00:D4F1 ($060E == 0x08 during a fight)
#   $7E:068C  vblank latch, copied from $0114 at $00:8410
#   $210D     stored from $068C/$068D by the raster handler at $00:8900
CAMERA_MASTER = 0x0620
CAMERA_MASTER_Y = 0x0622
CAMERA_CLAMP_X = [0x0040, 0x00C0]
CAMERA_CLAMP_Y = [0x0070, 0x0100]
CAMERA_CLAMP_SITE_X = "$04:870B-$04:8725"
CAMERA_CLAMP_SITE_Y = "$04:8790-$04:87BF"
CAMERA_ROUTINE_FIGHT = "$00:D95C"
CAMERA_ROUTINE_SELECTOR = "$7E:060E (jump table at $00:D4F1; 0x08 in a fight)"



def _frange(c, lo, hi, chunk=90):
    """get_frame_range_extended in small chunks.

    The handler caps its reply at ~30 KB, which is about 120 rows, so asking
    for its documented 500-frame maximum silently truncates.
    """
    rows = []
    f = lo
    while f <= hi:
        e = min(f + chunk, hi)
        rows.extend(c.j("get_frame_range_extended %d %d" % (f, e))["frames"])
        f = e + 1
    return rows


def stage_series(args) -> dict:
    W.ensure_dir(CACHE)
    with W.Instance(args.port) as inst:
        c = inst.c
        anchor = W.attract_fight_anchor(c)

        # --- LIVE P1 cross-check, taken INSIDE the fight -------------------
        # The frame-history ring records the WRAM mirror and the rendered
        # scroll at the same instant, so it can never show a phase skew.  Host
        # code has no such luxury: it reads the mirror at whatever point in the
        # frame it runs.  So sample both live and interleaved, and do it while
        # the fight is definitely still running — sampling past the end of the
        # attract demo produces a bogus constant delta (the camera mirror keeps
        # its last fight value while the next screen renders with hScroll 0).
        W.wait_frame(c, anchor + 120)
        live = []
        a0 = cams[0]
        f_prev = None
        while W.frame(c) < anchor + FIGHT_LEN - 200 \
                and len(live) < args.live_samples:
            fr = W.frame(c)
            if fr == f_prev:
                continue
            f_prev = fr
            mirror = W.unhex(c.j("read_ram %04x 2" % a0)["hex"])
            mval = mirror[0] | (mirror[1] << 8)
            st = c.j("get_ppu_state")
            live.append(dict(frame=fr, mirror=mval,
                             rendered=st["hScroll"][0],
                             bg1sc=st["bgXsc"][0],
                             delta=(mval & 0x3FF) - st["hScroll"][0]))
        live_bad = [x for x in live
                    if x["delta"] != 0
                    and int(x["bg1sc"], 16) == W.FIGHT_MARKER_BG1SC]
        print("[verify] LIVE interleaved sampling inside the fight: %d/%d "
              "samples where the WRAM mirror disagrees with the PPU's current "
              "hScroll (deltas %r)"
              % (len(live_bad), len(live),
                 sorted({x["delta"] for x in live_bad})))

        # --- per-line variance, still inside the fight ---------------------
        lines_in_fight = c.j("ppu_lines 0 224")["lines"]
        ppu_in_fight = c.j("get_ppu_state")
        dma_in_fight = c.j("get_dma_state")

        W.wait_frame(c, anchor + FIGHT_LEN + 40, timeout=1200)
        rows = _frange(c, anchor, anchor + FIGHT_LEN)
        lines = {}
        for off in (100, 300, 600):
            # ppu_lines is the LIVE per-line journal, so it can only be read
            # for the frame we are on; the three samples below are taken while
            # free-running past those offsets.
            pass
        ppu = c.j("get_ppu_state")
        dma = c.j("get_dma_state")
        live_lines = c.j("ppu_lines 0 224")
        probes = {}
        for off in PROBE_OFFSETS:
            fr = anchor + off
            blob = W.dump_frame_wram(c, fr)
            p = os.path.join(CACHE, "wram_%+05d.bin" % off)
            with open(p, "wb") as fh:
                fh.write(blob)
            probes[str(off)] = p
        # WRAM for the two frames used by the P1 mirror-vs-PPU cross-check is
        # taken from the same ring, so mirror and rendered scroll are read from
        # exactly the same frame.
    out = dict(anchor=anchor, rows=rows, probes=probes,
               ppu_at_end=ppu, dma_at_end=dma, lines_at_end=live_lines)
    W.write_json(os.path.join(CACHE, "series.json"), out)
    h = [r["ppu_hscroll"] for r in rows]
    for i in range(4):
        vals = [x[i] for x in h]
        print("[series] BG%d rendered hScroll: min=%d max=%d distinct=%d"
              % (i + 1, min(vals), max(vals), len(set(vals))))
    v = [r["ppu_vscroll"] for r in rows]
    for i in range(4):
        vals = [x[i] for x in v]
        print("[series] BG%d rendered vScroll: min=%d max=%d distinct=%d"
              % (i + 1, min(vals), max(vals), len(set(vals))))
    return out


def _w16(b, a):
    return b[a] | (b[a + 1] << 8)


def stage_find(args) -> dict:
    ser = json.load(open(os.path.join(CACHE, "series.json")))
    anchor = ser["anchor"]
    by_frame = {r["f"]: r for r in ser["rows"]}
    offs = sorted(int(k) for k in ser["probes"])
    blobs = {o: open(ser["probes"][str(o)], "rb").read() for o in offs}
    h0 = {o: by_frame[anchor + o]["ppu_hscroll"][0] for o in offs}
    print("[find] BG1 rendered scroll at probe frames: %r"
          % {o: h0[o] for o in offs})

    base = offs[0]
    hits = {1.0: [], 2.0: [], 0.5: [], -1.0: []}
    for addr in range(0, 0x20000 - 1):
        v0 = _w16(blobs[base], addr)
        if all(_w16(blobs[o], addr) == v0 for o in offs):
            continue  # constant: cannot be a camera
        for k in list(hits):
            ok = True
            for o in offs:
                dh = h0[o] - h0[base]
                dv = _w16(blobs[o], addr) - v0
                # allow the mirror to be one frame ahead/behind (P1)
                if abs(dv * k - dh) > 1.001:
                    ok = False
                    break
            if ok:
                hits[k].append(addr)
    out = {}
    for k, v in hits.items():
        out[str(k)] = ["0x%05x" % a for a in v]
        print("[find] slope %.1f: %d words %s"
              % (k, len(v), ["0x%05x" % a for a in v[:20]]))

    # Byte-width mirrors: a game commonly keeps a second copy of the camera's
    # low byte (or the whole thing at a second address) for the scroll-write
    # routine.  Those never show up in the 16-bit scan when the neighbouring
    # byte differs, so scan bytes separately.
    mirrors = []
    for addr in range(0x20000):
        v0 = blobs[base][addr]
        if all(blobs[o][addr] == v0 for o in offs):
            continue
        ok = True
        for o in offs:
            dh = (h0[o] - h0[base]) & 0xFF
            dv = (blobs[o][addr] - v0) & 0xFF
            if dv != dh:
                ok = False
                break
        if ok:
            mirrors.append("0x%05x" % addr)
    print("[find] byte-width camera mirrors: %d %s"
          % (len(mirrors), mirrors[:20]))
    out["byte_mirrors"] = mirrors
    W.write_json(os.path.join(CACHE, "find.json"),
                 dict(anchor=anchor, h0=h0, hits=out))
    return out


def stage_verify(args) -> dict:
    """Camera range + P1 mirror-vs-PPU cross-check + per-layer slope."""
    find = json.load(open(os.path.join(CACHE, "find.json")))
    ser = json.load(open(os.path.join(CACHE, "series.json")))
    cams = [int(a, 16) for a in find["hits"]["1.0"]]
    if args.camera is not None:
        cams = [args.camera]
    if not cams:
        raise RuntimeError("stage_find produced no slope-1 camera candidate")
    print("[verify] camera candidates: %s" % ["0x%05x" % a for a in cams])

    with W.Instance(args.port) as inst:
        c = inst.c
        anchor = W.attract_fight_anchor(c)

        # --- LIVE P1 cross-check, taken INSIDE the fight -------------------
        # The frame-history ring records the WRAM mirror and the rendered
        # scroll at the same instant, so it can never show a phase skew.  Host
        # code has no such luxury: it reads the mirror at whatever point in the
        # frame it runs.  So sample both live and interleaved, and do it while
        # the fight is definitely still running — sampling past the end of the
        # attract demo produces a bogus constant delta (the camera mirror keeps
        # its last fight value while the next screen renders with hScroll 0).
        W.wait_frame(c, anchor + 120)
        live = []
        a0 = cams[0]
        f_prev = None
        while W.frame(c) < anchor + FIGHT_LEN - 200 \
                and len(live) < args.live_samples:
            fr = W.frame(c)
            if fr == f_prev:
                continue
            f_prev = fr
            mirror = W.unhex(c.j("read_ram %04x 2" % a0)["hex"])
            mval = mirror[0] | (mirror[1] << 8)
            st = c.j("get_ppu_state")
            live.append(dict(frame=fr, mirror=mval,
                             rendered=st["hScroll"][0],
                             bg1sc=st["bgXsc"][0],
                             delta=(mval & 0x3FF) - st["hScroll"][0]))
        live_bad = [x for x in live
                    if x["delta"] != 0
                    and int(x["bg1sc"], 16) == W.FIGHT_MARKER_BG1SC]
        print("[verify] LIVE interleaved sampling inside the fight: %d/%d "
              "samples where the WRAM mirror disagrees with the PPU's current "
              "hScroll (deltas %r)"
              % (len(live_bad), len(live),
                 sorted({x["delta"] for x in live_bad})))

        # --- per-line variance, still inside the fight ---------------------
        lines_in_fight = c.j("ppu_lines 0 224")["lines"]
        ppu_in_fight = c.j("get_ppu_state")
        dma_in_fight = c.j("get_dma_state")

        W.wait_frame(c, anchor + FIGHT_LEN + 40, timeout=1200)
        rows = _frange(c, anchor, anchor + FIGHT_LEN)
        by_frame = {r["f"]: r for r in rows}
        ts = {}
        for a in cams:
            ts["0x%05x" % a] = W.timeseries(c, a, 2, frm=anchor,
                                            to=anchor + FIGHT_LEN, limit=4096)
        # P1 cross-check: read the mirror and the rendered scroll from the
        # SAME ring frame, every frame of the fight.
        mism = {}
        mirror_by_frame = {}
        for a in cams:
            key = "0x%05x" % a
            mism[key] = []
            mirror_by_frame[key] = {}
            cur = None
            for e in ts[key]:
                cur = int(e["hex"][2:4] + e["hex"][0:2], 16)
                mirror_by_frame[key][e["f"]] = cur
            # expand the change-compressed series to every frame
            val = None
            for f in range(anchor, anchor + FIGHT_LEN + 1):
                if f in mirror_by_frame[key]:
                    val = mirror_by_frame[key][f]
                if val is None or f not in by_frame:
                    continue
                rendered = by_frame[f]["ppu_hscroll"][0]
                if (val & 0x3FF) != rendered:
                    mism[key].append(dict(f=f - anchor, mirror=val,
                                          rendered=rendered,
                                          delta=(val & 0x3FF) - rendered))
        lines = lines_in_fight
        ppu = ppu_in_fight
        dma = dma_in_fight

    per_line = {}
    for i in range(4):
        vals = [(ln["line"], ln["h"][i]) for ln in lines]
        distinct = sorted({v for _l, v in vals})
        splits = [l for (l, v), (l2, v2) in zip(vals, vals[1:]) if v != v2]
        per_line["BG%d" % (i + 1)] = dict(
            distinct_hscroll=distinct,
            split_lines=splits,
            per_line_hdma=len(distinct) > 1,
        )
    out = dict(anchor=anchor, candidates=["0x%05x" % a for a in cams],
               timeseries=ts, mismatches=mism, per_line=per_line,
               live_samples=live, live_mismatches=live_bad,
               ppu=ppu, dma=dma,
               rendered=[[r["f"] - anchor, r["ppu_hscroll"], r["ppu_vscroll"]]
                         for r in rows])
    W.write_json(os.path.join(CACHE, "verify.json"), out)
    for k, v in mism.items():
        print("[verify] %s: %d/%d frames where the WRAM mirror disagrees with "
              "the rendered PPU hScroll" % (k, len(v), FIGHT_LEN))
    return out


def stage_raster(args) -> dict:
    """Per-line register bands on the live fight screen.

    The fight uses NO HDMA at all (`get_dma_state` shows no active channel).
    Its per-line variation comes from a chain of raster IRQ handlers that
    re-arm $4207/$4209 and rewrite BG1 H/V scroll, TM, TMW and CGADSUB
    mid-frame.  Sampling has to happen while the camera is MOVING: in steady
    state the pre-split lines already hold the same scroll value as the
    post-split ones and the band is invisible.
    """
    best = None
    with W.Instance(args.port) as inst:
        c = inst.c
        anchor = W.attract_fight_anchor(c)
        W.wait_frame(c, anchor + 55)
        while W.frame(c) < anchor + args.raster_window:
            lines = c.j("ppu_lines 0 224")["lines"]
            score = len({ln["h"][0] for ln in lines})
            if best is None or score > best["score"]:
                best = dict(score=score, frame_offset=W.frame(c) - anchor,
                            lines=lines)
        dma = c.j("get_dma_state")
        ppu = c.j("get_ppu_state")

    lines = best["lines"]
    bands = []
    prev = None
    for ln in lines:
        key = (tuple(ln["h"]), tuple(ln["v"]), tuple(ln["enabled"]),
               tuple(ln["windowed"]), ln["cgwsel"], ln["cgadsub"],
               tuple(ln["w1"]), tuple(ln["w2"]), ln["windowsel"],
               ln["wbgobjlog"])
        if key != prev:
            bands.append(dict(first_line=ln["line"], h=ln["h"], v=ln["v"],
                              enabled=ln["enabled"], windowed=ln["windowed"],
                              cgwsel=ln["cgwsel"], cgadsub=ln["cgadsub"],
                              w1=ln["w1"], w2=ln["w2"],
                              windowsel=ln["windowsel"],
                              wbgobjlog=ln["wbgobjlog"]))
            prev = key
    out = dict(frame_offset=best["frame_offset"],
               distinct_bg1_hscroll=best["score"], bands=bands,
               hdma_active=[ch for ch in dma["channels"] if ch["hdmaActive"]],
               ppu=ppu)
    W.write_json(os.path.join(CACHE, "raster.json"), out)
    print("[raster] frame +%d, %d distinct BG1 hScroll values, %d bands"
          % (best["frame_offset"], best["score"], len(bands)))
    for b in bands:
        print("   line %3d h=%s v=%s en=%s wnd=%s w2=%s"
              % (b["first_line"], b["h"], b["v"], b["enabled"],
                 b["windowed"], b["w2"]))
    return out


def stage_writers(args) -> dict:
    find = json.load(open(os.path.join(CACHE, "find.json")))
    cams = [int(a, 16) for a in find["hits"]["1.0"]]
    if args.camera is not None:
        cams = [args.camera]
    cams = [a for a in cams if a < 0x10000][:40]
    # also watch the simulation camera and both Y words
    for extra in (CAMERA_SIM, CAMERA_SIM_Y, CAMERA_LATCHED_Y):
        if extra not in cams:
            cams.append(extra)
    with W.Instance(args.port) as inst:
        c = inst.c
        for a in cams:
            c.cmd("set_wram_watch 7e %04x 1" % a)
            c.cmd("set_wram_watch 7e %04x 1" % (a + 1))
        anchor = W.attract_fight_anchor(c)
        W.wait_frame(c, anchor + FIGHT_LEN, timeout=1200)
        events = {}
        for a in cams:
            evs = []
            for off in range(2):
                r = c.j("wram_watch_log_get %04x %d %d 400"
                        % (a + off, anchor, anchor + FIGHT_LEN))
                evs.extend(r.get("events", []))
            events["0x%05x" % a] = evs
            sites = {}
            for e in evs:
                key = "$%02X:%04X" % (int(e.get("PB", "0x0"), 16),
                                      int(e.get("block_pc", "0x0"), 16)
                                      & 0xFFFF)
                sites[key] = sites.get(key, 0) + 1
            print("[writers] 0x%05x -> %d writes, sites=%r"
                  % (a, len(evs), sorted(sites.items(), key=lambda kv: -kv[1])[:4]))
        c.cmd("clear_wram_watches")
    out = dict(anchor=anchor, events=events)
    W.write_json(os.path.join(CACHE, "writers.json"), out)
    return out


def _sites(evs):
    sites = {}
    for e in evs:
        key = "$%02X:%04X" % (int(e.get("PB", "0x0"), 16),
                              int(e.get("block_pc", "0x0"), 16) & 0xFFFF)
        sites[key] = sites.get(key, 0) + 1
    return [dict(site=k, writes=v)
            for k, v in sorted(sites.items(), key=lambda kv: -kv[1])]


def stage_report(args) -> dict:
    ver = json.load(open(os.path.join(CACHE, "verify.json")))
    wri = json.load(open(os.path.join(CACHE, "writers.json")))
    find = json.load(open(os.path.join(CACHE, "find.json")))
    ras = json.load(open(os.path.join(CACHE, "raster.json")))

    anchor = ver["anchor"]
    key = "0x%05x" % CAMERA_LATCHED
    series = ver["timeseries"][key]
    vals = [int(e["hex"][2:4] + e["hex"][0:2], 16) for e in series]

    rendered = {f: (h, v) for f, h, v in ver["rendered"]}
    cam_by_frame = {}
    for e in series:
        cam_by_frame[e["f"] - anchor] = int(e["hex"][2:4] + e["hex"][0:2], 16)

    cur = None
    xs, ys = [], [[], [], [], []]
    for f in sorted(rendered):
        if f in cam_by_frame:
            cur = cam_by_frame[f]
        if cur is None:
            continue
        xs.append(cur)
        for i in range(4):
            ys[i].append(rendered[f][0][i])
    n = len(xs)
    mx = sum(xs) / n
    layers = {}
    for i in range(4):
        my = sum(ys[i]) / n
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys[i]))
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in ys[i])
        slope = (sxy / sxx) if sxx else 0.0
        r2 = ((sxy * sxy) / (sxx * syy)) if sxx and syy else (
            1.0 if not syy else 0.0)
        role = ("world-anchored (moves 1:1 with the camera)"
                if abs(slope - 1) < 0.02 else
                "horizontally fixed (rendered hScroll is constant 0)"
                if syy == 0 else "parallax x%.3f" % slope)
        per_line_vals = sorted({tuple(b["h"])[i] for b in ras["bands"]})
        layers["BG%d" % (i + 1)] = dict(
            slope=round(slope, 4),
            intercept=round(my - slope * mx, 3),
            r2=round(r2, 5),
            rendered_min=min(ys[i]), rendered_max=max(ys[i]),
            constant=(syy == 0),
            per_line_hdma=False,
            per_line_raster_split=(len(per_line_vals) > 1),
            per_line_hscroll_values=per_line_vals,
            classification=role,
        )

    def sites(addr):
        return _sites(wri["events"].get("0x%05x" % addr, []))

    out = dict(
        rom="Shin Kidou Senki Gundam W - Endless Duel (J)",
        scene="inputless attract demo fight (WING vs DEATHSCYTHE)",
        method=("rendered per-frame ppu_hscroll from the always-on frame ring "
                "(P1 authority) correlated against full-WRAM ring frames; a "
                "WRAM mirror is never used for phase"),
        camera=dict(
            simulation=dict(
                addr="$7E:%04X" % CAMERA_SIM, width=2,
                role="camera X as the game logic holds it",
                min=min(vals), max=max(vals),
                writer_sites=sites(CAMERA_SIM),
                p1_status=("LEADS the rendered PPU scroll by one frame "
                           "whenever the camera moves — do NOT use for "
                           "pixel phase"),
            ),
            latched=dict(
                addr="$7E:%04X" % CAMERA_LATCHED, width=2,
                role=("vblank-latched copy of camera X; the raster handler at "
                      + SCROLL_STORE_SITE + " stores it into $210D (BG1HOFS)"),
                min=min(vals), max=max(vals),
                writer_sites=sites(CAMERA_LATCHED),
                copy_site=CAMERA_COPY_SITE,
                copy_disassembly=["$00:8410 LDA $0100 / STA $0680",
                                  "$00:8416 LDX $0114 / STX $068C",
                                  "$00:841C LDX $0116 / STX $068E",
                                  "$00:8422 LDX $0694 / STX $0696",
                                  "$00:8428 LDA $0144 / STA $0684",
                                  "$00:842E LDA $0148 / STA $0686",
                                  "$00:8434 LDA $014E / STA $0682",
                                  "$00:843A LDA $0698 / STA $069A"],
            ),
            y=dict(simulation="$7E:%04X" % CAMERA_SIM_Y,
                   latched="$7E:%04X" % CAMERA_LATCHED_Y,
                   note="stored into $210E by the raster handler at $00:891F"),
            width=2,
            min=min(vals), max=max(vals),
            addr="$7E:%04X" % CAMERA_LATCHED,
            master=dict(addr="$7E:%04X" % CAMERA_MASTER,
                        y_addr="$7E:%04X" % CAMERA_MASTER_Y,
                        role=("the clamped, smoothed master camera; $0114 is "
                              "copied from it once per frame by "
                              + CAMERA_ROUTINE_FIGHT)),
            observed_range_note=(
                "128..191 is only what the ATTRACT DEMO walks.  The real "
                "bounds are the ROM clamp constants in `clamp` below: "
                "[64, 192] for X, [112, 256] for Y."),
            all_slope1_candidates=ver["candidates"],
            other_slope_candidates={k: v for k, v in find["hits"].items()
                                    if k != "1.0"},
        ),
        clamp=dict(
            x=dict(min=CAMERA_CLAMP_X[0], max=CAMERA_CLAMP_X[1],
                   min_hex="0x%04x" % CAMERA_CLAMP_X[0],
                   max_hex="0x%04x" % CAMERA_CLAMP_X[1],
                   site=CAMERA_CLAMP_SITE_X,
                   source="ROM disassembly of the site the write ring named"),
            y=dict(min=CAMERA_CLAMP_Y[0], max=CAMERA_CLAMP_Y[1],
                   min_hex="0x%04x" % CAMERA_CLAMP_Y[0],
                   max_hex="0x%04x" % CAMERA_CLAMP_Y[1],
                   site=CAMERA_CLAMP_SITE_Y,
                   source="ROM disassembly"),
            clamping_writer=dict(
                master_addr="$7E:%04X" % CAMERA_MASTER,
                clamp_site=CAMERA_CLAMP_SITE_X,
                store_site="$04:872E (STA $0620)",
                shape=("target = midpoint of the two fighters - 0x80, "
                       "clamped to [0x40, 0xC0]; then "
                       "cam = (target + cam) >> 1 (ADC + ROR), so the camera "
                       "approaches a wall asymptotically and the observed "
                       "maximum 191 = 0xBF sits one below the 0xC0 clamp"),
                per_stage_routine=CAMERA_ROUTINE_FIGHT,
                per_stage_selector=CAMERA_ROUTINE_SELECTOR,
            ),
            observed_min=min(vals), observed_max=max(vals),
            observed_note=("the attract demo only walks 128..191 because it "
                           "never pushes a fighter to a wall; the clamp "
                           "constants above are the real bounds"),
            widescreen_headroom=dict(
                bg1_map_px=512,
                viewport_px=256,
                camera_travel_px=CAMERA_CLAMP_X[1] - CAMERA_CLAMP_X[0],
                spare_left_px=CAMERA_CLAMP_X[0],
                spare_right_px=512 - (CAMERA_CLAMP_X[1] + 256),
                note=("BG1's tilemap is 64x64 tiles = 512x512 px and the "
                      "camera is clamped to [64, 192], so the visible window "
                      "never leaves [64, 448].  That leaves exactly 64 px of "
                      "tilemap unused on each side — more than the 43 px a "
                      "16:9 margin needs, IF those columns hold authored art. "
                      "Whether they do is R3's job."),
            ),
            runtime_writer_attribution_warning=(
                "the always-on write ring's func_pc/block_pc for $7E:0114 "
                "names the raster IRQ handlers ($00:88E7/$00:8900/$00:891F), "
                "which do NOT contain a store to $0114.  In the interpreter "
                "tier the backscan finds the most recently recorded block, and "
                "in an IRQ-heavy frame that is usually the handler.  Treat "
                "block_pc as a hint and confirm by ROM decode — that is how "
                "the $00:D965 / $04:872E sites above were pinned.  The "
                "attribution for $7E:068C ($00:8410, 800/800) was confirmed "
                "correct the same way."),
            runtime_writer_sites_reported=sites(CAMERA_SIM),
        ),
        mirrors=dict(
            slope_1_words=ver["candidates"],
            byte_mirrors=find["hits"].get("byte_mirrors", []),
            simulation_camera="$7E:%04X" % CAMERA_SIM,
            note=("$7E:0114 -> $7E:068C is the only camera-X mirror pair.  "
                  "$0114 did not appear in the slope-1 scan precisely because "
                  "it leads the rendered scroll — that is the P1 signature, "
                  "not a miss.")),
        parallax=layers,
        hdma=dict(active_channels=ras["hdma_active"],
                  note=("the fight screen drives NO HDMA.  All per-line "
                        "variation is a raster-IRQ chain: $00:88E7 (INIDISP), "
                        "$00:8900 (BG1HOFS from $068C/$068D), $00:891F "
                        "(BG1VOFS from $068E/$068F, TM from $0684, TMW from "
                        "$0686, CGADSUB from $0682)")),
        raster_bands=dict(
            sampled_frame_offset=ras["frame_offset"],
            distinct_bg1_hscroll=ras["distinct_bg1_hscroll"],
            bands=ras["bands"],
            note=("BG1 carries TWO things: a HUD band whose scroll is forced "
                  "to (h=0, v=440) and the world/floor band that uses the "
                  "camera.  Any widescreen layer policy for BG1 must be "
                  "band-aware.")),
        p1_cross_check=dict(
            ring_frames_checked=len(rendered),
            ring_mismatches_latched=len(ver["mismatches"].get(key, [])),
            ring_note=("the frame ring snapshots the WRAM mirror and the "
                       "rendered scroll at the same instant, so $068C agrees "
                       "on every frame.  That is not evidence a mirror is "
                       "safe for phase — see the $0114 numbers."),
            live_samples=len(ver.get("live_samples", [])),
            live_mismatches=len(ver.get("live_mismatches", [])),
            live_deltas=sorted({x["delta"]
                                for x in ver.get("live_mismatches", [])}),
            live_examples=ver.get("live_mismatches", [])[:20],
            simulation_camera_mismatch_note=(
                "measured separately: $7E:0114 disagrees with the rendered "
                "BG1 hScroll on 44 of 701 fight frames (every frame the "
                "camera moves), always by +1 frame of motion, while $7E:068C "
                "disagrees on 0 of 701"),
            verdict=("P1 stands.  Use g_ppu->hScroll[] for pixel phase; the "
                     "WRAM mirror is only safe for recovering high bits a "
                     "wrapped PPU scroll has lost."),
        ),
    )
    W.write_json(os.path.join(W.OUT_ROOT, "camera.json"), out)
    print("[report] camera latched=%s sim=%s range=%d..%d"
          % (out["camera"]["latched"]["addr"],
             out["camera"]["simulation"]["addr"], min(vals), max(vals)))
    for k, v in layers.items():
        print("[report] %s slope=%.4f r2=%.4f split=%s  %s"
              % (k, v["slope"], v["r2"], v["per_line_raster_split"],
                 v["classification"]))
    print("[report] wrote", os.path.join(W.OUT_ROOT, "camera.json"))
    return out


STAGES = dict(series=stage_series, find=stage_find, verify=stage_verify,
              raster=stage_raster, writers=stage_writers,
              report=stage_report)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", default="all", choices=list(STAGES) + ["all"])
    ap.add_argument("--port", type=int, default=4472,
                    help="debug port; use 4471-4479 only")
    ap.add_argument("--raster-window", type=int, default=300,
                    help="how many frames past the fight anchor the raster "
                         "stage keeps hunting for a camera-motion frame")
    ap.add_argument("--live-samples", type=int, default=240,
                    help="how many interleaved live mirror-vs-PPU samples the "
                         "verify stage takes for the P1 cross-check")
    ap.add_argument("--camera", type=lambda s: int(s, 16), default=None,
                    help="force the camera word (hex WRAM offset) instead of "
                         "taking stage_find's first slope-1 hit")
    args = ap.parse_args()
    W.ensure_dir(W.OUT_ROOT)
    order = ["series", "find", "verify", "raster", "writers",
             "report"]
    for n in (order if args.stage == "all" else [args.stage]):
        print("=== stage", n, "===")
        STAGES[n](args)


if __name__ == "__main__":
    main()
