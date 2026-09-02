"""Shared recon library for the GWED 16:9 widescreen work (Beads beads-8wg.9.13.2).

Everything here is *observation only*: it launches the trace build, free-runs it,
and reads the always-on rings (frame history / VRAM write ring / OAM rings /
per-line PPU journal).  Nothing arms a trace and nothing pauses the guest — see
`docs/WIDESCREEN_PATTERNS.md` and the standing "ring buffers, never arm-then-run"
rule.

Deliberate constraints baked in here:

* `tools/validation_states/tcp.py` is *reused, never edited*.  Its module-level
  ``EXE``/``ROM`` constants point at the main checkout's ``build-agent``; we
  rebind them after import so the same client drives this worktree's
  ``build-ws-trace``.
* Savestates are gitignored, so they only exist in the primary checkout.
  ``STATES_DIR`` points there.
* ``build-ws-trace/mods/preloaded/state.toml`` is shared by every process
  launched from that directory, so callers must run **one instance at a time**
  (or stage a private copy of the build dir).
* Screenshot paths must be absolute with forward slashes; ``dump_ram`` /
  ``dump_vram`` / ``wram_timeseries`` take a **hex address** and a **decimal
  length**.
* Never classify a scene from framebuffer pixels: gate on WRAM.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import time

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

WT_ROOT = r"F:\Projects\snesrecomp\_wt-gwed-widescreen"
MAIN_ROOT = r"F:\Projects\snesrecomp\GundamWingEndlessDuelSNESRecomp"

BUILD = os.path.join(WT_ROOT, "build-ws-trace")
EXE = os.path.join(BUILD, "GundamWingEndlessDuelSNESRecomp.exe")
ROM = os.path.join(
    MAIN_ROOT, "Shin Kidou Senki Gundam W - Endless Duel (J).smc"
)
# Savestates are gitignored -> they live only in the primary checkout.
STATES_DIR = os.path.join(MAIN_ROOT, "tools", "validation_states")

OUT_ROOT = os.path.join(WT_ROOT, "analysis", "widescreen", "recon")

# tcp.py lives in this worktree; import it without editing it.
sys.path.insert(0, os.path.join(WT_ROOT, "tools", "validation_states"))
import tcp  # noqa: E402  (path juggling is required)

tcp.BUILD = BUILD
tcp.EXE = EXE
tcp.ROM = ROM


# NOTE — RUN THESE SCRIPTS FROM POWERSHELL, NOT FROM A GIT-BASH SHELL.
# Under the agent harness's Bash tool the game exe refuses to load at all:
# CreateProcess succeeds and the child dies immediately with 0xC0000079 /
# 0xC0000135 before printing anything, with a full or a minimal environment
# block alike.  The same subprocess.Popen call from PowerShell starts the game
# and opens the debug port.  Do not "fix" this by scrubbing PATH — trimming
# MSYS entries only changes which loader status you get.

Conn = tcp.Conn

PORTS = list(range(4471, 4480))  # this agent's assigned block


def fwd(path: str) -> str:
    """Absolute forward-slash path — what `screenshot` requires."""
    return os.path.abspath(path).replace("\\", "/")


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def out_dir(*parts: str) -> str:
    return ensure_dir(os.path.join(OUT_ROOT, *parts))


# --------------------------------------------------------------------------
# PAR freeze set (see tools/validation_states/README.md).  Re-applied every
# poll tick, exactly like a Pro Action Replay code, to force a round to end.
# --------------------------------------------------------------------------

PAR_FREEZE = [
    "write_ram 1b70 ff70",   # P1 health
    "write_ram 1b80 2c01",   # P1 energy = 300
    "write_ram 1b74 0100",   # P2 health = 1  -> round ends
    "write_ram 1b84 0100",   # P2 energy = 1
    "write_ram 060c 99",     # round timer
]


def par_freeze(c) -> None:
    for line in PAR_FREEZE:
        c.cmd(line)


# --------------------------------------------------------------------------
# Scene table.  `kind` drives how a scene is reached:
#   boot   -> fresh process, free-run to `frames`
#   state  -> fresh process, load_state, free-run `settle` frames
#   par    -> fresh process, load_state, then PAR-freeze until the round ends
# Frame numbers, never seconds: attract timing drifts run to run.
# --------------------------------------------------------------------------

# The game-state words established by recon_gate.py (analysis/widescreen/
# recon/gate.json).  $7E:1000 is a stride-4 array of 16-bit state words; the
# first two are a coarse mode and a fine sub-mode:
#   ($1000, $1004)
#     (0x0002, 0x000A)  intro story crawl over the character portraits
#     (0x0008, 0x0000)  mecha cinematic
#     (0x000A, 0x000C)  title screen with the mode selector
#     (0x0010, 0x0012)  LIVE FIGHT
#     (0x0010, 0x0014)  post-round victory / defeat quote
#     (0x0010, 0x001E)  round end, inter-stage dialogue, ending
MODE_ADDR = 0x1000
SUBMODE_ADDR = 0x1004
MODE_FIGHT = (0x0010, 0x0012)
LIVENESS_ADDR = 0x0600


def read_mode(c):
    """(mode, sub_mode) as 16-bit words — the WRAM scene gate."""
    b = unhex(c.j("read_ram %04x 8" % MODE_ADDR)["hex"])
    return (b[0] | (b[1] << 8), b[4] | (b[5] << 8))


def wait_for_mode(c, mode, sub=None, limit=12000, poll=0.03):
    """Free-run until the WRAM scene gate reads `mode` (and `sub`).

    This is how every attract scene is reached: gate on WRAM, never on a
    frame offset (attract timing drifts run to run) and never on pixels.
    """
    while frame(c) < limit:
        m, sm = read_mode(c)
        if m == mode and (sub is None or sm == sub):
            return frame(c)
        time.sleep(poll)
    raise RuntimeError("mode (0x%04x, %r) never reached within %d frames"
                       % (mode, sub, limit))


SCENES = [
    # --- reachable inside the inputless attract cycle (one process each) ---
    dict(name="attract_fight", kind="wram", mode=0x0010, sub=0x0012,
         settle=180,
         note="THE live fight: the inputless attract demo (WING vs "
              "DEATHSCYTHE).  The only live gameplay reachable without an "
              "owner-recorded savestate."),
    dict(name="attract_crawl", kind="wram", mode=0x0002, sub=0x000A,
         settle=120,
         note="intro story crawl over character portraits"),
    dict(name="attract_cinematic", kind="wram", mode=0x0008, sub=0x0000,
         settle=120,
         note="mecha cinematic / explosion"),
    dict(name="title_menu", kind="wram", mode=0x000A, sub=0x000C, settle=120,
         note="title screen with the STORY/VS/TRIAL/OPTION selector"),

    # --- banked savestates (one fresh process each) ---
    dict(name="title_logo", kind="state", state="pre_title", settle=300,
         note="title logo before the mode selector fades in"),
    dict(name="victory_quote", kind="state", state="pre_quote", settle=400,
         note="post-round victory/defeat quote.  pre_quote was banked with "
              "the PAR freeze already applied (P2 health = 1), so the round "
              "ends on its own within ~100 frames of the load and the fight "
              "cannot be resumed from it.  This is the P6 probe scene: the "
              "fight HUD is still up and the coarse mode word still reads "
              "'battle family', but nothing is live."),
    dict(name="ko_1p_win", kind="state", state="pre_stage_ending_dialogue",
         settle=400,
         note="round-end / KO screen ('1P WIN') — frozen, never advances"),
    dict(name="black_transition", kind="state",
         state="pre_stage_battle_dialogue_3", settle=400,
         note="black inter-stage transition (stays black)"),
    dict(name="final_convo", kind="state", state="pre_final_convo", settle=400,
         note="post-final Treize conversation, dialogue box over portrait art"),
    dict(name="ending", kind="state", state="pre_ending", settle=400,
         note="per-pilot epilogue art"),
]

SCENES_BY_NAME = {s["name"]: s for s in SCENES}


def state_path(name: str) -> str:
    return os.path.join(STATES_DIR, name + ".state")


# --------------------------------------------------------------------------
# Process / connection helpers
# --------------------------------------------------------------------------

class Instance:
    """One trace-build process plus its debug connection.

    Use as a context manager; `clear_controller` and process kill happen in
    the finally path unconditionally.
    """

    def __init__(self, port: int, lang: str = "en", visible: bool = False,
                 build: str | None = None):
        self.port = port
        self.lang = lang
        self.visible = visible
        self.build = build or BUILD
        self.proc = None
        self.c = None

    def __enter__(self):
        self.proc = tcp.launch(self.port, self.lang, build=self.build,
                               visible=self.visible)
        # The trace exe is ~128 MB; a cold start on a busy disk can exceed
        # tcp.Conn's own 25 s budget, so retry a couple of times before
        # declaring the launch dead.
        last = None
        for _ in range(4):
            if self.proc.poll() is not None:
                raise RuntimeError("process exited rc=%r before listening"
                                   % self.proc.returncode)
            try:
                self.c = Conn(self.port)
                return self
            except Exception as e:  # noqa: BLE001
                last = e
        raise RuntimeError("no debug connection on port %d: %r"
                           % (self.port, last))

    def __exit__(self, *exc):
        try:
            if self.c:
                self.c.cmd("clear_controller")
        except Exception:
            pass
        try:
            if self.c:
                self.c.close()
        except Exception:
            pass
        try:
            if self.proc:
                self.proc.kill()
                self.proc.wait(timeout=10)
        except Exception:
            pass
        return False


def frame(c) -> int:
    return c.j("frame")["frame"]


def wait_frame(c, target: int, timeout: float = 240.0, poll: float = 0.05,
               tick=None) -> int:
    """Free-run until `snes_frame_counter >= target`.

    Polls `frame`; never uses run_to_frame (which *pauses* on arrival,
    debug_server.c:1589) and never pauses/steps.  `tick(c, f)` is called on
    every poll — that is how the PAR freeze is re-applied.
    """
    deadline = time.time() + timeout
    f = frame(c)
    while f < target:
        if time.time() > deadline:
            raise RuntimeError("wait_frame timeout: at %d want %d" % (f, target))
        if tick:
            tick(c, f)
        time.sleep(poll)
        f = frame(c)
    return f


def load_state(c, name: str) -> dict:
    p = state_path(name)
    if not os.path.exists(p):
        raise RuntimeError("missing savestate %s" % p)
    r = c.j("load_state " + fwd(p))
    if not r.get("ok"):
        raise RuntimeError("load_state %s failed: %r" % (name, r))
    return r


# The attract cycle's fight screen is identified by its PPU configuration:
# BG1SC ($2107) reads 0x6b there and 0x69/0x71 on every other attract screen.
# This is a *labelling* aid for the sweep, never the shipping gate — P5
# forbids gating on a register mirror.
FIGHT_MARKER_BG1SC = 0x6b


def attract_fight_anchor(c, limit: int = 6000) -> int:
    """Free-run from boot until the attract fight's PPU config appears."""
    f = frame(c)
    while f < limit:
        ppu = c.j("get_ppu_state")
        if int(ppu["bgXsc"][0], 16) == FIGHT_MARKER_BG1SC:
            return f
        f = wait_frame(c, f + 10)
    raise RuntimeError("attract fight never reached within %d frames" % limit)


