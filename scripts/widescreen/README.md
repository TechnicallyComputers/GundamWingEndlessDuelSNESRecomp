# GWED widescreen harness

Automated evidence for the opt-in 16:9 feature (Beads `beads-8wg.9.13`).
Three programs, one shared measurement module:

| file | what it is |
|---|---|
| `ws_verdicts.py` | the five **verdict** checks (this document) — Beads `beads-8wg.9.13.7` |
| `ws_metrics.py`  | shared measurement library: process control, the WRAM gate, BMP metrics, seam detector, OAM decode, report writer |
| `ws_recon.py`, `recon_*.py` | the **recon** library and probes — Beads `beads-8wg.9.13.2`, a different agent's files |
| `p16_gate.py` | the P16 baseline-vs-candidate gate — Beads `beads-8wg.9.13.1` |

Recon answers *what does the game do*.  The verdicts answer *is the widescreen
presentation correct*, and every answer is a measurement with an evidence path.

---

## Run them

**Use the native Windows interpreter, and run from PowerShell.**

```powershell
$py = "C:\Users\Matthew\AppData\Local\Programs\Python\Python312\python.exe"
cd F:\Projects\snesrecomp\_wt-gwed-widescreen\scripts\widescreen
& $py ws_verdicts.py <check> [options]
```

Two environment traps, both of which the harness now detects rather than
mis-measuring:

* `python` on this machine resolves to `c:\devkitPro\msys2\usr\bin\python.exe`,
  which returns **POSIX paths** from `os.path.abspath`.  Every absolute path
  the harness hands the game (`screenshot`, `load_state`) would come out as
  `/f/Projects/...`, which the exe cannot open — and it would surface as
  "screenshot failed", i.e. as a fake widescreen defect.  `ws_metrics.
  require_windows_python()` refuses to run under it.
* Launched from the agent harness's **Bash** tool the game exe dies in the
  loader before printing anything (`0xC0000079` / `0xC0000135`).  The identical
  `subprocess.Popen` from PowerShell starts the game and opens the debug port.

### The five checks

```powershell
# (a) presentation-only: the wide centre window is the 4:3 frame, byte for byte
& $py ws_verdicts.py center-parity  --scenario boot_attract:900 --frames 60 --port 4481

# (b) every enabled background layer paints both margins, and no seam at x=43/299
& $py ws_verdicts.py margins        --scenario attract_fight --port 4484

# (c) the HUD rides the 16:9 edges  (needs --hud-json, else SKIP with the reason)
& $py ws_verdicts.py hud-anchor     --scenario attract_fight --hud-json hud.json --port 4485

# (d) text and menu screens pillarbox: uniform margins, centred budget, centre == 4:3
& $py ws_verdicts.py text-letterbox --scenario state:pre_title --frames 60 --port 4482

# (e) sprites in the margins both exist in OAM and draw pixels
& $py ws_verdicts.py sprite-nocull  --scenario attract_fight --port 4486
```

Each prints `PASS|FAIL|SKIP <check> <scenario>` lines and writes
`summary.json` (DKC2 `report.json` shape: `run_id`, `utc`, `git_rev`,
`exe_sha256`, `exe_mtime`, `env`, `scenario`, `checks`, `findings`, `status`).

**Exit status:** `0` every check passed (or skipped), `1` a check FAILED,
`2` harness error — the measurement did not happen, which is not a verdict
either way.

Evidence lands in `analysis/widescreen/verify/<utc>/<check>_<scenario>/`
(gitignored: BMPs, WRAM dumps and `.state` files are never committed).

Common options: `--build` (default `build-ws-trace2`), `--rom`, `--ws-extra`
(default 43), `--port`, `--out`, `--gate-json`, `--no-gate`, `--settle`,
`--load-at`, `--sample-gap`, and for the two pixel-parity checks
`--frames` / `--no-control`.

---

## Scenario names

| scenario | entry | notes |
|---|---|---|
| `boot_attract[:N]` | cold boot, no input, free-run to guest frame `N` (default 900) | fully deterministic; the only scenario that needs no savestate |
| `attract_fight` | cold boot, then free-run **polling the WRAM gate** until mode + liveness both say *live fight*, then settle 120 frames | the only live gameplay reachable without an owner-recorded state.  Its arrival frame varies run to run, so it is a *sampling* scenario: `margins` / `sprite-nocull` / `hud-anchor` use it, the frame-exact pixel-parity checks cannot |
| `state:<name>` | free-run to a **fixed guest frame** (`--load-at`, default 200), then `load_state`, then settle `--settle` frames | the fixed load frame is what makes two compared processes land at the same guest moment; loading "as soon as the port answers" would put them at two different ones |

