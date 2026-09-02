#!/usr/bin/env python3
"""P16 gate: prove a widescreen candidate build is bit-identical at 4:3.

WIDESCREEN_PATTERNS.md P16 is the release gate for every opt-in widescreen
feature: with the feature off (SNESRECOMP_WS_EXTRA=0, package disabled), the
candidate build must execute the SAME GUEST FRAMES and present the SAME
PIXELS as the baseline it forked from. This script is the mechanised form of
that claim, and it is meant to be run on the SHIPPING config (build-ws-release,
trace OFF) — a gate that only holds in a debug build gates nothing.

Three checks, in descending order of authority:

  wram_crc_identical    the load-bearing one. Per-frame crc32 of the guest's
                        128 KB WRAM, from the --framedump JSON sidecars. It is
                        resolution-independent, so it stays meaningful when the
                        candidate renders 342 px wide, and it catches guest-side
                        divergence (a widescreen patch that leaked into WRAM)
                        that pixels can miss.
  frame_pixels_identical  BMP frames from runner/src/widescreen.c's
                        SNESRECOMP_FRAME_BMP_DIR dumper. Equal widths compare
                        byte-for-byte; a wider candidate must match inside the
                        centred native 256 columns AND show one uniform colour
                        in each margin band.
  no_new_runtime_events   stderr must not gain stub traps, watchdog trips,
                        aborts or assertions relative to the baseline.

Boot timing is not wall-clock-stable across two processes, so the WRAM series
are aligned by an integer frame-offset search (+-120 frames). Identical but
shifted is a PASS with the offset recorded — the assertion is "same execution",
not "same start-up latency".

Scenarios
---------
  boot_attract    cold boot, no input, straight into the attract reel. Fully
                  supported on the release build: --framedump + --exit-at-frame
                  make the run frame-exact with no debug server involved.
  state:<name>    load tools/validation_states/<name>.state before free-running.
                  Loading a savestate goes through the debug server's
                  `load_state <filename>` command, which exists only in a
                  -DSNESRECOMP_ENABLE_TRACE=ON build. Pass --trace to enable
                  these, and point --baseline-exe/--candidate-exe at trace
                  builds; on a release build the script refuses rather than
                  silently measuring the wrong thing (a savestate that never
                  loaded still boots to the attract reel and would PASS).

Exit status: 0 all checks passed, 1 a check failed, 2 harness error.
"""

import argparse
import glob
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time

# state.toml written into each exe's own mods/preloaded so the two runs do not
# fight over one file (it is per-build-dir shared state; see
# tools/validation_states/tcp.py write_state).
STATE_TOML = """format_version = 1

[[package]]
id = "gwed.localization"
version = "1.0.0"

[[feature]]
package_id = "gwed.localization"
id = "localization"
enabled = true

[feature.values]
language = "en"
"""

# stderr lines that mean the runtime hit something it should not have.
EVENT_PATTERNS = [
    r"UNRESOLVED-STUB",
    r"\bTRAP\b",
    r"watchdog",
    r"\babort\b",
    r"assertion",
    r"Assertion",
    r"FAILED",
]

MAX_OFFSET = 120          # frames of boot-timing skew tolerated by alignment
MAX_DIFF_FRAMES = 10      # how many differing frames get copied into out/diff


class HarnessError(Exception):
    pass


# ── running a side ───────────────────────────────────────────────────────────

def write_state_toml(workdir):
    p = os.path.join(workdir, "mods", "preloaded", "state.toml")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    for _ in range(20):
        try:
            with open(p, "w", newline="\n") as f:
                f.write(STATE_TOML)
            return
        except OSError:
            time.sleep(0.5)
    raise HarnessError("state.toml contended: %s" % p)