def reach_scene(c, scene: dict) -> dict:
    """Drive a fresh process to `scene`. Returns {'entry_frame','frame'}."""
    kind = scene["kind"]
    if kind == "wram":
        # Reaching an attract scene by free-running costs ~55 s (the fight is
        # ~3150 frames past reset), and the per-layer sweep needs one fresh
        # process PER LAYER.  So the first visit banks a savestate and every
        # later visit loads it — a load is ~2 s and carries the exact VRAM /
        # CGRAM / OAM the scene had.  The state is still *entered* through the
        # WRAM gate, never a frame offset.
        cache = ensure_dir(os.path.join(OUT_ROOT, "scene_states"))
        p = os.path.join(cache, scene["name"] + ".state")
        if os.path.exists(p) and not scene.get("no_state_cache"):
            wait_frame(c, 40)
            r = c.j("load_state " + fwd(p))
            if r.get("ok"):
                entry = frame(c)
                wait_frame(c, entry + 30)
                m, sm = read_mode(c)
                if m == scene["mode"] and (scene.get("sub") is None
                                           or sm == scene["sub"]):
                    return dict(entry_frame=entry, frame=frame(c),
                                from_cached_state=p,
                                gate=dict(mode="0x%04x" % m,
                                          sub="0x%04x" % sm))
                # The cached state does not satisfy the gate: fall through and
                # re-derive it rather than trusting a stale file.
                print("[ws_recon] cached state %s failed the WRAM gate "
                      "(read 0x%04x/0x%04x) — re-deriving" % (p, m, sm))
        entry = wait_for_mode(c, scene["mode"], scene.get("sub"))
        wait_frame(c, entry + scene["settle"])
        if not scene.get("no_state_cache"):
            c.j("save_state " + fwd(p))
        return dict(entry_frame=entry, frame=frame(c),
                    gate=dict(mode="0x%04x" % scene["mode"],
                              sub=("0x%04x" % scene["sub"])
                              if scene.get("sub") is not None else None))
    if kind == "boot":
        entry = 0
        wait_frame(c, scene["frames"])
    elif kind == "state":
        wait_frame(c, 40)          # let the process finish booting
        load_state(c, scene["state"])
        entry = frame(c)
        wait_frame(c, entry + scene["settle"])
    elif kind == "par":
        wait_frame(c, 40)
        load_state(c, scene["state"])
        entry = frame(c)
        # PAR-freeze on every poll tick until the settle window elapses; the
        # round ends into the quote box partway through.
        wait_frame(c, entry + scene["settle"],
                   tick=lambda cc, f: par_freeze(cc))
    else:
        raise RuntimeError("unknown scene kind %r" % kind)
    return dict(entry_frame=entry, frame=frame(c))


