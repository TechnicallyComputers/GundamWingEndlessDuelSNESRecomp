r"""R1 — find GWED's "live fight" WRAM discriminator (P5) and an independent
liveness signal (P6).

Run from **PowerShell**, not Git-Bash (see the note in ws_recon.py).

Stages (each caches its output, so a later stage can be re-run alone):

  sweep    one process, free-run a whole inputless attract cycle
           (boot -> intro crawl -> cinematic -> title/mode menu -> attract
           fight -> back to crawl).  Screenshots every `--shot-step` frames
           are the audit trail for the scene labels; the WRAM used for the
           diff is pulled **retroactively out of the 6000-frame ring**, never
           captured by arming anything.
  states   one fresh process per banked savestate scene (title logo, victory
           quote, KO/"1P WIN", black transition, final convo, ending) ->
           full WRAM per scene.
  diff     intersect: addresses whose value is identical across every live-
           fight sample and different in every non-fight sample.
  narrow   score the bank-$7E survivors by how they behave over a whole
           attract cycle (one value through the fight, a different one both
           before and after it) using change-compressed wram_timeseries.
  confirm  exact transition frames for the shortlist, plus a change count
           inside the live-fight window vs inside the victory-quote window.
  liveness find a signal that changes on EVERY frame of a live fight and on
           NO frame of the menus, crawl, KO screen or victory quote — the
           observable consequence P6 requires.
  writers  one process with `set_wram_watch` armed *before the game boots*,
           free-running the same attract cycle, then `wram_watch_log_get`.
           Treat its func_pc/block_pc as a HINT: in the interpreter tier the
           backscan often names a raster IRQ handler that contains no such
           store.  Every writer in gate.json was confirmed by ROM decode.
  verdict  classify each shortlist address mode|liveness|reject and write
           analysis/widescreen/recon/gate.json.

  all      the stages above, in order.

Usage:
  py -3 scripts\widescreen\recon_gate.py --stage all --port 4471
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ws_recon as W  # noqa: E402

CACHE = os.path.join(W.OUT_ROOT, "gate_cache")

# The attract cycle is anchored on the frame the fight's PPU configuration
# appears (BG1SC == 0x6b is unique to the fight screen across the whole
# cycle — verified in the sweep and recorded in gate.json).  Offsets are
# relative to that anchor so run-to-run attract drift cannot mislabel a
# sample.  Never seconds.
FIGHT_MARKER_BG1SC = 0x6b

ATTRACT_OFFSETS = [
    # (label, frame offset from the fight anchor, is-live-fight?)
    ("attract_crawl",     -2600, False),
    ("attract_crawl_b",   -2000, False),
    ("attract_cinematic", -1300, False),
    ("attract_logo",       -950, False),
    ("title_menu",         -600, False),
    ("title_menu_b",       -300, False),
    ("attract_fight",      +150, True),
    ("attract_fight_b",    +300, True),
    ("attract_fight_c",    +450, True),
    ("attract_fight_d",    +600, True),
    ("attract_fight_e",    +800, True),
    # Post-fight samples matter as much as pre-fight ones: without them any
    # address the fight merely *initialises* (0 before, non-zero after, never
    # cleared) passes the diff trivially.  The attract cycle restarts into the
    # crawl ~90 frames after the fight ends.
    ("post_fight_crawl",  +1150, False),
    ("post_fight_crawl_b", +1500, False),
    ("post_fight_crawl_c", +2000, False),
]

# Savestate scenes.  NONE of them is a live fight: `pre_quote` was banked with
# the PAR freeze already applied, so it drops straight into the victory quote,
# and `pre_stage_ending_dialogue` is a frozen "1P WIN" round-end.  The only
# live fight reachable without an owner-recorded state is the attract demo.
STATE_SCENES = ["title_logo", "victory_quote", "ko_1p_win",
                "black_transition", "final_convo", "ending"]
LIVE_STATE_SCENES = set()

# Liveness candidates named in the playbook: round timer and the two health
# words.  These are the corroborating signals P6 requires.
TIMER_ADDR = 0x060C
P1_HEALTH = 0x1B70
P2_HEALTH = 0x1B74
P1_ENERGY = 0x1B80
P2_ENERGY = 0x1B84


# --------------------------------------------------------------------------

def stage_sweep(args) -> dict:
    W.ensure_dir(CACHE)
    shots = W.ensure_dir(os.path.join(CACHE, "sweep_shots"))
    marks = []
    with W.Instance(args.port) as inst:
        c = inst.c
        f = 0
        while f < args.frames:
            f = W.wait_frame(c, f + args.shot_step, timeout=900)
            ppu = c.j("get_ppu_state")
            shot = os.path.join(shots, "f%05d.bmp" % f)
            c.cmd("screenshot " + W.fwd(shot))
            marks.append(dict(f=f, bg1sc=ppu["bgXsc"][0], bgXsc=ppu["bgXsc"],
                              bgmode=ppu["bgmode"], shot=shot))
        hist = W.history(c)

        fight_frames = [m["f"] for m in marks
                        if int(m["bg1sc"], 16) == FIGHT_MARKER_BG1SC]
        if not fight_frames:
            raise RuntimeError("no fight-configured frames in the sweep; "
                               "raise --frames")
        anchor = min(fight_frames)
        print("[sweep] fight PPU config first seen at frame", anchor,
              "last at", max(fight_frames))

        samples = []
        for label, off, live in ATTRACT_OFFSETS:
            fr = anchor + off
            if fr < hist["oldest"] or fr > hist["newest"]:
                print("[sweep] SKIP %s (frame %d outside ring %d..%d)"
                      % (label, fr, hist["oldest"], hist["newest"]))
                continue
            blob = W.dump_frame_wram(c, fr)
            path = os.path.join(CACHE, "wram_%s.bin" % label)
            with open(path, "wb") as fh:
                fh.write(blob)
            near = min(marks, key=lambda m: abs(m["f"] - fr))
            samples.append(dict(label=label, frame=fr, live=live, wram=path,
                                evidence_shot=near["shot"],
                                evidence_shot_frame=near["f"]))
            print("[sweep] %-18s f=%5d live=%s" % (label, fr, live))

    out = dict(anchor=anchor, history=hist, marks=marks, samples=samples,
               fight_first=anchor, fight_last=max(fight_frames))
    W.write_json(os.path.join(CACHE, "sweep.json"), out)
    return out


def stage_states(args) -> dict:
    W.ensure_dir(CACHE)
    samples = []
    for name in STATE_SCENES:
        scene = W.SCENES_BY_NAME[name]
        with W.Instance(args.port) as inst:
            c = inst.c
            info = W.reach_scene(c, scene)
            ppu = c.j("get_ppu_state")
            blob = W.dump_ram(c, 0, 0x20000)
            path = os.path.join(CACHE, "wram_state_%s.bin" % name)
            with open(path, "wb") as fh:
                fh.write(blob)
            shot = os.path.join(W.ensure_dir(os.path.join(CACHE, "state_shots")),
                                "%s.bmp" % name)
            c.cmd("screenshot " + W.fwd(shot))
            # timer/health readings, for the liveness verdict
            ts = W.timeseries(c, TIMER_ADDR, 1,
                              frm=info["frame"] - 300, to=info["frame"])
            samples.append(dict(label="state_" + name, frame=info["frame"],
                                live=(name in LIVE_STATE_SCENES),
                                wram=path, evidence_shot=shot,
                                bg1sc=ppu["bgXsc"][0], bgXsc=ppu["bgXsc"],
                                timer_series=ts))
            print("[states] %-18s f=%5d bg1sc=%s timer_changes=%d"
                  % (name, info["frame"], ppu["bgXsc"][0], len(ts)))
    out = dict(samples=samples)
    W.write_json(os.path.join(CACHE, "states.json"), out)
    return out


def _load(path):
    with open(path, "rb") as fh:
        return fh.read()


def stage_diff(args) -> dict:
    sweep = json.load(open(os.path.join(CACHE, "sweep.json")))
    states = json.load(open(os.path.join(CACHE, "states.json")))
    samples = sweep["samples"] + states["samples"]
    blobs = {s["label"]: _load(s["wram"]) for s in samples}
    live = [s["label"] for s in samples if s["live"]]
    dead = [s["label"] for s in samples if not s["live"]]
    print("[diff] live=%r" % live)
    print("[diff] non-live=%r" % dead)

    # Addresses constant across every live sample ...
    cand = []
    first = blobs[live[0]]
    for addr in range(0x20000):
        v = first[addr]
        if any(blobs[l][addr] != v for l in live[1:]):
            continue
        if any(blobs[d][addr] == v for d in dead):
            continue
        cand.append((addr, v))
    print("[diff] %d byte addresses unique to live fight" % len(cand))

    rows = []
    for addr, v in cand:
        rows.append(dict(
            addr="0x%05x" % addr,
            addr_int=addr,
            live_value="0x%02x" % v,
            values={s["label"]: "0x%02x" % blobs[s["label"]][addr]
                    for s in samples},
        ))
    out = dict(live_scenes=live, non_live_scenes=dead, candidates=rows)
    W.write_json(os.path.join(CACHE, "diff.json"), out)
    return out


def stage_narrow(args) -> dict:
    """Score every bank-$7E candidate by its behaviour over a whole attract
    cycle: a mode byte holds ONE value for the whole fight, holds other values
    on the other screens, and changes only at scene boundaries.  A scratch or
    graphics-buffer byte churns every frame.

    Bank $7F ($10000-$1FFFF) candidates are dropped wholesale: on this game
    that half of WRAM is decompression / tile-staging scratch (it accounts for
    6337 of the 7028 raw candidates and its bytes churn every frame), so no
    stable mode discriminator can live there.
    """
    diff = json.load(open(os.path.join(CACHE, "diff.json")))
    addrs = [r["addr_int"] for r in diff["candidates"] if r["addr_int"] < 0x10000]
    print("[narrow] %d bank-$7E candidates (of %d total)"
          % (len(addrs), len(diff["candidates"])))
    with W.Instance(args.port) as inst:
        c = inst.c
        anchor = W.attract_fight_anchor(c)
        end = anchor + 2100
        W.wait_frame(c, end, timeout=1200)
        # Fight window = [anchor, fight_end); find fight_end by the same PPU
        # marker recorded during the sweep.
        rows = {}
        for a in addrs:
            ts = W.timeseries(c, a, 1, frm=0, to=end, limit=4096)
            vals = [e["hex"] for e in ts]
            fight_vals = [e["hex"] for e in ts
                          if anchor + 100 <= e["f"] <= anchor + 800]
            # value in force at the anchor
            at_anchor = None
            for e in ts:
                if e["f"] <= anchor:
                    at_anchor = e["hex"]
            def value_at(fr):
                v = None
                for e in ts:
                    if e["f"] <= fr:
                        v = e["hex"]
                return v
            fight_v = value_at(anchor + 400)
            pre_v = value_at(anchor - 300)
            post_v = value_at(anchor + 1500)
            post2_v = value_at(anchor + 2000)
            rows["0x%05x" % a] = dict(
                addr_int=a,
                changes=len(ts),
                distinct=len(set(vals)),
                fight_changes=len(fight_vals),
                fight_stable=(len(set(fight_vals)) <= 1),
                value_at_anchor=at_anchor,
                value_pre_fight=pre_v,
                value_in_fight=fight_v,
                value_post_fight=post_v,
                value_post_fight_2=post2_v,
                round_trips=(fight_v is not None and pre_v != fight_v
                             and post_v != fight_v and post2_v != fight_v),
                series=ts if len(ts) <= 40 else ts[:40],
                series_truncated=len(ts) > 40,
            )
    out = dict(anchor=anchor, end=end, rows=rows)
    W.write_json(os.path.join(CACHE, "narrow.json"), out)
    stable = [k for k, v in rows.items()
              if v["fight_stable"] and v["round_trips"] and v["changes"] <= 40]
    print("[narrow] %d addresses hold ONE value through the fight and a "
          "different value both before and after it" % len(stable))
    for k in sorted(stable):
        v = rows[k]
        print("   %s changes=%2d distinct=%d  pre=%s fight=%s post=%s post2=%s"
              % (k, v["changes"], v["distinct"], v["value_pre_fight"],
                 v["value_in_fight"], v["value_post_fight"],
                 v["value_post_fight_2"]))
    return out


SHORTLIST = [
    # (addr, width, role) — the survivors of `narrow`, plus the playbook's
    # known liveness words.  $7E:1000 is a stride-4 array of 16-bit game-state
    # words (the odd bytes are always 0), so those are read as words.
    (0x1000, 2, "mode word (coarse): 0x0010 for the whole battle family"),
    (0x1004, 2, "sub-mode word: 0x0012 == live fight"),
    (0x1008, 2, "third state word"),
    (0x100C, 2, "fourth state word"),
    (0x1010, 2, "state word"),
    (0x1014, 2, "state word"),
    (0x0038, 1, "dispatch-vector byte (bank $83/$84 handler pointer area)"),
    (0x003A, 1, "dispatch-vector byte"),
    (0x1E2A, 1, "P1 object flags — bit 3 (0x08) seen only in a live fight"),
    (0x1E2E, 1, "P2 object flags — bit 3 (0x08) seen only in a live fight"),
    (0x0600, 1, "fight frame counter — increments every frame while a "
                "round is live; frozen on every scripted screen.  THE "
                "liveness signal (P6)."),
    (0x060C, 1, "round timer (BCD).  REJECTED as liveness: the attract "
                "demo runs with the clock disabled (HUD shows the "
                "infinity glyph) so it changes twice in a whole cycle."),
    (0x0030, 4, "32-bit RNG state — churns every frame in a live fight, "
                "but it is not fight-specific enough to gate on."),
    (0x1B70, 2, "P1 health"),
    (0x1B74, 2, "P2 health"),
    (0x1B80, 2, "P1 energy counter"),
    (0x1B84, 2, "P2 energy counter"),
]

GATE_MODE_ADDR = 0x1004
GATE_MODE_VALUE = 0x0012
GATE_COARSE_ADDR = 0x1000
GATE_COARSE_VALUE = 0x0010
GATE_LIVENESS_ADDR = 0x0600

# Runtime write attribution in the interpreter tier is a HINT, not an answer:
# the trace ring's func_pc/block_pc backscan returns the most recently
# recorded block, and in an IRQ-heavy frame that is usually a raster handler
# that contains no store to the watched address at all (proved for $7E:0114,
# where the ring blamed $00:88E7/$00:8900/$00:891F and the ROM has no store to
# $0114 in any of them).  Every writer below was therefore CONFIRMED by
# decoding the ROM with recompiler/snes65816.py and searching for the actual
# store opcodes.  Ghidra is barred for SNES, so the decoder is the tool.
ROM_CONFIRMED_WRITERS = {
    "0x1000": dict(
        stores=["$01:B55E STA $1000 (A=0x0002)",
                "$01:B60F STA $1000 (A=0x000A, the title screen)",
                "$01:847A / $01:86FA / $01:872B / $01:88BF  STZ $1000"],
        note=("the whole game-state machine lives in bank $01 around "
              "$B550-$B630.  Each screen's entry code writes the same value "
              "to a PENDING word at $7E:1500+n and to the LIVE word at "
              "$7E:1000+n, e.g. $01:B60C-$01:B621 writes 0x0A/0x0C/0x0E into "
              "$1500/$1504/$1508 and $1000/$1004/$1008 — which is exactly the "
              "title-menu triple this recon measured."),
        parallel_array="$7E:1500 (stride 4) holds the pending/desired copy"),
    "0x1004": dict(
        stores=["$01:B579 STA $1004 (A=0x0004)",
                "$01:B618 STA $1004 (A=0x000C, the title screen)",
                "$01:847D / $01:86FD / $01:872E / $01:88C2  STZ $1004",
                "$15:9443 STY $1004"],
        note="same state machine as $1000; $1004 is its second word"),
    "0x0600": dict(
        stores=["$04:8377 INC $0600  (the per-frame increment)",
                "$00:B411 STZ $0600  (reset at round start)"],
        note=("a single INC per frame inside the fight's per-frame update, "
              "which is why it is a clean liveness signal: no fight update, "
              "no increment")),
}


def stage_confirm(args) -> dict:
    """Transition frames + liveness measurements for the shortlist.

    Two measurements per address: the change-compressed series over one whole
    attract cycle (so the transition frames are exact), and the number of
    changes inside the live-fight window vs inside the victory-quote window.
    That difference is what P6 calls "an observable consequence".
    """
    out = dict(attract={}, quote={})
    with W.Instance(args.port) as inst:
        c = inst.c
        anchor = W.attract_fight_anchor(c)
        end = anchor + 1400
        W.wait_frame(c, end, timeout=1200)
        out["anchor"] = anchor
        out["end"] = end
        for addr, width, role in SHORTLIST:
            ts = W.timeseries(c, addr, width, frm=0, to=end, limit=4096)
            in_fight = [e for e in ts
                        if anchor + 100 <= e["f"] <= anchor + 800]
            out["attract"]["0x%04x" % addr] = dict(
                width=width, role=role,
                changes=len(ts),
                changes_in_fight_window=len(in_fight),
                series=[[e["f"] - anchor, e["hex"]] for e in ts],
            )
            print("[confirm/attract] $%04X w=%d changes=%3d in-fight=%3d  %s"
                  % (addr, width, len(ts), len(in_fight), role))

    # The victory quote is the P6 probe: same battle-family mode, no liveness.
    scene = W.SCENES_BY_NAME["victory_quote"]
    with W.Instance(args.port) as inst:
        c = inst.c
        info = W.reach_scene(c, scene)
        f1 = info["frame"]
        for addr, width, role in SHORTLIST:
            ts = W.timeseries(c, addr, width, frm=0, to=f1, limit=4096)
            # start 200 frames after the load has settled, so the
            # load_state discontinuity is not counted as motion
            window = [e for e in ts if e["f"] >= f1 - 200]
            out["quote"]["0x%04x" % addr] = dict(
                width=width, role=role,
                changes=len(ts),
                changes_in_window=len(window),
                series=[[e["f"] - f1, e["hex"]] for e in ts],
            )
            print("[confirm/quote]   $%04X w=%d changes=%3d in-window=%3d"
                  % (addr, width, len(ts), len(window)))
        out["quote_frame"] = f1
    W.write_json(os.path.join(CACHE, "confirm.json"), out)
    return out


def _consecutive_wram(c, first, count):
    return [W.dump_frame_wram(c, first + i) for i in range(count)]


def _churn(frames):
    """Addresses that differ between EVERY consecutive pair of frames."""
    if len(frames) < 2:
        return set()
    sets = []
    for a, b in zip(frames, frames[1:]):
        sets.append({i for i in range(0x20000) if a[i] != b[i]})
    out = set(sets[0])
    for s in sets[1:]:
        out &= s
    return out


def _any_change(frames):
    out = set()
    for a, b in zip(frames, frames[1:]):
        out |= {i for i in range(0x20000) if a[i] != b[i]}
    return out


def stage_liveness(args) -> dict:
    """P6 — find a signal that is an *observable consequence* of the fight
    pipeline running, not a mode byte.

    Method: pull runs of consecutive frames straight out of the ring and keep
    the addresses that change on EVERY frame of a live fight and on NO frame
    of the title menu, the post-fight crawl, the frozen KO screen or the
    victory quote.  The round timer $060C is NOT usable: the attract demo runs
    with the clock disabled (the HUD shows the infinity glyph), so it changes
    twice in a whole attract cycle.
    """
    N = 8
    with W.Instance(args.port) as inst:
        c = inst.c
        anchor = W.attract_fight_anchor(c)
        W.wait_frame(c, anchor + 1300, timeout=1200)
        fight = _consecutive_wram(c, anchor + 400, N)
        menu = _consecutive_wram(c, anchor - 400, N)
        crawl = _consecutive_wram(c, anchor + 1200, N)
    live_set = _churn(fight)
    dead_set = _any_change(menu) | _any_change(crawl)
    cands = live_set - dead_set
    print("[liveness] fight-every-frame=%d  menu/crawl-any=%d  after=%d"
          % (len(live_set), len(dead_set), len(cands)))

    for scene_name in ("victory_quote", "ko_1p_win"):
        scene = W.SCENES_BY_NAME[scene_name]
        with W.Instance(args.port) as inst:
            c = inst.c
            info = W.reach_scene(c, scene)
            W.wait_frame(c, info["frame"] + 20)
            frames = _consecutive_wram(c, info["frame"] + 5, N)
        moved = _any_change(frames)
        cands -= moved
        print("[liveness] minus %s (%d moving) -> %d"
              % (scene_name, len(moved), len(cands)))

    low = sorted(a for a in cands if a < 0x2000)
    out = dict(anchor=anchor, count=len(cands),
               candidates=["0x%05x" % a for a in sorted(cands)][:400],
               low_candidates=["0x%05x" % a for a in low])
    W.write_json(os.path.join(CACHE, "liveness.json"), out)
    print("[liveness] %d candidates below $2000: %s"
          % (len(low), ["0x%04x" % a for a in low[:40]]))
    return out


def stage_writers(args) -> dict:
    """Writer pc24 + enclosing function for every shortlist address.

    The watches are armed on the very first connection, before the game has
    left its boot sequence, and the process then free-runs the whole attract
    cycle: an always-on recorder for the run, not an arm-then-capture window.
    """
    addrs = [a for a, _w, _r in SHORTLIST]
    with W.Instance(args.port) as inst:
        c = inst.c
        for a in addrs:
            for off in range(2):
                c.cmd("set_wram_watch 7e %04x 1" % (a + off))
        anchor = W.attract_fight_anchor(c)
        target = anchor + 1200
        W.wait_frame(c, target, timeout=1200)
        events = {}
        for a in addrs:
            evs = []
            for off in range(2):
                r = c.j("wram_watch_log_get %04x 0 %d 400" % (a + off, target))
                evs.extend(r.get("events", []))
            events["0x%04x" % a] = evs
            print("[writers] $%04X -> %d write events  funcs=%r"
                  % (a, len(evs),
                     sorted({e.get("func", "?") for e in evs})[:4]))
        c.cmd("clear_wram_watches")
    out = dict(anchor=anchor, watched=["0x%04x" % a for a in addrs],
               events=events)
    W.write_json(os.path.join(CACHE, "writers.json"), out)
    return out


def _writer_funcs(events):
    """Attribute writes to code sites.

    GWED promotes nothing (`recomp/symbols.toml` has `emit = false` for
    I_RESET/I_NMI only), so the fight engine runs entirely in the interpreter
    and the CPU trace ring carries no AOT function *names* — `func` is always
    "?".  What IS carried is usable: `func_pc` / `block_pc` (16-bit code
    addresses) plus `PB` (the program bank at the moment of the write).  The
    event's own `pc24` is NOT usable for WRAM writes: the low 16 bits are
    reused to carry the WRAM address, so it degenerates to `PB << 16`.
    """
    seen = {}
    for e in events:
        pb = e.get("PB", "0x00")
        key = (pb, e.get("func_pc", "?"), e.get("block_pc", "?"))
        seen[key] = seen.get(key, 0) + 1
    out = []
    for (pb, func_pc, block_pc), n in sorted(seen.items(),
                                             key=lambda kv: -kv[1]):
        try:
            site = "$%02X:%04X" % (int(pb, 16), int(block_pc, 16) & 0xFFFF)
        except (TypeError, ValueError):
            site = "?"
        out.append(dict(pb=pb, func_pc=func_pc, block_pc=block_pc,
                        site=site, writes=n, func_name=None,
                        func_name_note="interpreter tier: no AOT symbol"))
    return out


def _word(blob, addr):
    return blob[addr] | (blob[addr + 1] << 8)


def stage_verdict(args) -> dict:
    sweep = json.load(open(os.path.join(CACHE, "sweep.json")))
    states = json.load(open(os.path.join(CACHE, "states.json")))
    confirm = json.load(open(os.path.join(CACHE, "confirm.json")))
    writers = json.load(open(os.path.join(CACHE, "writers.json")))

    samples = sweep["samples"] + states["samples"]
    live_labels = {s["label"] for s in samples if s["live"]}
    blobs = {s["label"]: open(s["wram"], "rb").read() for s in samples}

    cands = []
    for addr, width, role in SHORTLIST:
        key = "0x%04x" % addr
        vals = {}
        for s in samples:
            b = blobs[s["label"]]
            vals[s["label"]] = ("0x%04x" % _word(b, addr)) if width == 2 \
                else ("0x%02x" % b[addr])
        live_vals = {v for k, v in vals.items() if k in live_labels}
        dead_vals = {v for k, v in vals.items() if k not in live_labels}
        unique = len(live_vals) == 1 and not (live_vals & dead_vals)

        a_ch = confirm["attract"].get(key, {})
        q_ch = confirm["quote"].get(key, {})
        ticks_fight = a_ch.get("changes_in_fight_window", 0)
        ticks_quote = q_ch.get("changes_in_window", 0)

        if unique:
            verdict = "mode"
            reasons = ["holds the single value %s across every live-fight "
                       "sample and that value appears in none of the %d "
                       "non-fight samples"
                       % (next(iter(live_vals)), len(live_labels ^ set(vals)))]
        elif ticks_fight >= 3 and ticks_quote == 0:
            verdict = "liveness"
            reasons = ["changes %d times inside the live-fight window and 0 "
                       "times inside the victory-quote window" % ticks_fight]
        else:
            verdict = "reject"
            reasons = ["live-fight value(s) %s are shared with a non-fight "
                       "scene" % sorted(live_vals)]
            if ticks_fight and ticks_quote:
                reasons.append("also changes during the victory quote (%d "
                               "times), so it is not a liveness signal either"
                               % ticks_quote)

        cands.append(dict(
            addr="$7E:%04X" % addr, width=width, role=role,
            values_by_scene=vals,
            attract_series=a_ch.get("series", []),
            quote_series=q_ch.get("series", []),
            changes_in_fight_window=ticks_fight,
            changes_in_quote_window=ticks_quote,
            writers_runtime_hint=_writer_funcs(
                writers.get("events", {}).get(key, [])),
            writers_rom_confirmed=ROM_CONFIRMED_WRITERS.get(key),
            verdict=verdict, reasons=reasons,
        ))

    by_addr = {c["addr"]: c for c in cands}
    liveness = by_addr.get("$7E:%04X" % GATE_LIVENESS_ADDR)
    if liveness is None or liveness["verdict"] != "liveness":
        liveness = next((c for c in cands if c["verdict"] == "liveness"), None)
    coarse_key = "$7E:%04X" % GATE_COARSE_ADDR
    fine_key = "$7E:%04X" % GATE_MODE_ADDR
    quote_coarse = by_addr[coarse_key]["values_by_scene"].get(
        "state_victory_quote")
    quote_fine = by_addr[fine_key]["values_by_scene"].get(
        "state_victory_quote")

    # A second, independent P6 demonstration that needs no savestate: inside
    # the attract cycle the fine mode word turns 0x0012 seventy-odd frames
    # BEFORE the frame counter starts running (stage load / round intro), so a
    # mode-only gate would enable fight widescreen behaviour over the round
    # intro too.
    live_series = confirm["attract"].get(
        "0x%04x" % GATE_LIVENESS_ADDR, {}).get("series", [])
    mode_series = confirm["attract"].get(
        "0x%04x" % GATE_MODE_ADDR, {}).get("series", [])
    mode_on = next((o for o, v in mode_series
                    if int(v[2:4] + v[0:2], 16) == GATE_MODE_VALUE), None)
    live_on = None
    for o, _v in live_series:
        if o > -3000:
            live_on = o if live_on is None else min(live_on, o)
    live_on = next((o for o, _v in live_series if o > -100), None)

    p6 = dict(
        round_intro_probe=dict(
            mode_becomes_fight_at_offset=mode_on,
            liveness_starts_at_offset=live_on,
            gap_frames=(live_on - mode_on)
            if (mode_on is not None and live_on is not None) else None,
            conclusion=("inside the attract cycle the fine mode word already "
                        "reads 'live fight' for ~%s frames of stage load / "
                        "round intro before the frame counter starts moving, "
                        "so even the FINE mode word is not liveness on its "
                        "own" % ((live_on - mode_on)
                                 if (mode_on is not None
                                     and live_on is not None) else "?")),
        ),
        scene="state_victory_quote",
        coarse_mode_addr=coarse_key,
        coarse_mode_value_in_fight="0x%04x" % GATE_COARSE_VALUE,
        coarse_mode_value_in_quote=quote_coarse,
        passes_coarse_mode_alone=(quote_coarse == "0x%04x" % GATE_COARSE_VALUE),
        fine_mode_addr=fine_key,
        fine_mode_value_in_fight="0x%04x" % GATE_MODE_VALUE,
        fine_mode_value_in_quote=quote_fine,
        liveness_addr=liveness["addr"] if liveness else None,
        liveness_changes_in_fight=liveness["changes_in_fight_window"]
        if liveness else None,
        liveness_changes_in_quote=liveness["changes_in_quote_window"]
        if liveness else None,
        conclusion=("the victory quote satisfies the coarse mode word "
                    "($7E:1000 == 0x0010, the whole battle family) but fails "
                    "both the fine sub-mode word ($7E:1004 == 0x0012) and the "
                    "liveness signal.  A coarse-mode-only gate would enable "
                    "fight widescreen behaviour on a scripted screen — "
                    "exactly the P6 defect."),
    )

    out = dict(
        rom="Shin Kidou Senki Gundam W - Endless Duel (J)",
        build="build-ws-trace",
        method=("full-WRAM diff over the inputless attract cycle (frames "
                "pulled retroactively from the always-on 6000-frame ring), "
                "intersected with six banked savestate scenes, then "
                "change-compressed wram_timeseries confirmation and "
                "set_wram_watch writer attribution"),
        sweep=dict(anchor=sweep["anchor"], fight_first=sweep["fight_first"],
                   fight_last=sweep["fight_last"],
                   note="attract timing drifts run to run, so every frame "
                        "offset in this file is relative to the fight anchor"),
        fight_ppu_marker=dict(
            reg="BG1SC/$2107", value="0x%02x" % FIGHT_MARKER_BG1SC,
            note="used ONLY to anchor the sweep.  P5 forbids gating on a PPU "
                 "register mirror, and this one is not even sufficient: the "
                 "victory quote and the KO screen share it."),
        gate=dict(
            mode=dict(addr=fine_key, width=2,
                      value="0x%04x" % GATE_MODE_VALUE,
                      coarse_qualifier=dict(addr=coarse_key, width=2,
                                            value="0x%04x"
                                            % GATE_COARSE_VALUE)),
            liveness=dict(addr=liveness["addr"] if liveness else None,
                          rule="increments (mod 256) on every frame of a "
                               "live round and is frozen on every scripted "
                               "screen"),
        ),
        p6_probe=p6,
        limitations=dict(
            scenes_not_reachable=[
                "in-game pause menu", "character select", "VS screen",
                "options / key-config screens", "trial mode",
                "a human-driven 1P or 2P fight (including both walls)"],
            why=("only the owner can play; this recon works from the "
                 "inputless attract cycle plus the banked savestates, and "
                 "`pre_quote` was banked with the PAR freeze already applied "
                 "so it drops into the victory quote instead of resuming a "
                 "fight.  $7E:1004 == 0x0012 is unique to live fight across "
                 "every scene reachable here (14 non-fight samples), but the "
                 "listed screens have NOT been checked and must be, using "
                 "the owner-recorded states named in the plan "
                 "(ws_charselect, ws_vs_screen, ws_menu_options, "
                 "ws_fight_left_wall, ws_fight_right_wall)."),
            attract_only_caveat=("the attract demo runs with the round clock "
                                 "disabled (the HUD shows the infinity "
                                 "glyph), which is why $7E:060C is useless as "
                                 "a liveness signal here and why $7E:0600 was "
                                 "used instead.  $060C should still be "
                                 "re-checked on a human-driven round."),
        ),
        writer_attribution=dict(
            method_runtime=("set_wram_watch armed before the game left its "
                            "boot sequence, then wram_watch_log_get over the "
                            "whole attract cycle"),
            method_rom=("ROM decode with recompiler/snes65816.py, searching "
                        "for the actual store opcodes at the addresses the "
                        "ring pointed at"),
            runtime_hint_is_unreliable=(
                "the ring's pc24 for a WRAM_WRITE event degenerates to "
                "PB<<16 (its low 16 bits carry the WRAM address instead), and "
                "func_pc/block_pc come from a backscan that in the "
                "interpreter tier lands on the most recent raster IRQ handler."
                "  Confirmed wrong for $7E:0114 and confirmed right for "
                "$7E:068C, so it must always be cross-checked."),
            rom_confirmed=ROM_CONFIRMED_WRITERS,
        ),
        scenes=[dict(label=s["label"], frame=s["frame"], live=s["live"],
                     evidence_shot=s.get("evidence_shot")) for s in samples],
        candidates=cands,
    )
    W.write_json(os.path.join(W.OUT_ROOT, "gate.json"), out)
    print("[verdict] mode =", out["gate"]["mode"])
    print("[verdict] live =", out["gate"]["liveness"])
    print("[verdict] wrote", os.path.join(W.OUT_ROOT, "gate.json"))
    return out


STAGES = dict(sweep=stage_sweep, states=stage_states, diff=stage_diff,
              narrow=stage_narrow, confirm=stage_confirm,
              liveness=stage_liveness,
              writers=stage_writers, verdict=stage_verdict)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", default="all", choices=list(STAGES) + ["all"],
                    help="which stage to run (default all)")
    ap.add_argument("--port", type=int, default=4471,
                    help="debug port; use 4471-4479 only")
    ap.add_argument("--frames", type=int, default=5500,
                    help="attract sweep length in frames (~60 fps headless)")
    ap.add_argument("--shot-step", type=int, default=50,
                    help="screenshot/PPU-sample cadence in frames")
    args = ap.parse_args()
    W.ensure_dir(W.OUT_ROOT)
    order = ["sweep", "states", "diff", "narrow", "confirm",
             "liveness", "writers", "verdict"]
    names = order if args.stage == "all" else [args.stage]
    for n in names:
        print("=== stage", n, "===")
        STAGES[n](args)


if __name__ == "__main__":
    main()