`load_state` exists only in a `-DSNESRECOMP_ENABLE_TRACE=ON` build.

### Entry-state inventory

Already recorded, in `tools/validation_states/` of the **primary checkout**
(savestates are gitignored, so they do not exist in this worktree):

`pre_title`, `pre_quote`, `pre_stage_battle_dialogue_3`,
`pre_stage_ending_dialogue`, `pre_final_convo`, `pre_final_convo_2`,
`pre_ending`, `pre_ending_2`.

Still needed, and only a human can record them (the plan's Phase 2 list) —
raw `snes_saveload` dumps, so any new PPU field must stay **outside** the
`PPU_SAVESTATE_REGS_SIZE` span or every banked state dies:

`ws_fight_left_wall`, `ws_fight_right_wall`, `ws_fight_midscroll`,
`ws_projectile_at_left_edge`, `ws_projectile_at_right_edge`, `ws_ko`,
`ws_victory`, `ws_charselect`, `ws_vs_screen`, `ws_menu_options`.

Until they exist, `margins` / `sprite-nocull` / `hud-anchor` have exactly one
live-fight entry (`attract_fight`) and cannot be run at the camera walls.

---

## Port blocks

One port block per concurrent agent, because these processes are long-lived:

| block | owner |
|---|---|
| 4471–4479 | recon (`recon_*.py`) |
| **4481–4489** | **verdicts (`ws_verdicts.py`)** |
| 4491–4499 | P16 gate (`p16_gate.py`) |

`mods/preloaded/state.toml` is shared by every process launched from one build
directory, so the checks run their processes **sequentially**.  Two *checks*
must not share a build directory at the same time — give the second one its
own copy of the exe directory (`ws_metrics.stage_build`, or
`Copy-Item src\dir\* dst\dir\` — never `Copy-Item src\dir dst\dir`, which
nests).

---

## How the geometry is pinned

`SNESRECOMP_WS_EXTRA` is the authority, not the window:

* `SNESRECOMP_WS_EXTRA=0` forces the authentic 256-wide frame **even with the
  mod package enabled** (`src/gwed_display.c GwedDisplay_ComputeFrameWidth`).
* `SNESRECOMP_WS_EXTRA=43` pins the 16:9 margin — 342 = `ceil_even(256*4/3)`,
  which is 16:9 at the 7:6 CRT pixel aspect.

So the 4:3 and wide sides of a parity test differ by one environment variable
and nothing else.  In particular they do **not** need two different
`state.toml` files, which is what makes running both from one build directory
safe.  The harness still writes a `state.toml` with the widescreen package
enabled (`gwed.enhancement.widescreen` / feature `widescreen`) alongside the
localization block — the Mods package is the sole widescreen authority
(`beads-8wg.1.10`), there is no `config.ini` key.

Activation is confirmed by **measurement, not by the log line**: the exe's
captured `stderr.log` comes back **empty** on this build (the mingw link does
not give the process a usable stderr under the harness), so
`gwed_display.c`'s `[ws] widescreen on — frame 342x224 (margin 43/side)` never
reaches the file.  What the harness asserts instead is the geometry itself —
the captured frame is 342 px wide and `get_ppu_state.widescreen.budget` is 43,
neither of which can happen unless `GwedDisplay_ComputeFrameWidth` saw
`s_widescreen_enabled == true`, i.e. unless the package block took effect.
`capture_side` raises a harness error (never a verdict) if the widths are
wrong.

`SNESRECOMP_LAYER_MASK` (bit0..3 = BG1..BG4, bit4 = OBJ) is read **once** at
the first `PpuBeginDrawing`, so `margins` and `sprite-nocull` start one fresh
process per mask.

### Frames come from the env dumper, not from `screenshot`

`center-parity` and `text-letterbox` capture with
`SNESRECOMP_FRAME_BMP_DIR` + `_START` / `_STEP=1` / `_END`, because only that
dumper is frame-exact: it writes from inside the present path and names each
file after the guest frame it presented.  The debug server's `screenshot`
copies whatever was most recently presented and reads `snes_frame_counter`
*afterwards*, so its label can run ahead of its content — **measured on this
build: a poll at frame 917 produced a reply labelled 920**, which a byte-exact
comparison then reports as a one-pixel palette difference that is really a
three-frame offset.  `screenshot` is still the right tool for the sampling
checks (`margins`, `sprite-nocull`, `hud-anchor`), where the label only has
to identify a sample.

### The reproducibility control

The pixel-parity checks run a **third** process at `WS_EXTRA=0` and compare it
against the first.  Cross-process byte-exactness assumes the guest executes
reproducibly, and on this engine it does not always: the APU is paced by the
audio thread, so a scene that is still running can diverge between two runs of
the *same* configuration.  A parity mismatch counts as a widescreen defect
only when it starts **earlier** than the control's own divergence.  Measured:
`state:pre_quote` diverges 4:3-vs-4:3 at frame 0 — it is not a usable
centre-parity scenario at all, and the check reports SKIP rather than blaming
widescreen.  `--no-control` turns the control off (and then a mismatch is
reported as FAIL with reproducibility explicitly unproven).

---

## Scene identity: the WRAM gate, never pixels

Scene identity comes from a savestate entry plus a WRAM gate read with
`read_ram` — never from sampling the framebuffer.  Pixel diffs are *verdicts*.

`--gate-json` (default `analysis/widescreen/recon/gate.json`, produced by
`recon_gate.py`) supplies it.  The harness reads:

```json
{ "gate": {
    "mode":     { "addr": "$7E:1004", "width": 2, "value": "0x0012" },
    "liveness": { "addr": "$7E:0600", "rule": "increments every frame of a live round" } } }
```

* `mode` — a 16-bit word; `value` is the live-fight encoding.
* `liveness` — a counter sampled across 8 real guest frames.

Scene identity is the **conjunction** (`WIDESCREEN_PATTERNS` P5/P6): the
victory quote satisfies the coarse battle-family word on its own, which is
exactly the defect the liveness term exists to reject.  `addr` accepts
`$7E:1004`, `0x1004` or an integer offset.

Without a gate spec, pass `--no-gate`: the checks still run, the report is
stamped `gate_verified: false`, and every scene-identity claim reads
UNVERIFIED (`attract_fight` then parks on the recon-recorded anchor frame
3286 instead of polling).

---

## `--hud-json`

`hud-anchor` cannot guess which OAM slots or tile ids are HUD, nor which 16:9
edge each anchors to — that is an R5 recon result.  Without the file the check
reports SKIP with that reason rather than inventing a threshold.

```json
{
  "obj": [
    { "name": "p1_health", "slots": [0, 7],   "anchor": "left"   },
    { "name": "p2_health", "slots": [8, 15],  "anchor": "right"  },
    { "name": "timer",     "tiles": [64, 79], "anchor": "center" }
  ],
  "bg": [
    { "name": "hud_band", "layer": 2, "lines": [8, 40] }
  ]
}
```

* `obj[]` — select slots by `slots: [lo, hi]` (slot index range) **or**
  `tiles: [lo, hi]` (tile-id range).  `anchor` is `left` / `right` / `center`,
  which requires an X shift of exactly `-43` / `+43` / `0` with Y unchanged
  between the 4:3 and the wide capture.  OAM is read from `oam_render_get`,
  the render-consumed ring: GWED renders from a **latched** OAM snapshot
  (`src/game_rtl.c`), so the live `dump_oam` array is a frame ahead of what
  was drawn.
* `bg[]` — `layer` is a `ppu_window` layer index (0..5); `lines` is the
  inclusive scanline band, sampled every `--window-step` lines.  Its window
  edges must expand past the native 256 columns, and
  `get_ppu_state.widescreen.budget` must equal `--ws-extra`.

## `--expect-margin-object`

`sprite-nocull` has two stages, and only the second can fail without a
declaration:

1. `object_render_or_cull` — does the OAM entry **exist**?  Without
   `--expect-margin-object` an empty margin is indistinguishable from a scene
   with nothing in the margin, so the stage reports SKIP and records the
   observed margin entries and the signed-X histogram.  The file is a JSON
   list of objects that must be present:
   `[{"margin": "left", "tile": 320}, {"margin": "right", "slot": 12}]`
   (any subset of `slot` / `tile` / `margin` may be given).
2. `object_ppu_or_presenter` — does it **draw**?  Every margin OAM entry with
   an on-screen Y must paint at least one non-backdrop pixel inside its
   OBSEL-derived rect in the OBJ-isolated capture.

Signed X is reported under **both** readings, and neither is assumed:
a 9-bit OAM X is 0..511 and the SNES treats `>= 256` as `x - 512` (that is
how a sprite walks off the left edge), while a widescreen host that publishes
`PpuWsSetOamLeftHints/RightHints` asks the PPU to read raw X in
`[256, 256+extra)` as a sprite in the **right** margin.  The two disagree for
exactly that window, and entries in it are flagged `x_reading_ambiguous`.

---

## Thresholds (all ported from the DKC2 harness)

| name | value | meaning |
|---|---|---|
| `MARGIN_SHARE` | 0.25 | a margin counts as painted when its non-backdrop fraction reaches 25% of what the native window itself achieves |
| `LAYER_PRESENT_MIN` | 0.05 | layers below 5% non-backdrop in the native window are skipped: they draw nothing here, so their margins carry no information |
| `SEAM_RATIO` / `SEAM_EXCESS` | 3.0 / 18.0 | `edge_score` at x=43 and x=299 vs its ten neighbouring columns |

Blankness is measured against the **dominant backdrop colour**, not against
black: layer-isolated SNES frames keep the fixed/backdrop colour, so
measuring only black would call an empty OBJ frame completely full.

A seam hit is only promoted to a defect when it survives an **adjacent
sample with camera motion** — an authored vertical edge (a wall, a mast) can
land on the old 4:3 boundary for one frame, whereas a stale-margin seam stays
pinned to that screen coordinate while the camera moves.  Camera motion is
read from `get_ppu_state.hScroll` deltas (the PPU's own per-layer scroll,
`WIDESCREEN_PATTERNS` P1 authority) — never inferred from pixels.

---

## Baseline results at `e93e1d5` (the "before" record)

`e93e1d5` pillarboxes **every** screen: `GwedDisplay_ResolveScreen()` answers
`Bounded` unconditionally, so `PpuSetExtraSpaceCentered(43)` +
`PpuSetWidescreenLayerClamp(0x0F)` apply everywhere and the margin columns are
memset to black.  Evidence: `analysis/widescreen/verify/20260902T051500Z/`.

| check | scenario | result |
|---|---|---|
| `center-parity` | `boot_attract:900` | **PASS** — 60/60 frames, 13440/13440 rows byte-exact |
| `text-letterbox` | `boot_attract:900` | **PASS** — all 5 sub-checks |
| `text-letterbox` | `state:pre_title` | **PASS** — all 5 sub-checks |
| `text-letterbox` | `state:pre_quote` | uniformity / budget / gate **PASS**; `center_matches_native` **SKIP** (control diverges at frame 0 — see below) |
| `margins` | `attract_fight` | `margin_non_blank` **FAIL** `background_load_or_render` on all 6 masks — the correct, informative failure; `native_boundary_seam` **PASS** (the boundary edges are the pillarbox itself, so they are reported as a consequence of the empty margin rather than as a second, independent defect) |
| `sprite-nocull` | `attract_fight` | **SKIP/SKIP** — 512 sampled OAM slots, every X in `[0, 253]`: nothing is emitted outside the native range at this scene today |
| `hud-anchor` | `attract_fight` | **SKIP** — no `--hud-json` yet (R5 pending) |

Three things this baseline settled that the plan did not anticipate:

1. **`text-letterbox` is the right check for `pre_quote` and the attract fight
   only while every screen is Bounded.**  Once the per-screen policy table
   lands (`beads-8wg.9.13.5`) and the live-fight branch switches to
   `PpuSetExtraSpace` + world layers, the attract fight and the victory quote
   must be judged by **`margins`** instead — `text-letterbox` would then
   correctly FAIL them, and that FAIL would be a bug in the *check selection*,
   not in the feature.  `docs/WIDESCREEN.md` has to state which scenario each
   check owns after the policy table exists.
2. **`state:pre_quote` yields no centre-parity evidence.**  Two identically
   configured 4:3 runs diverge at frame 0 of the compared window, and the WRAM
   gate itself read `0x0014 / live=true` on one run and `0x0000 / live=false`
   on another: `pre_quote` was banked with the PAR freeze already applied, so
   the round ends on its own and the timeline is not reproducible across
   processes.  The frame-exact checks need a genuinely frozen entry state.
3. **The pillarbox margins are opaque black, not CGRAM 0.**  `gwed_display.c`'s
   Bounded branch memsets them so a later switch out of World mode cannot
   leave a stale world frame frozen in the margins.  At `state:pre_quote`
   CGRAM 0 is `#000083`, so a check written as "margins == backdrop" would
   FAIL a correct implementation.  `margins_are_backdrop_or_cleared` therefore
   accepts either, reports which one it measured, and fails only on a third
   colour; `margins_uniform` (the assertion that actually implies "no
   stretching, no slicing") is a separate, unconditional check.

## Discipline

* Nothing arms a trace.  The rings are always on; probes query them.
* `run_to_frame` is **never** used: it *pauses* the guest on arrival.  The
  harness polls `frame` and free-runs.  Nothing pauses, steps or breaks.
* `clear_controller` runs in every finally path (`set_controller` latches),
  even though these checks inject no input.
* `tools/validation_states/tcp.py` is imported, never edited; its module-level
  `EXE` / `ROM` constants are rebound after import.