# --------------------------------------------------------------------------
# Hex helpers
# --------------------------------------------------------------------------

def unhex(s: str) -> bytes:
    return bytes.fromhex(s.replace(" ", ""))


def dump_ram(c, addr: int = 0, length: int = 0x20000) -> bytes:
    r = c.j("dump_ram %x %d" % (addr, length))
    if "hex" not in r:
        raise RuntimeError("dump_ram failed: %r" % r)
    return unhex(r["hex"])


def dump_frame_wram(c, f: int, addr: int = 0, length: int = 0x20000) -> bytes:
    r = c.j("dump_frame_wram %d %x %d" % (f, addr, length))
    if "hex" not in r:
        raise RuntimeError("dump_frame_wram(%d) failed: %r" % (f, r))
    return unhex(r["hex"])


def dump_vram(c, addr: int = 0, length: int = 0x10000) -> bytes:
    r = c.j("dump_vram %x %d" % (addr, length))
    if "hex" not in r:
        raise RuntimeError("dump_vram failed: %r" % r)
    return unhex(r["hex"])


def rd8(buf: bytes, addr: int) -> int:
    return buf[addr]


def rd16(buf: bytes, addr: int) -> int:
    return buf[addr] | (buf[addr + 1] << 8)


def s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def timeseries(c, addr: int, length: int = 1, frm: int | None = None,
               to: int | None = None, limit: int = 4096) -> list:
    """Change-compressed WRAM history for [addr, addr+length)."""
    parts = ["wram_timeseries %x %d" % (addr, length)]
    if frm is not None or to is not None:
        parts.append(str(frm if frm is not None else -1))
        parts.append(str(to if to is not None else -1))
        parts.append(str(limit))
    r = c.j(" ".join(parts))
    if not r.get("ok"):
        raise RuntimeError("wram_timeseries failed: %r" % r)
    return r["entries"]


