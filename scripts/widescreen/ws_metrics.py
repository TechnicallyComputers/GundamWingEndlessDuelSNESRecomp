"""Shared measurement library for the GWED 16:9 widescreen VERDICT scripts.

Beads beads-8wg.9.13.7.  Companion to `ws_recon.py` (Beads beads-8wg.9.13.2),
which is the *recon* library: it answers "what does the game do".  This module
is the *verdict* library: it answers "is the widescreen presentation correct",
and every one of its answers is a measurement with an evidence path.

It is deliberately standalone rather than importing `ws_recon.py`:

* `ws_recon.py` pins ``BUILD`` / ``PORTS`` to the recon agent's build dir and
  port block (4471-4479).  The verdict scripts own 4481-4489 and a different
  build dir, and both agents run at the same time.
* the recon module is still being written; a verdict harness that imports a
  moving module reports the wrong thing for reasons that have nothing to do
  with widescreen.

What *is* shared, on purpose: `tools/validation_states/tcp.py` (imported, never
edited) supplies the `Conn` client, and the detector maths is ported from
DKC2Recomp (`scripts/capture_widescreen_diagnostics.py`
`read_bmp_margin_metrics`, `scripts/audit_widescreen_route.py` `edge_score`).

Standing rules baked in here
----------------------------
* **Scene identity never comes from pixels.**  It comes from a savestate entry
  plus the WRAM gate proven in recon (`--gate-json .../recon/gate.json`:
  mode word ``$7E:1004`` and the frame-liveness counter ``$7E:0600``), read
  with `read_ram`.  Pixel comparisons are *verdicts*, never identity.
* **Rings, not arm-then-run.**  Nothing here arms a trace.  `run_to_frame`
  is never used: it PAUSES on arrival (debug_server.c), so this module polls
  `frame` and free-runs.  Nothing pauses, steps or breaks.
* **One fresh process per `SNESRECOMP_LAYER_MASK`.**  The mask is latched at
  the first `PpuBeginDrawing`.
* **`SNESRECOMP_WS_EXTRA` is the geometry authority.**  0 forces the authentic
  256-wide frame *even with the mod package enabled*
  (`src/gwed_display.c GwedDisplay_ComputeFrameWidth`), so the 4:3 and wide
  sides of a parity test differ by one environment variable and nothing else
  -- in particular they do not need two different `state.toml` files, which is
  what makes running them from one build directory safe.
* **`mods/preloaded/state.toml` is per-build-dir shared state.**  Runs are
  sequential by default; `--stage-dir` makes a private copy of the exe
  directory for callers that must overlap.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import uuid
from collections import Counter

# ── paths ───────────────────────────────────────────────────────────────────

WT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
MAIN_ROOT = r"F:\Projects\snesrecomp\GundamWingEndlessDuelSNESRecomp"

DEFAULT_BUILD = os.path.join(WT_ROOT, "build-ws-trace2")
EXE_NAME = "GundamWingEndlessDuelSNESRecomp.exe"
DEFAULT_ROM = os.path.join(
    MAIN_ROOT, "Shin Kidou Senki Gundam W - Endless Duel (J).smc")
# Savestates are gitignored, so they exist only in the primary checkout.
DEFAULT_STATES_DIR = os.path.join(MAIN_ROOT, "tools", "validation_states")
DEFAULT_GATE_JSON = os.path.join(WT_ROOT, "analysis", "widescreen", "recon",
                                 "gate.json")
VERIFY_ROOT = os.path.join(WT_ROOT, "analysis", "widescreen", "verify")

sys.path.insert(0, os.path.join(WT_ROOT, "tools", "validation_states"))
import tcp  # noqa: E402  (path juggling is required; tcp.py is never edited)

Conn = tcp.Conn

# This agent's assigned port block (recon owns 4471-4479, P16 owns 4491-4499).
PORT_BASE = 4481
PORT_LIMIT = 4489

# Widescreen geometry.  342 = ceil_even(256 * 4/3): 16:9 at the 7:6 CRT pixel
# aspect, which is what `SnesDisplayAspect_ComputeWideFrameWidth(256)` returns.
NATIVE_WIDTH = 256
FRAME_HEIGHT = 224
DEFAULT_WS_EXTRA = 43
WIDE_WIDTH = NATIVE_WIDTH + 2 * DEFAULT_WS_EXTRA        # 342

LAYER_MASKS = {"composite": 0xFF, "bg1": 0x01, "bg2": 0x02, "bg3": 0x04,
               "bg4": 0x08, "obj": 0x10}
BACKGROUND_LAYERS = ("bg1", "bg2", "bg3", "bg4")

# Seam thresholds, ported verbatim from DKC2 audit_widescreen_route.py.
SEAM_RATIO = 3.0
SEAM_EXCESS = 18.0
# margin_non_blank: a margin counts as painted when its own non-backdrop
# fraction reaches this share of what the native window itself achieves.
MARGIN_SHARE = 0.25
# Layers this empty in the native window have nothing to say about margins.
LAYER_PRESENT_MIN = 0.05

# OBSEL sprite geometry (small, large), from scripts/render_oam_capture.py.
OBJ_SIZES = [((8, 8), (16, 16)), ((8, 8), (32, 32)), ((8, 8), (64, 64)),
             ((16, 16), (32, 32)), ((16, 16), (64, 64)), ((32, 32), (64, 64)),
             ((16, 32), (32, 64)), ((16, 32), (32, 32))]


class HarnessError(Exception):
    """Something about the measurement setup is wrong -> exit 2, not a verdict."""


WINDOWS_PYTHON = (r"C:\Users\Matthew\AppData\Local\Programs\Python\Python312"
                  r"\python.exe")


def require_windows_python() -> None:
    """Refuse to run under the MSYS python that is first on PATH here.

    `c:\\devkitPro\\msys2\\usr\\bin\\python.exe` reports POSIX paths from
    `os.path.abspath`, so every absolute path this harness hands to the game
    (`screenshot`, `load_state`) comes out as `/f/Projects/...`, which the
    exe's `fopen` cannot open -- and the failure surfaces as "screenshot
    failed", i.e. as a fake widescreen defect.  Fail loudly instead.
    """
    if os.path.abspath(__file__).startswith("/"):
        raise HarnessError(
            "this is an MSYS python (%s) and it reports POSIX paths; re-run "
            "with the native interpreter:\n    & '%s' ws_verdicts.py ..."
            % (sys.executable, WINDOWS_PYTHON))


def fwd(path: str) -> str:
    """Absolute forward-slash path -- what `screenshot` and `load_state` want."""
    return os.path.abspath(path).replace("\\", "/")


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def parse_int(value) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0) if value.lower().startswith("0x") else int(value, 16)
    raise TypeError(repr(value))


# ── build / provenance ──────────────────────────────────────────────────────

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_rev() -> str:
    """HEAD of the worktree.  MSYS git chokes on this worktree's gitdir, so a
    failure is reported as unknown rather than guessed."""
    env_rev = os.environ.get("GWED_GIT_REV")
    if env_rev:
        return env_rev
    try:
        out = subprocess.run(["git", "-C", WT_ROOT, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def exe_path(build: str) -> str:
    p = os.path.join(build, EXE_NAME)
    if not os.path.isfile(p):
        raise HarnessError("no exe at %s (configure/build that dir first)" % p)
    return p


# ── mods/preloaded/state.toml ───────────────────────────────────────────────
#
# The live feature state file.  Shape copied from tools/validation_states/tcp.py
# `write_state`, with the widescreen package block added beside the
# localization one.  The Mods package is the SOLE widescreen authority
# (Beads beads-8wg.1.10) -- there is no config.ini key to set.

STATE_TOML = """format_version = 1