def run_side(exe, rom, scenario, frames, outdir, bmp_step,
             no_framedump=False, trace=False, port=4491, state_dir=None):
    """One fresh process. Returns a dict describing where its evidence landed."""
    exe = os.path.abspath(exe)
    workdir = os.path.dirname(exe)
    bmp_dir = os.path.join(outdir, "bmp")
    fd_dir = os.path.join(outdir, "framedump")
    os.makedirs(bmp_dir, exist_ok=True)
    os.makedirs(fd_dir, exist_ok=True)
    log_path = os.path.join(outdir, "stderr.log")

    write_state_toml(workdir)

    env = dict(os.environ)
    env["SNESRECOMP_WS_EXTRA"] = "0"          # P16: feature off on both sides
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SDL_AUDIODRIVER"] = "dummy"
    env["SNESRECOMP_FRAME_BMP_DIR"] = bmp_dir
    env["SNESRECOMP_FRAME_BMP_START"] = "0"
    env["SNESRECOMP_FRAME_BMP_STEP"] = str(bmp_step)
    env["SNESRECOMP_FRAME_BMP_END"] = str(frames)
    env.pop("SNESRECOMP_FRAME_BMP", None)     # single-file mode would fight the dir mode
    if not no_framedump:
        env["SNESRECOMP_FRAMEDUMP_START"] = "0"
        env["SNESRECOMP_FRAMEDUMP_END"] = str(frames)
    else:
        env.pop("SNESRECOMP_FRAMEDUMP_START", None)
        env.pop("SNESRECOMP_FRAMEDUMP_END", None)

    argv = [exe, "--no-launcher", rom]
    if not no_framedump:
        argv += ["--framedump", fd_dir, "--exit-at-frame", str(frames)]

    state_file = None
    if scenario.startswith("state:"):
        if not trace:
            raise HarnessError(
                "scenario %r needs the debug server (load_state), which exists "
                "only in a -DSNESRECOMP_ENABLE_TRACE=ON build: pass --trace and "
                "trace-build exes" % scenario)
        name = scenario.split(":", 1)[1]
        root = state_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "tools", "validation_states")
        state_file = os.path.abspath(os.path.join(root, name + ".state"))
        if not os.path.isfile(state_file):
            raise HarnessError("no such savestate: %s" % state_file)
        env["SNESRECOMP_DEBUG_PORT"] = str(port)
    elif scenario != "boot_attract":
        raise HarnessError("unknown scenario %r" % scenario)

    log = open(log_path, "wb")
    t0 = time.time()
    proc = subprocess.Popen(argv, cwd=workdir, env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    try:
        if state_file:
            _load_state_over_tcp(port, state_file)
        if no_framedump:
            # A build without --exit-at-frame cannot be stopped on a frame, so
            # this side gets a wall clock: 60.0988 Hz plus start-up slack. Its
            # WRAM series is unusable (no framedump), which is exactly why this
            # mode only ever backs the pixel check.
            deadline = t0 + frames / 60.0 + 12.0
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.25)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            rc = -1  # killed on purpose; not a verdict
        else:
            rc = proc.wait(timeout=frames / 60.0 + 180.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise HarnessError("run did not reach frame %d: %s" % (frames, exe))
    finally:
        log.close()

    return {
        "exe": exe,
        "argv": argv,
        "returncode": rc,
        "wall_seconds": round(time.time() - t0, 2),
        "bmp_dir": bmp_dir,
        "framedump_dir": fd_dir if not no_framedump else None,
        "log": log_path,
    }


def _load_state_over_tcp(port, state_file):
    deadline = time.time() + 30
    last = None
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            s.settimeout(30)
            f = s.makefile("rwb")
            f.write(("load_state %s\n" % state_file.replace("\\", "/")).encode())
            f.flush()
            reply = f.readline().decode("utf-8", "replace").strip()
            s.close()
            if '"error"' in reply:
                raise HarnessError("load_state refused: %s" % reply)
            return reply
        except OSError as e:
            last = e
            time.sleep(0.3)
    raise HarnessError("debug server never answered on port %d (%s)" % (port, last))


# ── evidence readers ─────────────────────────────────────────────────────────

def read_crc_series(fd_dir):
    """{frame: crc32_wram} from the framedump JSON sidecars."""
    if not fd_dir:
        return {}
    out = {}
    for p in glob.glob(os.path.join(fd_dir, "frame_*.json")):
        try:
            with open(p) as f:
                d = json.load(f)
            out[int(d["frame"])] = str(d["crc32_wram"]).lower()
        except (OSError, ValueError, KeyError):
            continue
    return out


def read_bmp(path):
    """(width, height, rows_top_down_bytes) for widescreen.c's 32bpp dumps."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 54 or data[:2] != b"BM":
        raise HarnessError("not a BMP: %s" % path)
    off = struct.unpack_from("<I", data, 10)[0]
    w = struct.unpack_from("<i", data, 18)[0]
    h = struct.unpack_from("<i", data, 22)[0]
    bpp = struct.unpack_from("<H", data, 28)[0]
    if bpp != 32:
        raise HarnessError("expected 32bpp, got %d: %s" % (bpp, path))
    height = abs(h)
    px = data[off:off + w * height * 4]
    if len(px) < w * height * 4:
        raise HarnessError("truncated BMP: %s" % path)
    stride = w * 4
    rows = [px[y * stride:(y + 1) * stride] for y in range(height)]
    if h > 0:                     # bottom-up: widescreen.c writes -h, but honour both
        rows.reverse()
    return w, height, rows


def bmp_frames(bmp_dir):
    out = {}
    for p in glob.glob(os.path.join(bmp_dir, "frame_*.bmp")):
        m = re.search(r"frame_(\d+)\.bmp$", os.path.basename(p))
        if m:
            out[int(m.group(1))] = p
    return out


# ── checks ───────────────────────────────────────────────────────────────────

def check_wram_crc(base, cand):
    """PASS / PASS(offset) / FAIL / SKIP for the per-frame WRAM digest series."""
    b = read_crc_series(base["framedump_dir"])
    c = read_crc_series(cand["framedump_dir"])
    if not b or not c:
        return {"status": "SKIP",
                "detail": "one side produced no framedump (baseline built "
                          "without --framedump support?)",
                "baseline_frames": len(b), "candidate_frames": len(c)}

    def compare(offset):
        """Candidate frame f vs baseline frame f+offset. -> (n, first_bad)"""
        n = 0
        first_bad = None
        for f in sorted(c):
            bf = f + offset
            if bf not in b:
                continue
            n += 1
            if b[bf] != c[f]:
                if first_bad is None:
                    first_bad = (f, bf, c[f], b[bf])
        return n, first_bad

    need = max(30, int(0.5 * min(len(b), len(c))))
    zero_n, zero_bad = compare(0)
    if zero_n >= need and zero_bad is None:
        return {"status": "PASS", "offset": 0, "compared": zero_n}

    for k in range(1, MAX_OFFSET + 1):
        for off in (k, -k):
            n, bad = compare(off)
            if n >= need and bad is None:
                return {"status": "PASS", "offset": off, "compared": n,
                        "detail": "identical but shifted by %d frames "
                                  "(boot-timing skew, not divergence)" % off}

    detail = "no frame offset in [-%d,%d] aligns the WRAM digest series" % (
        MAX_OFFSET, MAX_OFFSET)
    out = {"status": "FAIL", "offset": None, "compared": zero_n,
           "detail": detail}
    if zero_bad:
        f, bf, cc, bc = zero_bad
        out["first_divergent_frame"] = f
        out["first_divergent"] = {"candidate_frame": f, "baseline_frame": bf,
                                  "candidate_crc": cc, "baseline_crc": bc}
    return out


def uniform_band(rows, x0, x1):
    """Is columns [x0,x1) one colour across every row? -> (bool, colour)"""
    if x1 <= x0:
        return True, None
    first = rows[0][x0 * 4:(x0 + 1) * 4]
    for r in rows:
        seg = r[x0 * 4:x1 * 4]
        for i in range(0, len(seg), 4):
            if seg[i:i + 4] != first:
                return False, None
    return True, "#%02X%02X%02X" % (first[2], first[1], first[0])


def check_pixels(base, cand, offset, diff_dir):
    b = bmp_frames(base["bmp_dir"])
    c = bmp_frames(cand["bmp_dir"])
    if not b or not c:
        return {"status": "FAIL", "detail": "no BMP frames captured",
                "baseline_frames": len(b), "candidate_frames": len(c)}

    # Frames only exist on the dump step, so a non-zero WRAM offset usually
    # leaves nothing paired up; fall back to same-numbered frames in that case.
    def pairs(off):
        return [(f, f + off) for f in sorted(c) if (f + off) in b]

    use = offset or 0
    p = pairs(use)
    if len(p) < max(2, len(c) // 2):
        p0 = pairs(0)
        if len(p0) > len(p):
            use, p = 0, p0
    if not p:
        return {"status": "FAIL", "detail": "no comparable frame pairs",
                "baseline_frames": len(b), "candidate_frames": len(c)}

    mismatches = []
    margins = []
    widths = set()
    os.makedirs(diff_dir, exist_ok=True)
    for cf, bf in p:
        bw, bh, brows = read_bmp(b[bf])
        cw, ch, crows = read_bmp(c[cf])
        widths.add((bw, cw))
        bad = None
        if ch != bh:
            bad = "height %d != %d" % (ch, bh)
        elif cw == bw:
            for y in range(bh):
                if crows[y] != brows[y]:
                    bad = "row %d differs" % y
                    break
        elif cw > bw:
            # Wider candidate: the native columns must match exactly and each
            # margin band must be a single flat colour (no sliced art).
            m = (cw - bw) // 2
            if (cw - bw) % 2:
                bad = "odd margin: %d vs %d" % (cw, bw)
            else:
                for y in range(bh):
                    if crows[y][m * 4:(m + bw) * 4] != brows[y]:
                        bad = "native window row %d differs" % y
                        break
                if not bad:
                    lu, lc = uniform_band(crows, 0, m)
                    ru, rc = uniform_band(crows, m + bw, cw)
                    margins.append({"frame": cf, "left_uniform": lu,
                                    "left_colour": lc, "right_uniform": ru,
                                    "right_colour": rc})
                    if not lu or not ru:
                        bad = "margin band is not one uniform colour"
        else:
            bad = "candidate narrower than baseline (%d < %d)" % (cw, bw)

        if bad:
            if len(mismatches) < MAX_DIFF_FRAMES:
                shutil.copyfile(b[bf],
                                os.path.join(diff_dir, "frame_%06d_baseline.bmp" % cf))
                shutil.copyfile(c[cf],
                                os.path.join(diff_dir, "frame_%06d_candidate.bmp" % cf))
                _write_side_by_side(diff_dir, cf, (bw, bh, brows), (cw, ch, crows))
            mismatches.append({"candidate_frame": cf, "baseline_frame": bf,
                               "reason": bad})

    res = {"status": "FAIL" if mismatches else "PASS",
           "offset": use,
           "compared": len(p),
           "widths": sorted("baseline=%d candidate=%d" % wc for wc in widths),
           "mismatch_count": len(mismatches)}
    if mismatches:
        res["first_mismatch"] = mismatches[0]
        res["mismatches"] = mismatches[:MAX_DIFF_FRAMES]
        res["diff_dir"] = diff_dir
    if margins:
        res["margins"] = margins[:MAX_DIFF_FRAMES]
    return res


def _write_side_by_side(diff_dir, frame, base_img, cand_img):
    """baseline | candidate in one image. PNG when PIL is around, else BMP."""
    bw, bh, brows = base_img
    cw, ch, crows = cand_img
    w, h = bw + cw, max(bh, ch)
    rows = []
    for y in range(h):
        left = brows[y] if y < bh else b"\x00" * (bw * 4)
        right = crows[y] if y < ch else b"\x00" * (cw * 4)
        rows.append(left + right)
    try:
        from PIL import Image
        img = Image.frombytes("RGBA", (w, h), b"".join(rows))
        b_, g_, r_, _a = img.split()
        Image.merge("RGB", (r_, g_, b_)).save(
            os.path.join(diff_dir, "frame_%06d_sbs.png" % frame))
        return
    except Exception:
        pass
    _write_bmp(os.path.join(diff_dir, "frame_%06d_sbs.bmp" % frame), w, h, rows)


def _write_bmp(path, w, h, rows_top_down):
    img = w * h * 4
    hdr = bytearray(54)
    hdr[0:2] = b"BM"
    struct.pack_into("<I", hdr, 2, 54 + img)
    struct.pack_into("<I", hdr, 10, 54)
    struct.pack_into("<I", hdr, 14, 40)
    struct.pack_into("<i", hdr, 18, w)
    struct.pack_into("<i", hdr, 22, -h)
    struct.pack_into("<H", hdr, 26, 1)
    struct.pack_into("<H", hdr, 28, 32)
    struct.pack_into("<I", hdr, 34, img)
    with open(path, "wb") as f:
        f.write(hdr)
        for r in rows_top_down:
            f.write(r)


def scan_events(log_path):
    hits = {}
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                for pat in EVENT_PATTERNS:
                    if re.search(pat, line):
                        hits.setdefault(pat, []).append((i, line.rstrip()))
    except OSError:
        pass
    return hits


def check_events(base, cand):
    b = scan_events(base["log"])
    c = scan_events(cand["log"])
    new = {}
    for pat, lines in c.items():
        if len(lines) > len(b.get(pat, [])):
            new[pat] = [ln for _n, ln in lines[:5]]
    res = {"status": "FAIL" if new else "PASS",
           "baseline_hits": {k: len(v) for k, v in b.items()},
           "candidate_hits": {k: len(v) for k, v in c.items()},
           "baseline_log": base["log"], "candidate_log": cand["log"]}
    if new:
        res["new_events"] = new
    # A non-zero exit is itself a runtime event worth failing on (the
    # wall-clock-killed no-framedump baseline reports -1 by design).
    if cand["returncode"] not in (0,):
        res["status"] = "FAIL"
        res.setdefault("new_events", {})["exit_status"] = [
            "candidate exited %s" % cand["returncode"]]
    return res


# ── driver ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline-exe", required=True)
    ap.add_argument("--candidate-exe", required=True)
    ap.add_argument("--rom", required=True)
    ap.add_argument("--scenarios", default="boot_attract",
                    help="comma list: boot_attract, state:<name> (needs --trace)")
    ap.add_argument("--frames", type=int, default=3600,
                    help="guest frames per run (3600 = 60 s)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--bmp-step", type=int, default=30)
    ap.add_argument("--trace", action="store_true",
                    help="exes are trace builds; enables state: scenarios")
    ap.add_argument("--port", type=int, default=4491,
                    help="debug-server port base for state: scenarios")
    ap.add_argument("--state-dir", default=None,
                    help="override tools/validation_states")
    ap.add_argument("--baseline-no-framedump", action="store_true",
                    help="baseline predates the harness flags: run it with the "
                         "FRAME_BMP env only and a wall clock, and report "
                         "wram_crc_identical as SKIP")
    args = ap.parse_args()

    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    rom = os.path.abspath(args.rom)
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]

    verdict = {
        "baseline_exe": os.path.abspath(args.baseline_exe),
        "candidate_exe": os.path.abspath(args.candidate_exe),
        "rom": rom,
        "frames": args.frames,
        "bmp_step": args.bmp_step,
        "baseline_no_framedump": bool(args.baseline_no_framedump),
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scenarios": {},
    }
    failed = False
    try:
        for si, scenario in enumerate(scenarios):
            sdir = os.path.join(out_root, scenario.replace(":", "_"))
            base = run_side(args.baseline_exe, rom, scenario, args.frames,
                            os.path.join(sdir, "baseline"), args.bmp_step,
                            no_framedump=args.baseline_no_framedump,
                            trace=args.trace, port=args.port + 2 * si,
                            state_dir=args.state_dir)
            cand = run_side(args.candidate_exe, rom, scenario, args.frames,
                            os.path.join(sdir, "candidate"), args.bmp_step,
                            trace=args.trace, port=args.port + 2 * si + 1,
                            state_dir=args.state_dir)

            wram = check_wram_crc(base, cand)
            pixels = check_pixels(base, cand, wram.get("offset") or 0,
                                  os.path.join(sdir, "diff"))
            events = check_events(base, cand)
            verdict["scenarios"][scenario] = {
                "baseline_run": base, "candidate_run": cand,
                "checks": {"wram_crc_identical": wram,
                           "frame_pixels_identical": pixels,
                           "no_new_runtime_events": events},
            }
            for name, res in (("wram_crc_identical", wram),
                              ("frame_pixels_identical", pixels),
                              ("no_new_runtime_events", events)):
                extra = ""
                if res.get("offset"):
                    extra = " (offset=%d)" % res["offset"]
                print("%s %s %s%s" % (res["status"], name, scenario, extra))
                if res["status"] == "FAIL":
                    failed = True
                    if res.get("detail"):
                        print("      %s" % res["detail"])
                    if res.get("first_mismatch"):
                        print("      first mismatch: %s" % res["first_mismatch"])
                    if res.get("new_events"):
                        print("      new events: %s" % res["new_events"])
    except HarnessError as e:
        verdict["harness_error"] = str(e)
        _write_verdict(out_root, verdict)
        print("HARNESS-ERROR %s" % e, file=sys.stderr)
        return 2

    verdict["result"] = "FAIL" if failed else "PASS"
    path = _write_verdict(out_root, verdict)
    print("verdict: %s -> %s" % (verdict["result"], path))
    return 1 if failed else 0


def _write_verdict(out_root, verdict):
    path = os.path.join(out_root, "verdict.json")
    with open(path, "w") as f:
        json.dump(verdict, f, indent=2)
    return path


if __name__ == "__main__":
    sys.exit(main())