def history(c) -> dict:
    return c.j("history")["history"]


# --------------------------------------------------------------------------
# PPU register decoding
# --------------------------------------------------------------------------

SCREEN_SIZE = {0: "32x32", 1: "64x32", 2: "32x64", 3: "64x64"}
SCREEN_TILES = {0: (32, 32), 1: (64, 32), 2: (32, 64), 3: (64, 64)}

# BG char base nibbles live in $2107..$210A?  No: BG12NBA ($210B) holds BG1/BG2
# char bases, BG34NBA ($210C) holds BG3/BG4.  The engine packs both into
# `bgTileAdr` as nibbles [BG1,BG2,BG3,BG4] from the low nibble up.  This
# ordering is verified empirically in recon_screens.py rather than assumed —
# BG12NBA/BG34NBA confusion is a documented day-loss (LOCALIZATION_PLAYBOOK).


def hexint(v):
    if isinstance(v, str):
        return int(v, 16) if v.startswith("0x") else int(v, 16)
    return int(v)


def decode_layers(ppu: dict) -> list:
    """Per-BG-layer register decode from a get_ppu_state reply."""
    tile_adr = hexint(ppu["bgTileAdr"])
    layers = []
    for i in range(4):
        sc = hexint(ppu["bgXsc"][i])
        size_bits = sc & 3
        map_base_words = (sc >> 2) << 10          # 1 KWord units
        char_nib = (tile_adr >> (4 * i)) & 0xF
        tw, th = SCREEN_TILES[size_bits]
        layers.append(dict(
            layer=i + 1,
            bgXsc="0x%02x" % sc,
            screen_size=SCREEN_SIZE[size_bits],
            tiles_w=tw, tiles_h=th,
            map_base_word=map_base_words,
            map_base_byte="0x%04x" % (map_base_words * 2),
            char_base_nibble=char_nib,
            char_base_word=char_nib << 12,
            char_base_byte="0x%05x" % ((char_nib << 12) * 2),
            hscroll=ppu["hScroll"][i],
            vscroll=ppu["vScroll"][i],
            main=bool(hexint(ppu["screenEnabled"][0]) & (1 << i)),
            sub=bool(hexint(ppu["screenEnabled"][1]) & (1 << i)),
        ))
    return layers