[[package]]
id = "gwed.localization"
version = "1.0.0"

[[feature]]
package_id = "gwed.localization"
id = "localization"
enabled = true

[feature.values]
language = "%(lang)s"

[[package]]
id = "gwed.enhancement.widescreen"
version = "1.0.0"

[[feature]]
package_id = "gwed.enhancement.widescreen"
id = "widescreen"
enabled = %(ws)s
"""


def write_state_toml(build: str, widescreen: bool, lang: str = "en") -> str:
    p = os.path.join(build, "mods", "preloaded", "state.toml")
    ensure_dir(os.path.dirname(p))
    body = STATE_TOML % {"lang": lang, "ws": "true" if widescreen else "false"}
    for _ in range(20):
        try:
            with open(p, "w", newline="\n") as fh:
                fh.write(body)
            return p
        except OSError:
            time.sleep(0.5)
    raise HarnessError("state.toml contended: %s" % p)


def stage_build(src_build: str, dst_dir: str) -> str:
    """Private copy of an exe directory.

    `mods/preloaded/state.toml` is shared by every process launched from a
    build dir, so overlapping instances need their own.  Copies file-by-file
    (`Copy-Item src\\dir\\* dst\\dir\\`, never `Copy-Item src\\dir dst\\dir`,
    which nests).
    """
    ensure_dir(dst_dir)
    for name in os.listdir(src_build):
        s = os.path.join(src_build, name)
        d = os.path.join(dst_dir, name)
        if name in ("CMakeFiles", "CMakeCache.txt", ".ninja_deps",
                    ".ninja_log", "build.ninja"):
            continue
        if os.path.isdir(s):
            if not os.path.isdir(d):
                shutil.copytree(s, d)
        elif not os.path.exists(d) or os.path.getmtime(s) > os.path.getmtime(d):
            shutil.copy2(s, d)
    return dst_dir


# ── one measured process ────────────────────────────────────────────────────

class Instance:
    """One trace-build process plus its debug connection.

    Context manager; `clear_controller` and the process kill always run in the
    finally path (set_controller LATCHES -- see the standing input-injection
    rule) even though these verdict scripts inject nothing.
    """

    def __init__(self, port: int, build: str, rom: str, ws_extra: int,
                 layer_mask: int | None = None, widescreen: bool = True,
                 lang: str = "en", log_path: str | None = None,
                 extra_env: dict | None = None):
        self.port = port
        self.build = build
        self.rom = rom
        self.ws_extra = ws_extra
        self.layer_mask = layer_mask
        self.widescreen = widescreen
        self.lang = lang
        self.log_path = log_path
        self.extra_env = dict(extra_env or {})
        self.proc = None
        self.c = None
        self._log = None

    def env(self) -> dict:
        env = dict(os.environ)
        env["SNESRECOMP_DEBUG_PORT"] = str(self.port)
        env["SDL_VIDEODRIVER"] = "dummy"
        env["SDL_AUDIODRIVER"] = "dummy"
        # Pinned geometry: 0 forces the authentic 4:3 frame even with the
        # package enabled, 43 pins the 16:9 margin.  Never left to the window.
        env["SNESRECOMP_WS_EXTRA"] = str(self.ws_extra)
        if self.layer_mask is None:
            env.pop("SNESRECOMP_LAYER_MASK", None)
        else:
            env["SNESRECOMP_LAYER_MASK"] = str(self.layer_mask)
        # Single-file/dir BMP dumping must not fight a caller's series capture.
        for k in ("SNESRECOMP_FRAME_BMP", "SNESRECOMP_FRAME_BMP_FRAME",
                  "SNESRECOMP_FRAME_BMP_DIR", "SNESRECOMP_FRAME_BMP_START",
                  "SNESRECOMP_FRAME_BMP_STEP", "SNESRECOMP_FRAME_BMP_END"):
            env.pop(k, None)
        env.update(self.extra_env)
        return env

    def __enter__(self):
        write_state_toml(self.build, self.widescreen, self.lang)
        exe = exe_path(self.build)
        if self.log_path:
            ensure_dir(os.path.dirname(self.log_path))
            self._log = open(self.log_path, "wb")
            out = self._log
        else:
            out = subprocess.DEVNULL
        self.proc = subprocess.Popen([exe, "--no-launcher", self.rom],
                                     cwd=self.build, env=self.env(),
                                     stdout=out, stderr=subprocess.STDOUT)
        last = None
        for _ in range(4):
            if self.proc.poll() is not None:
                raise HarnessError("process exited rc=%r before listening (%s)"
                                   % (self.proc.returncode, self.log_path))
            try:
                self.c = Conn(self.port)
                return self
            except Exception as e:  # noqa: BLE001
                last = e
        raise HarnessError("no debug connection on port %d: %r"
                           % (self.port, last))

    def __exit__(self, *exc):
        for fn in (lambda: self.c and self.c.cmd("clear_controller"),
                   lambda: self.c and self.c.close(),
                   lambda: self.proc and self.proc.kill(),
                   lambda: self.proc and self.proc.wait(timeout=10),
                   lambda: self._log and self._log.close()):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        return False


def frame(c) -> int:
    return c.j("frame")["frame"]


def wait_frame(c, target: int, timeout: float = 300.0, poll: float = 0.05,
               tick=None) -> int:
    """Free-run until `snes_frame_counter >= target`.

    Polls `frame`.  Never `run_to_frame`, which pauses the guest on arrival.
    """
    deadline = time.time() + timeout
    f = frame(c)
    while f < target:
        if time.time() > deadline:
            raise HarnessError("wait_frame timeout: at %d want %d" % (f, target))
        if tick:
            tick(c, f)
        time.sleep(poll)
        f = frame(c)
    return f


def load_state(c, name: str, states_dir: str) -> dict:
    p = os.path.join(states_dir, name + ".state")
    if not os.path.isfile(p):
        raise HarnessError("missing savestate %s" % p)
    r = c.j("load_state " + fwd(p))
    if not r.get("ok"):
        raise HarnessError("load_state %s failed: %r" % (name, r))
    return r


# ── the WRAM gate (scene identity; NEVER pixels) ────────────────────────────

class Gate:
    """The proven WRAM scene gate from recon (`analysis/.../recon/gate.json`).

    `mode` is a 16-bit word that names the sub-mode; `liveness` is a counter
    that only advances while a round is actually running.  Scene identity is
    the CONJUNCTION (WIDESCREEN_PATTERNS P5/P6): the victory quote passes the
    coarse battle-family word on its own, which is exactly the defect the
    liveness term exists to reject.
    """

    def __init__(self, spec: dict | None, source: str | None):
        self.source = source
        self.spec = spec
        self.verified = spec is not None
        if spec is None:
            self.mode_addr = self.mode_width = self.mode_value = None
            self.live_addr = None
            return
        g = spec["gate"]
        self.mode_addr = self._addr(g["mode"]["addr"])
        self.mode_width = int(g["mode"].get("width", 2))
        self.mode_value = parse_int(g["mode"]["value"])
        self.live_addr = self._addr(g["liveness"]["addr"])
        self.live_width = int(g["liveness"].get("width", 1))

    @staticmethod
    def _addr(text) -> int:
        """'$7E:1004' / '0x1004' / 4100 -> WRAM offset."""
        if isinstance(text, int):
            return text & 0x1FFFF
        s = str(text).replace("$", "").replace("_", "")
        if ":" in s:
            bank, off = s.split(":", 1)
            base = (int(bank, 16) - 0x7E) << 16
            return base + int(off, 16)
        return int(s, 16)

    def read(self, c) -> dict:
        if not self.verified:
            return {"verified": False,
                    "reason": "no --gate-json; scene identity UNVERIFIED"}
        mode = read_ram(c, self.mode_addr, self.mode_width)
        live0 = read_ram(c, self.live_addr, self.live_width)
        f0 = frame(c)
        # Liveness is a per-frame counter: sample it across real guest frames.
        wait_frame(c, f0 + 8, timeout=30.0)
        live1 = read_ram(c, self.live_addr, self.live_width)
        return {"verified": True,
                "mode_addr": "0x%05x" % self.mode_addr,
                "mode": "0x%04x" % mode,
                "mode_expected_fight": "0x%04x" % self.mode_value,
                "is_fight_mode": mode == self.mode_value,
                "liveness_addr": "0x%05x" % self.live_addr,
                "liveness": [live0, live1],
                "is_live": live0 != live1,
                "is_live_fight": mode == self.mode_value and live0 != live1}


def load_gate(path: str | None, allow_missing: bool) -> Gate:
    if not path:
        if not allow_missing:
            raise HarnessError("--gate-json is required unless --no-gate")
        return Gate(None, None)
    if not os.path.isfile(path):
        if not allow_missing:
            raise HarnessError("no gate spec at %s (pass --no-gate to run "
                               "anyway with scene identity UNVERIFIED)" % path)
        return Gate(None, None)
    with open(path) as fh:
        return Gate(json.load(fh), path)


def read_ram(c, addr: int, length: int = 1) -> int:
    """Little-endian integer from `read_ram <hex addr> <dec len>`."""
    r = c.j("read_ram %x %d" % (addr, length))
    hexs = r.get("hex") or r.get("data")
    if hexs is None:
        raise HarnessError("read_ram %x %d failed: %r" % (addr, length, r))
    raw = bytes.fromhex(hexs.replace(" ", ""))[:length]
    return int.from_bytes(raw, "little")


# ── scenario entry ──────────────────────────────────────────────────────────
#
#   boot_attract[:N]  cold boot, no input, free-run to guest frame N.
#   attract_fight     cold boot, free-run until the WRAM gate says LIVE FIGHT
#                     (mode word + liveness counter), then settle.  Falls back
#                     to the recon-recorded anchor frame when --no-gate.
#   state:<name>      load tools/validation_states/<name>.state, then settle.
#
# Attract offsets are always frame numbers, never seconds: attract timing
# drifts between runs.

BOOT_ATTRACT_DEFAULT_FRAME = 900        # inside the intro crawl, well settled
ATTRACT_FIGHT_FALLBACK_FRAME = 3286     # recon gate.json sweep, attract_fight
STATE_SETTLE_DEFAULT = 400
# A savestate is loaded at a FIXED GUEST FRAME, never "as soon as the port
# answers".  Two processes reach a given guest frame at two different wall
# clocks, so loading on connection makes the post-load frame counter differ
# between the 4:3 and the wide side by however long each took to boot -- and
# then a frame-exact pixel comparison is comparing two different moments. This
# holds whether or not the savestate carries its own frame counter.
LOAD_AT_FRAME = 200


def reach_scene(c, scenario: str, gate: Gate, states_dir: str,
                settle: int = STATE_SETTLE_DEFAULT,
                load_at: int = LOAD_AT_FRAME) -> dict:
    """Drive a fresh process to `scenario`.  Returns an evidence dict."""
    if scenario.startswith("state:"):
        name = scenario.split(":", 1)[1]
        wait_frame(c, load_at)
        load_state(c, name, states_dir)
        entry = frame(c)
        wait_frame(c, entry + settle)
        return {"scenario": scenario, "entry": "load_state %s" % name,
                "entry_frame": entry, "frame": frame(c),
                "gate": gate.read(c)}

    if scenario.startswith("boot_attract"):
        target = BOOT_ATTRACT_DEFAULT_FRAME
        if ":" in scenario:
            target = int(scenario.split(":", 1)[1])
        wait_frame(c, target)
        return {"scenario": scenario, "entry": "cold boot",
                "entry_frame": 0, "frame": frame(c),
                "target_frame": target, "gate": gate.read(c)}

    if scenario == "attract_fight":
        if not gate.verified:
            wait_frame(c, ATTRACT_FIGHT_FALLBACK_FRAME)
            g = gate.read(c)
            g["reason"] = ("--no-gate: parked on the recon-recorded anchor "
                           "frame %d; scene identity UNVERIFIED"
                           % ATTRACT_FIGHT_FALLBACK_FRAME)
            return {"scenario": scenario, "entry": "cold boot + anchor frame",
                    "entry_frame": 0, "frame": frame(c), "gate": g}
        # Free-run and poll the gate.  No arming, no pausing.
        f = frame(c)
        while f < 8000:
            mode = read_ram(c, gate.mode_addr, gate.mode_width)
            if mode == gate.mode_value:
                g = gate.read(c)
                if g["is_live_fight"]:
                    wait_frame(c, frame(c) + 120)   # settle inside the round
                    return {"scenario": scenario,
                            "entry": "cold boot + WRAM gate (live fight)",
                            "entry_frame": f, "frame": frame(c),
                            "gate": gate.read(c)}
            f = wait_frame(c, f + 30)
        raise HarnessError("the attract demo's live fight never satisfied the "
                           "WRAM gate within 8000 frames")

    raise HarnessError("unknown scenario %r" % scenario)


# ── BMP ─────────────────────────────────────────────────────────────────────

def read_bmp_rgb(path: str):
    """(width, height, rows) with rows[logical_y] = bytes of RGB triples.

    Handles both producers: the debug server's `screenshot` (24-bit) and
    `runner/src/widescreen.c`'s SNESRECOMP_FRAME_BMP dumps (32-bit top-down).
    Logical Y is always top-down even when BMP storage is bottom-up.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 54 or data[:2] != b"BM":
        raise HarnessError("not a BMP: %s" % path)
    off = struct.unpack_from("<I", data, 10)[0]
    width = struct.unpack_from("<i", data, 18)[0]
    raw_h = struct.unpack_from("<i", data, 22)[0]
    bpp = struct.unpack_from("<H", data, 28)[0]
    comp = struct.unpack_from("<I", data, 30)[0]
    if width <= 0 or raw_h == 0 or bpp not in (24, 32) or comp != 0:
        raise HarnessError("unsupported BMP %dx%d %dbpp comp=%d: %s"
                           % (width, raw_h, bpp, comp, path))
    height = abs(raw_h)
    bytes_pp = bpp // 8
    stride = ((width * bytes_pp + 3) // 4) * 4
    if off + stride * height > len(data):
        raise HarnessError("truncated BMP pixel data: %s" % path)
    rows = []
    for logical_y in range(height):
        sy = height - 1 - logical_y if raw_h > 0 else logical_y
        base = off + sy * stride
        row = bytearray(width * 3)
        for x in range(width):
            p = base + x * bytes_pp
            # BMP stores BGR(A); normalise to RGB.
            row[x * 3] = data[p + 2]
            row[x * 3 + 1] = data[p + 1]
            row[x * 3 + 2] = data[p]
        rows.append(bytes(row))
    return width, height, rows


def flat_rgb(rows) -> bytes:
    return b"".join(rows)


def regions_for(width: int, ws_extra: int) -> dict:
    """left [0,extra) / native [extra, extra+256) / right [.., width)."""
    if width == NATIVE_WIDTH:
        return {"native_view": (0, NATIVE_WIDTH)}
    return {"left_margin": (0, ws_extra),
            "native_view": (ws_extra, ws_extra + NATIVE_WIDTH),
            "right_margin": (ws_extra + NATIVE_WIDTH, width)}


def margin_metrics(path: str, ws_extra: int = DEFAULT_WS_EXTRA) -> dict:
    """Per-region blankness against the DOMINANT BACKDROP colour.

    Ported from DKC2 `read_bmp_margin_metrics`.  Measuring "non-black" alone
    would call an empty layer-isolated frame completely full, because SNES
    layer isolation keeps the fixed/backdrop colour.
    """
    width, height, rows = read_bmp_rgb(path)
    hist = Counter()
    for row in rows:
        for x in range(width):
            hist[row[x * 3:x * 3 + 3]] += 1
    backdrop, backdrop_px = hist.most_common(1)[0]
    out = {"path": path, "width": width, "height": height,
           "dominant_backdrop_rgb": backdrop.hex(),
           "dominant_backdrop_pixels": backdrop_px,
           "regions": {}}
    for name, (x0, x1) in regions_for(width, ws_extra).items():
        non_black = non_backdrop = 0
        colours = set()
        for row in rows:
            for x in range(x0, x1):
                px = row[x * 3:x * 3 + 3]
                if px != b"\0\0\0":
                    non_black += 1
                    if len(colours) < 4096:
                        colours.add(px)
                if px != backdrop:
                    non_backdrop += 1
        n = (x1 - x0) * height
        out["regions"][name] = {
            "bounds_x": [x0, x1], "pixels": n,
            "non_black_pixels": non_black,
            "non_black_fraction": non_black / n if n else 0.0,
            "non_backdrop_pixels": non_backdrop,
            "non_backdrop_fraction": non_backdrop / n if n else 0.0,
            "unique_non_black_colours": len(colours)}
    return out


def uniform_band(rows, x0: int, x1: int):
    """Is [x0,x1) one single colour on every row?  -> (bool, '#RRGGBB'|None)"""
    if x1 <= x0:
        return True, None
    first = rows[0][x0 * 3:x0 * 3 + 3]
    for row in rows:
        seg = row[x0 * 3:x1 * 3]
        for i in range(0, len(seg), 3):
            if seg[i:i + 3] != first:
                return False, None
    return True, "#%02X%02X%02X" % (first[0], first[1], first[2])


def edge_score(rows, width: int, height: int, edge_x: int) -> dict:
    """Column-boundary discontinuity at `edge_x`, vs its 10 neighbours.

    Ported from DKC2 `audit_widescreen_route.py edge_score`.  A stale-margin
    seam sits at a FIXED screen coordinate while the camera moves, which is
    why the caller corroborates a hit on an adjacent sample with motion.
    """
    def difference(x: int) -> float:
        total = 0
        for row in rows:
            left = (x - 1) * 3
            right = x * 3
            total += abs(row[left] - row[right])
            total += abs(row[left + 1] - row[right + 1])
            total += abs(row[left + 2] - row[right + 2])
        return total / (height * 3)

    if edge_x <= 0 or edge_x >= width:
        return {"edge": 0.0, "nearby": 0.0, "ratio": 0.0, "excess": 0.0,
                "note": "edge outside the frame"}
    edge = difference(edge_x)
    nearby = [difference(x) for x in range(max(1, edge_x - 5),
                                           min(width, edge_x + 6))
              if x != edge_x]
    baseline = sum(nearby) / len(nearby) if nearby else 0.0
    return {"edge": round(edge, 3), "nearby": round(baseline, 3),
            "ratio": round(edge / max(baseline, 1.0), 3),
            "excess": round(edge - baseline, 3)}


def cgram_backdrop_rgb(c) -> dict:
    """CGRAM entry 0 as RGB888.  BGR555 unpack per scripts/render_vram_bg_capture.py."""
    r = c.j("dump_cgram")
    hexs = r.get("hex") or r.get("cgram")
    if not hexs:
        raise HarnessError("dump_cgram failed: %r" % r)
    raw = bytes.fromhex(hexs.replace(" ", ""))
    word = raw[0] | (raw[1] << 8)
    rgb = (((word) & 0x1F) * 255 // 31,
           ((word >> 5) & 0x1F) * 255 // 31,
           ((word >> 10) & 0x1F) * 255 // 31)
    return {"cgram0_bgr555": "0x%04x" % word,
            "rgb": "#%02X%02X%02X" % rgb, "rgb_tuple": list(rgb)}


# ── OAM decoding ────────────────────────────────────────────────────────────

def oam_slots(c, snaps: int = 1, slots: int = 128) -> dict:
    """Newest render-consumed OAM snapshot: {'frame', 'slots': [...]}.

    GWED renders from a LATCHED OAM snapshot (src/game_rtl.c), so the live
    `dump_oam` array is a frame ahead of what was drawn.  `oam_render_get` is
    the render-consumed ring -- the only OAM view that matches the pixels in
    the same capture.
    """
    r = c.j("oam_render_get %d %d" % (snaps, slots))
    snapshots = r.get("snaps") or []
    if not snapshots:
        raise HarnessError("oam_render_get returned no snapshots: %r"
                           % {k: r.get(k) for k in ("ok", "error", "count")})
    newest = snapshots[-1]
    out = []
    for i, s in enumerate(newest["slot"]):
        y, xlow, xhigh, tile, attr, big = s
        raw_x = xlow | ((xhigh & 1) << 8)
        out.append({"slot": i, "y": y, "raw_x": raw_x, "tile": tile,
                    "attr": attr, "big": bool(big),
                    "palette": (attr >> 1) & 7, "priority": (attr >> 4) & 3,
                    "flip_h": bool(attr & 0x40), "flip_v": bool(attr & 0x80)})
    return {"frame": newest.get("f"), "seq": newest.get("seq"),
            "active": newest.get("active"), "slots": out}


def obj_size_for(obsel: int, big: bool):
    small, large = OBJ_SIZES[(obsel >> 5) & 7]
    return large if big else small


def signed_x_interpretations(raw_x: int, ws_extra: int = DEFAULT_WS_EXTRA):
    """The readings of a 9-bit OAM X: the hardware one, and the engine's.

    A 9-bit X is 0..511 and hardware treats >=256 as negative (x-512): that is
    how a sprite walks off the LEFT edge.  The engine's widescreen decode
    (`runner/src/snes/ppu.c PpuDecodeOamX`) moves that wrap threshold OUT with
    the margin -- `raw >= 256 + extraRightCur` wraps, everything below stays
    positive -- so raw X in [256, 256+extra) renders in the RIGHT margin
    whether or not the game publishes `PpuWsSetOamRightHints`.  Hints only
    make that band STRICT (opt-in per slot); without them the positive
    reading is what the PPU actually uses.

    `engine` is therefore the reading a pixel check must position by, and
    `signed` is kept beside it because it is the reading a 4:3 build (or a
    hint-strict slot that was not marked) would use.  Both are reported and
    `ambiguous` still flags the window where they disagree -- a game whose
    accepted X range makes the two bands overlap needs the hints.
    """
    ambiguous = 256 <= raw_x < 256 + ws_extra
    return {"raw_x": raw_x,
            "signed": raw_x - 512 if raw_x >= 256 else raw_x,
            "engine": raw_x - 512 if raw_x >= 256 + ws_extra else raw_x,
            "right_margin_hint": raw_x if ambiguous else None,
            "ambiguous": ambiguous}


def row_backdrops(rows, width: int) -> list:
    """Per-scanline modal colour.

    A frame-wide "dominant backdrop" assumes the backdrop is ONE colour.  That
    is false for any title whose backdrop is an HDMA gradient: GWED's arena
    paints a different fixed colour on every scanline, so an OBJ-isolated
    capture measures ~85% "non-backdrop" against the frame mode and every ink
    test becomes meaningless.  The backdrop is constant ALONG a scanline
    though, so the row's own mode is the right reference -- sprites cover a
    small share of any single row in an OBJ-isolated frame, which is what
    makes the mode the backdrop and not the sprite.
    """
    out = []
    for row in rows:
        hist = Counter()
        for x in range(width):
            hist[row[x * 3:x * 3 + 3]] += 1
        out.append(hist.most_common(1)[0][0])
    return out


def rect_has_ink(rows, width: int, height: int, x0: int, y0: int, w: int,
                 h: int, backdrop: bytes, backdrop_rows: list = None) -> dict:
    """Non-backdrop pixel count inside a sprite rect, clipped to the frame.

    `backdrop_rows` (from row_backdrops) takes precedence over the single
    `backdrop` colour when supplied -- see row_backdrops for why a frame-wide
    backdrop is the wrong reference on a per-line gradient.
    """
    xa, xb = max(0, x0), min(width, x0 + w)
    ya, yb = max(0, y0), min(height, y0 + h)
    if xb <= xa or yb <= ya:
        return {"clipped_out": True, "pixels": 0, "ink": 0}
    ink = 0
    for y in range(ya, yb):
        row = rows[y]
        ref = backdrop_rows[y] if backdrop_rows else backdrop
        for x in range(xa, xb):
            if row[x * 3:x * 3 + 3] != ref:
                ink += 1
    n = (xb - xa) * (yb - ya)
    return {"clipped_out": False, "bounds": [xa, ya, xb, yb], "pixels": n,
            "ink": ink, "ink_fraction": ink / n if n else 0.0,
            "backdrop_reference": "per_row" if backdrop_rows else "frame"}


# ── reporting ───────────────────────────────────────────────────────────────

def finding(kind: str, confidence: str, evidence: str, **extra) -> dict:
    """DKC2 `audit_widescreen_route.py finding()` shape."""
    item = {"kind": kind, "confidence": confidence, "evidence": evidence}
    item.update(extra)
    return item


class Report:
    """summary.json in the DKC2 report shape, plus the PASS/FAIL console lines."""

    def __init__(self, check: str, scenario: str, out_dir: str, build: str,
                 rom: str, env: dict, gate: Gate):
        self.check = check
        self.scenario = scenario
        self.out_dir = ensure_dir(out_dir)
        exe = exe_path(build)
        st = os.stat(exe)
        self.doc = {
            "run_id": uuid.uuid4().hex[:12],
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "git_rev": git_rev(),
            "check": check,
            "scenario": scenario,
            "build": build,
            "exe": exe,
            "exe_sha256": sha256_file(exe),
            "exe_mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                       time.gmtime(st.st_mtime)),
            "rom": rom,
            "env": env,
            "gate_spec": gate.source,
            "gate_verified": gate.verified,
            "checks": {},
            "findings": [],
            "status": "PASS",
        }

    def add(self, name: str, result: dict) -> dict:
        self.doc["checks"][name] = result
        status = result.get("status", "SKIP")
        extra = ""
        for key in ("detail", "summary"):
            if result.get(key):
                extra = "  -- %s" % result[key]
                break
        print("%s %s %s%s" % (status, name, self.scenario, extra))
        if status == "FAIL":
            self.doc["status"] = "FAIL"
        elif status == "SKIP" and self.doc["status"] == "PASS":
            self.doc["status"] = "PASS"
        return result

    def find(self, *args, **kw):
        self.doc["findings"].append(finding(*args, **kw))

    def note(self, key: str, value) -> None:
        self.doc.setdefault("notes", {})[key] = value

    def write(self) -> str:
        p = os.path.join(self.out_dir, "summary.json")
        with open(p, "w") as fh:
            json.dump(self.doc, fh, indent=2)
        print("summary: %s -> %s" % (self.doc["status"], p))
        return p

    @property
    def failed(self) -> bool:
        return self.doc["status"] == "FAIL"