def obj_enabled(ppu: dict) -> dict:
    return dict(main=bool(hexint(ppu["screenEnabled"][0]) & 0x10),
                sub=bool(hexint(ppu["screenEnabled"][1]) & 0x10))


BADR_NAMES = {
    0x0d: "BG1HOFS", 0x0e: "BG1VOFS",
    0x0f: "BG2HOFS", 0x10: "BG2VOFS",
    0x11: "BG3HOFS", 0x12: "BG3VOFS",
    0x13: "BG4HOFS", 0x14: "BG4VOFS",
    0x00: "INIDISP", 0x01: "OBSEL", 0x05: "BGMODE", 0x06: "MOSAIC",
    0x07: "BG1SC", 0x08: "BG2SC", 0x09: "BG3SC", 0x0a: "BG4SC",
    0x0b: "BG12NBA", 0x0c: "BG34NBA",
    0x15: "M7SEL", 0x1a: "M7SEL2",
    0x21: "CGADD", 0x22: "CGDATA",
    0x23: "W12SEL", 0x24: "W34SEL", 0x25: "WOBJSEL",
    0x26: "WH0", 0x27: "WH1", 0x28: "WH2", 0x29: "WH3",
    0x2a: "WBGLOG", 0x2b: "WOBJLOG",
    0x2c: "TM", 0x2d: "TS", 0x2e: "TMW", 0x2f: "TSW",
    0x30: "CGWSEL", 0x31: "CGADSUB", 0x32: "COLDATA", 0x33: "SETINI",
    0x18: "VMDATAL", 0x19: "VMDATAH", 0x04: "OAMDATA",
}


def decode_hdma(dma: dict) -> list:
    out = []
    for ch in dma["channels"]:
        if not ch["hdmaActive"]:
            continue
        b = hexint(ch["bAdr"])
        out.append(dict(ch=ch["ch"], bAdr="0x%02x" % b,
                        bAdr_name=BADR_NAMES.get(b, "?"),
                        mode=ch["mode"], indirect=ch["indirect"],
                        aBank="0x%02x" % hexint(ch["aBank"]),
                        tableAdr=ch["tableAdr"]))
    return out


def decode_bg_map(vram: bytes, map_base_word: int, tiles_w: int,
                  tiles_h: int) -> list:
    """Tilemap entries as rows[ty][tx], honouring the SNES quadrant layout.

    A 64-tile-wide screen does NOT store its right half contiguously: it lives
    at map_base + 0x400 WORDS (0x800 bytes), and a 64-tile-high screen puts
    its bottom half at + (wider ? 0x800 : 0x400) words.  Mirrors
    `PPU_bgTilemapAdr` + the quadrant arithmetic in
    snesrecomp/runner/src/snes/ppu.c (PpuDrawBackground_*).  Getting this wrong
    is the "64-wide map right half at base+0x800" pitfall in
    docs/LOCALIZATION_PLAYBOOK.md.
    """
    wider = tiles_w > 32
    higher = tiles_h > 32
    rows = []
    for ty in range(tiles_h):
        row = []
        for tx in range(tiles_w):
            off = map_base_word + ((ty & 31) << 5) + (tx & 31)
            if tx >= 32 and wider:
                off += 0x400
            if ty >= 32 and higher:
                off += 0x800 if wider else 0x400
            b = off * 2
            row.append(vram[b] | (vram[b + 1] << 8) if b + 1 < len(vram)
                       else 0)
        rows.append(row)
    return rows


def column_occupancy(rows: list, ty0: int, ty1: int) -> list:
    """Per-column authored-content fraction over the row band [ty0, ty1].

    Two measures per column: how many entries are non-zero, and how many
    differ from the band's modal entry (which is what a blank/backdrop tile
    looks like when it is not literally 0).
    """
    band = rows[ty0:ty1 + 1]
    hist = {}
    for r in band:
        for e in r:
            hist[e] = hist.get(e, 0) + 1
    modal = max(hist.items(), key=lambda kv: kv[1])[0] if hist else 0
    n = len(band)
    out = []
    for tx in range(len(rows[0])):
        col = [r[tx] for r in band]
        out.append(dict(
            tx=tx,
            nonzero=sum(1 for e in col if e & 0x3FF),
            differs_from_modal=sum(1 for e in col if e != modal),
            rows=n,
            distinct=len(set(col)),
        ))
    return out, modal


def line_variance(lines: list) -> dict:
    """Which per-line PPU registers actually vary across the frame.

    Per-line variance in h[]/v[] is HDMA (or a raster IRQ) driving scroll;
    that is what tells a parallax layer from a flat one.
    """
    keys = {}
    for k in ("cgwsel", "cgadsub", "windowsel", "wbgobjlog"):
        keys[k] = sorted({ln[k] for ln in lines})
    for i in range(4):
        keys["h%d" % (i + 1)] = sorted({ln["h"][i] for ln in lines})
        keys["v%d" % (i + 1)] = sorted({ln["v"][i] for ln in lines})
    keys["w1"] = sorted({tuple(ln["w1"]) for ln in lines})
    keys["w2"] = sorted({tuple(ln["w2"]) for ln in lines})
    keys["enabled"] = sorted({tuple(ln["enabled"]) for ln in lines})
    return {k: dict(distinct=len(v), values=v[:12]) for k, v in keys.items()}


# --------------------------------------------------------------------------
# BMP
# --------------------------------------------------------------------------

def read_bmp(path: str):
    """Read a 24-bit top-down BMP as (w, h, rows[y][x] = (r,g,b))."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] != b"BM":
        raise RuntimeError("not a BMP: %s" % path)
    off = struct.unpack_from("<I", data, 10)[0]
    w = struct.unpack_from("<i", data, 18)[0]
    h = struct.unpack_from("<i", data, 22)[0]
    bpp = struct.unpack_from("<H", data, 28)[0]
    if bpp != 24:
        raise RuntimeError("expected 24bpp, got %d" % bpp)
    top_down = h < 0
    h = abs(h)
    stride = (w * 3 + 3) & ~3
    rows = []
    for y in range(h):
        sy = y if top_down else (h - 1 - y)
        base = off + sy * stride
        row = []
        for x in range(w):
            b, g, r = data[base + x * 3: base + x * 3 + 3]
            row.append((r, g, b))
        rows.append(row)
    return w, h, rows


def dominant_color(rows):
    hist = {}
    for row in rows:
        for px in row:
            hist[px] = hist.get(px, 0) + 1
    return max(hist.items(), key=lambda kv: kv[1])


def non_backdrop_fraction(rows, backdrop, x0=None, x1=None):
    x0 = 0 if x0 is None else x0
    n = 0
    tot = 0
    for row in rows:
        hi = len(row) if x1 is None else x1
        for x in range(x0, hi):
            tot += 1
            if row[x] != backdrop:
                n += 1
    return (n / tot) if tot else 0.0


# --------------------------------------------------------------------------
# Snapshot bundle
# --------------------------------------------------------------------------

def snapshot(c, dest: str, tag: str, with_vram: bool = True,
             with_wram: bool = True, oam_snaps: int = 2) -> dict:
    """Write the full PPU/DMA/OAM/CGRAM/VRAM/WRAM evidence bundle for `tag`."""
    ensure_dir(dest)
    f = frame(c)
    bundle = dict(tag=tag, frame=f)

    ppu = c.j("get_ppu_state")
    dma = c.j("get_dma_state")
    lines = c.j("ppu_lines 0 224")
    windows = {}
    for layer in range(6):
        windows["layer%d" % layer] = c.j("ppu_window 100 %d" % layer)
    oam_render = c.j("oam_render_get %d 128" % oam_snaps)
    oam_raw = c.j("dump_oam")
    cgram = c.j("dump_cgram")
    irq = c.j("get_interrupt_state")

    def w(name, obj):
        p = os.path.join(dest, "%s_%s.json" % (tag, name))
        with open(p, "w") as fh:
            json.dump(obj, fh, indent=1)
        return p

    bundle["files"] = {
        "ppu": w("ppu", ppu),
        "dma": w("dma", dma),
        "lines": w("lines", lines),
        "windows": w("windows", windows),
        "oam_render": w("oam_render", oam_render),
        "oam_raw": w("oam", oam_raw),
        "cgram": w("cgram", cgram),
        "irq": w("irq", irq),
    }
    if with_vram:
        bundle["files"]["vram"] = w("vram", c.j("dump_vram 0 65536"))
    if with_wram:
        p = os.path.join(dest, "%s_wram.bin" % tag)
        with open(p, "wb") as fh:
            fh.write(dump_ram(c, 0, 0x20000))
        bundle["files"]["wram"] = p

    shot = os.path.join(dest, "%s.bmp" % tag)
    r = c.j("screenshot " + fwd(shot))
    bundle["screenshot"] = r
    bundle["files"]["bmp"] = shot

    # Digest of the register state — what recon_screens.py summarises.
    bundle["summary"] = dict(
        bgmode=ppu["bgmode"],
        inidisp=ppu["inidisp"],
        obsel=ppu["obsel"],
        setini=ppu["setini"],
        screenEnabled=ppu["screenEnabled"],
        screenWindowed=ppu["screenWindowed"],
        cgwsel=ppu["cgwsel"],
        cgadsub=ppu["cgadsub"],
        fixedColor=ppu["fixedColor"],
        windowsel=ppu["windowsel"],
        wbgobjlog=ppu["wbgobjlog"],
        window1=[ppu["window1left"], ppu["window1right"]],
        window2=[ppu["window2left"], ppu["window2right"]],
        layers=decode_layers(ppu),
        obj=obj_enabled(ppu),
        hdma=decode_hdma(dma),
        line_variance=line_variance(lines["lines"]),
        widescreen=ppu["widescreen"],
    )
    with open(os.path.join(dest, "%s_bundle.json" % tag), "w") as fh:
        json.dump(bundle, fh, indent=1)
    return bundle


def write_json(path: str, obj) -> str:
    ensure_dir(os.path.dirname(path))
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=1)
    return path


# --------------------------------------------------------------------------
# Per-layer BMP capture (SNESRECOMP_LAYER_MASK is read ONCE at the first
# PpuBeginDrawing, so each mask needs its own fresh process).
# --------------------------------------------------------------------------

LAYER_BITS = {"bg1": 0x01, "bg2": 0x02, "bg3": 0x04, "bg4": 0x08,
              "obj": 0x10, "all": 0x1f}


def layer_capture(scene: dict, layer_name: str, port: int, dest: str,
                  lang: str = "en") -> dict:
    """One fresh process with SNESRECOMP_LAYER_MASK set, driven to `scene`."""
    mask = LAYER_BITS[layer_name]
    tcp.write_state(lang, BUILD)
    env = dict(os.environ)
    env["SNESRECOMP_DEBUG_PORT"] = str(port)
    env["SDL_AUDIODRIVER"] = "dummy"
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SNESRECOMP_LAYER_MASK"] = str(mask)
    proc = subprocess.Popen([EXE, "--no-launcher", ROM], cwd=BUILD, env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    c = None
    try:
        c = Conn(port)
        info = reach_scene(c, scene)
        shot = os.path.join(ensure_dir(dest),
                            "%s_%s.bmp" % (scene["name"], layer_name))
        r = c.j("screenshot " + fwd(shot))
        return dict(layer=layer_name, mask=mask, path=shot,
                    frame=r.get("frame"), width=r.get("width"),
                    ws_extra=r.get("ws_extra"), reached=info)
    finally:
        try:
            if c:
                c.cmd("clear_controller")
                c.close()
        except Exception:
            pass
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass


__all__ = [n for n in dir() if not n.startswith("_")]
