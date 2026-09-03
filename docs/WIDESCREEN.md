# 16:9 widescreen — Gundam Wing: Endless Duel

Opt-in 342x224 presentation (43 px of margin per side) for
`GundamWingEndlessDuelSNESRecomp`. Beads `beads-8wg.9.13` (+ children `.1`-`.7`);
the two engine primitives it required are `beads-8wg.2.25`.

Read `snesrecomp/docs/WIDESCREEN_PATTERNS.md` first. It is engine doctrine:
sixteen invariants P1-P16 (plus P2b and P2c, both added by this port), each a
defect some title actually hit, the invariant that prevents it, and how to
measure that you have it. The doctrine's bar is *"a port claiming widescreen
support should record which of P1-P16 it has verified, and by what
measurement"*, which is what the table below is for.

## Status

Implemented and measured: presentation plumbing, a WRAM-gated per-screen policy
table, BG1 world-mirror margins, elastic HUD anchoring, and a guarded
sprite-bounds ROM patch with netplay caps. The shipped default is authentic
256x224. Not yet verified: the camera walls in a live process, the human-only
screens, and a two-peer netplay run — see [Known gaps](#known-gaps-and-caveats).

## How a player turns it on

The launcher's **Mods** page, package `gwed.enhancement.widescreen` 1.0.0,
feature `widescreen` (group *Display*, `default_enabled = false`).
Manifest: `mods/preloaded/packages/gwed.enhancement.widescreen/1.0.0/manifest.toml`;
activation glue: `src/widescreen_mod.c`.

There is **no `config.ini` Widescreen key** (Beads `beads-8wg.1.10`). The mod
runtime's `<exe_dir>/mods/preloaded/state.toml` is the sole persistence, and
`gi.widescreen_supported = 0` so recomp-ui's shared SNES profile does not draw
its legacy 16:9 Display toggle beside the package row.

Window geometry changed for **both** aspects, because `game_present` now
computes an aspect-correct destination rect (`SnesDisplayAspect_ComputeViewport`)
instead of passing `NULL` and letting the host stretch the frame over whatever
shape the window had been dragged into:

| session | frame | window |
|---|---|---|
| 4:3 (default) | 256x224 | **896x672** — a true 4:3 field (was 768x672, i.e. square pixels presented as if they were 4:3) |
| 16:9 (package on) | **342x224** | **1197x672** |

342 = `ceil_even(256 * 4/3)`, which is 16:9 at the CRT 7:6 pixel aspect.
`config.ini [Video] Aspect / IgnoreAspect / IntegerScale` remain the escape
hatches; recomp-ui draws no aspect control for SNES.

## Architecture

`src/gwed_display.{c,h}` owns the policy; `src/gwed_ws_patch.{c,h}` owns the one
guest-side patch; `src/main.c` owns the plumbing.

### Session-pinned width

`GwedDisplay_BeginSession` pins the frame width once, immediately after
`snes_mod_runtime_activate_plugins_c()` and before the window, the texture and
the first frame; `main.c`'s `session_reboot` path re-pins on a rematch. That is
the only moment the width may change, because it is baked into the framebuffer
pitch, the SDL texture, netplay agreement and every P16 capture. `GAME_WIDTH`
stays the authentic 256; `GAME_MAX_WIDTH = 256 + 2*kWsExtraMax` (446) sizes
`g_render_pixels` and the frame-blend buffer.

`GwedDisplay_PreparePpuFrame` re-applies the whole policy **every frame**, from
`RtlDrawPpuFrame` before `draw_ppu_frame()`. This is mandatory, not defensive:
`ppu_reset()` memsets the entire `Ppu` struct and restores only
`renderBuffer`/`renderPitch`/`renderFlags`, so a reset or a savestate load would
otherwise drop the policy silently. It is also the only moment the frame's PPU
policy *can* be set — GWED composites the whole frame from its latched
OAM/CGRAM snapshot in that one call.

With widescreen off the function issues `PpuBeginDrawing(256)` and touches no
widescreen state at all. That is P16 by construction.

### The screen resolver — WRAM, never pixels

`GwedDisplay_ResolveScreen()` (`src/gwed_display.c`):

```
$7E:1000 == 0x0010                        battle family
$7E:1004 in {0x0012, 0x0014, 0x001E}      live fight / round intro, victory quote, round end
BG1 $2107 == 0x6B  and  BG1 char base 0   the arena tilemap is actually loaded
   -> World;  anything else -> Bounded
```

No framebuffer sampling anywhere. The state words come from recon pass A
(`analysis/widescreen/recon/gate.json`): `$7E:1000` is a stride-4 array of
16-bit state words, `$7E:1500` its pending copy, and the observed
`(1000,1004)` pairs are `(0x02,0x0A)` intro crawl, `(0x08,0x00)` cinematic,
`(0x0A,0x0C)` title+mode menu, `(0x10,0x12)` live fight, `(0x10,0x14)` victory
quote, `(0x10,0x1E)` round end / inter-stage dialogue / ending. Writers are
ROM-confirmed in bank `$01` ~`$B550-$B630`.

**Amended 2026-09-03 — the mode words do not classify attract fights.** The
reading above came from one attract fight and the player-fight savestates. With
the gate's inputs logged across a whole inputless attract cycle, every attract
demo has the arena map loaded (`$2107 == 0x6B`, char base 0) but reads a
different word pair, and `$7E:1004 == $7E:1000 + 2` on all of them:

| attract fight | `$7E:1000` | `$7E:1004` |
|---|---|---|
| city (the recon sample) | `0010` | `0012` |
| purple sky | `0016` | `0018` |
| dark arena | `0012` | `0014` |
| purple sky 2 | `0014` | `0016` |

For attract demos `$1000` is a demo *index* stepping by two, not a family, and a
gate keyed on `0x0010/0x0012` refused every demo but the first — the field
report was exactly that. The gate is now the BG1 precondition **alone**: it is
the leg this section already calls load-bearing, it is the leg the whole-cycle
sweep proved unique to arena screens, and it agreed on every stage. The words
are still read, for the `[ws] screen=` log line, where they say what a refused
screen was doing. Each newly admitted stage was checked with the P2b seam
detector on BG1-isolated wide captures before this shipped (see the commit).

The **BG1 register precondition is load-bearing**, and was not in the plan.
`0x001E` is shared by the KO / round-end screen, which *is* the arena, and by
the inter-stage dialogue and ending screens, which are not; recon captured
those with `$2107 == 0x69` and BG1 pointed elsewhere (the ending disables BG1
entirely), so the sub-mode word alone cannot classify. The check is therefore a
*precondition on the world bounds* — if BG1 is not the arena map then
reflecting about the arena's edges is meaningless — and it fails closed to
Bounded. Every non-arena screen recon captured reads `0x69`; only
`attract_fight`, `victory_quote` and `ko_1p_win` read `0x6B`.

The victory quote (`0x0014`) is **not** a full-screen portrait, contrary to the
issue sketch: recon shows the arena BG1 at hScroll 118 with the fight HUD
present, and the portrait panel is a re-pointed BG2 (`bgXsc[1] 0x79`, 64x32,
vScroll 1016). So it takes the World policy with BG2 clamped. Pillarboxing it
would snap the frame 342 -> 256 for the length of every victory quote.

The gate deliberately **omits** the P6 liveness signal `$7E:0600`. The sub-mode
reads live-fight for ~76 frames of stage load / round intro before the counter
starts moving, and 76 frames of pillarbox at the start of every round is a worse
artefact than anything liveness protects against. Liveness matters when a gate
drives behaviour that could change the simulation; nothing in this file does.

### Per-screen policy table

| screen (gate result) | policy |
|---|---|
| **World** — live fight / round intro, victory quote, KO / round end (all with the arena BG1 loaded) | `PpuSetExtraSpace(43)`, layers extend, no margin clear |
| **Bounded** — everything else: title logo, mode menu, attract crawl, attract cinematic, inter-stage dialogue, final conversation, ending, black transitions | `PpuSetExtraSpaceCentered(43)` + `PpuSetWidescreenLayerClamp(0x0F)` + an explicit margin clear |

Order matters: `PpuSetExtraSpace`/`Centered` both call `PpuResetLayerPolicies()`,
so every layer policy must be issued **after** them, never before.

A pillarbox cannot stretch or slice text, so Bounded is the safe default and
guessing wrong there costs view, not correctness.

### World layer policy

| layer | role | policy |
|---|---|---|
| BG1 | 64x64 map byte `$D000`, char `$0000` — the near stage plane and ground (lines 72-224, camera-scrolled) **plus** the raster HUD band (lines 22-71) | `PpuSetWidescreenLayerWorldMirrorBand` over the whole picture `[0,225)` with the world **`[64,448)`**, plus a clamp band over `[22,73)` for the HUD split. Clamp wins per line inside the PPU, so one full-height world band plus that clamp is exactly the intended split and also covers the arena strip at lines 0..21 |
| BG2 | 32x64 map `$F000` — far skyline/dome, hScroll pinned 0 | **no policy at all.** The map is 256 px wide, so the hardware's own wrap tiles it into the margins, and it is *seamless*: map column 31 is the X-flipped twin of column 0 for every visible row, so `edge_score` at both x=43 and x=299 is 0.000 with opacity agreement 1.0. `SNESRECOMP_WS_BG2_MODE=wrap\|mirror\|clamp` exposes the alternatives for A/B. Any *other* BG2 layout on an arena screen (the victory quote's portrait panel) is clamped — its off-screen columns are not known to be authored |
| BG3 | 64x64 map `$C000`, char `$6000`, 2bpp — text and banner overlay | clamped wholesale. BG3 carries no world content on any fight-family screen, so text and banners can neither stretch nor be sliced |
| OBJ | the two mobile suits and effects | native placement; bounds widened by the ROM patch below |

Camera X is clamped in ROM to `[64,192]` (`$04:870B-$04:8725`), and BG1's
authored art is world px `[64,448)` (map columns 8..55, tile 0 outside), so the
native 256 view spans exactly the authored world at the walls and **only the
43 px margins can ever leave it**. Mid-scroll the margins hold genuine authored
art and render naturally; at a wall they reflect about the authored world edge
rather than the viewport edge. That is engine pattern **P2b**, which this port
required and which did not exist before it.

### HUD elastic bands

The fight HUD is BG1 map rows 58-63 on scanlines 24..71, mirror-symmetric about
px 128, and it is **one continuous graphic**: recon measured no fully
transparent column anywhere in px 1..254 of those rows. `AnchorBand` alone would
therefore have opened a 43 px transparent hole on each side of the TIME pod, and
`StretchBand` is barred because the band carries glyphs. The shipped policy is
four elastic anchor bands plus one identity band (engine pattern **P2c**),
deciding per *column group* instead of per line — `GwedApplyHudPolicy`, all
constants named in `src/gwed_display.c`:

| scanlines | band | elastic source runs |
|---|---|---|
| 22-23 | clamp | — (tail of arena map row 57, no HUD layout) |
| 24-31 | row 58 | `[88,112)` / `[144,168)` — the plain chrome between each name plate and the TIME label (three identical `$0777` tiles either side) |
| 32-39 | row 59 | `[8,120)` / `[136,248)` — the whole health gauge interior, so each bar gets 43 px longer and keeps its fill fraction |
| 40-47 | row 60 | `[24,120)` / `[136,232)` — the boost gauge interiors (narrower: this row's outer 24 px carry shaped chrome, not bar) |
| 48-71 | rows 61-63 | `[62,63)` / `[193,194)` — **one** column of flat chrome |
| 72 | identity band (a clamp) | — (a HUD tile row drawn at the *arena's* hScroll, so none of the px constants apply) |

Everything else is rigid and byte-exact where it lands: the frame caps, both
name plates, the TIME label, the timer digits, both energy counters and the
round-win markers. Rows 61-63 deliberately stretch one column, not the six
(px 62-67) recon measured as identical: those six are identical only while
nothing overlays them, and a multi-hit combo replaces the energy box with a
wider "nn HIT" readout whose right cap, charge arrow and shoulder land exactly
there — a 6 -> 49 px stretch of that measured as six 8-9 px steps. A single
source column can only be REPEATED, so the widened run is flat in every state.

`GwedApplyHudPolicy` fails closed to the previous centred clamp if the PPU
refuses any band (every setter is validated, the rejection logged once), so a
bad constant can cost the anchoring but can never tear a glyph. A
`_Static_assert` ties the band count to `kPpuWsElasticBands`.

The victory-quote and KO screens get the same treatment for free: both carry
this HUD and the resolver already keeps them World.

### The OAM X-clip ROM patch

The only X clip in GWED's OAM metasprite emitter is 13 bytes at **`$00:A09F`**
(LoROM file offset `0x209F`, m=0 x=0), and it runs in the interpreter:
`src/gen/program_manifest.json` has 37 nodes with `max_pc24 0x8A01`, so no AOT
body in any `(m,x)` variant covers it and MMX's generated-C override approach
would match nothing. A guarded in-memory ROM-image patch is **tier-independent**
instead (`snes_text_xlate.cpp apply_rom_patch` idiom), and `cart->rom` is a
private per-`SnesInit` malloc+memcpy, so the player's file is never touched and
every session boots pristine whether or not `Disarm` ran.

```
expect  c9 00 01 90 08 c9 e0 ff b0 03 4c 0e a1
emit    c9 2b 01 90 05 c9 b5 ff 90 65 c9 00 01     (ws_extra = 43)
```

Accept range becomes exactly signed **`[-(32+extra), 256+extra-1]` = `[-75, 298]`**.
The trap P8 warns about is live here: the carry that accepts an X is the *same*
carry `$00:A0DB` pops to decide the sprite's 9th OAM X bit (`PHP` at `$A0AC`,
popped at `$A0DA`), so widening the two immediates in place would lose the 9th
bit on every right-margin sprite. The 13-byte replacement splits the jobs — two
compares decide accept/reject, then a third against the **unchanged** `#$0100`
regenerates the authentic carry. `$A0AC` and everything after it is untouched.
`emit` derives `0x012b` as `256+extra`, `0xffb5` as `-(32+extra)` and the BCC
displacement `0x65` as `$A10E-($A09F+10)`, and **declines** (site stays vanilla,
logged) for any margin where the raw right-margin band `256..256+extra-1` would
meet the negative band `512-(32+extra)..511` — above extra 144; `kWsExtraMax`
(95) binds first.

No OAM hints are published. `PpuDecodeOamX`'s wrap threshold *moves out* with
the margin (`if (x >= 256 + extraRightCur) x -= 512`), so at extra 43 raw
256..298 already reads as the right margin and raw 437..511 still wraps to
-75..-1. Recon B's "hints REQUIRED" note predates reading that decode.

Sites recorded but deliberately **not** patched: `$00:A0B6` (the 13-byte Y twin
— 16:9 widens X only, and it sits 23 bytes away so it is easy to confuse),
`$00:A07F` (the H-flip mirror, where the sprite sizes appear in code), and
`$04:9CE3` (not a sprite site: the per-scanline WH2 window table HDMA ch7 feeds
to `$2128` for the shockwave effect — the effect will be unwindowed in the
margins, a cosmetic follow-up).

Arm is in `main.c` immediately after the netplay resolution block, **not** in
`GameSessionReset` (which runs before `SnesInit`, so the cart image it could
write is the retired machine, and the caps do not exist yet). `GwedWsPatch_Arm`
refuses unless `g_ws_active && ws_extra > 0`, and the 4:3 path logs nothing —
deliberate, since the P16 gate diffs stderr. Disarm runs from both the mod reset
callback and `GameSessionReset`, never caches a ROM pointer, and restores only
bytes it still recognises as its own.

### Netplay

`GwedFillMatchCaps` (`src/main.c`, wired as `opts.fill_match_caps`) publishes
`widescreen`, `widescreen_hud`, `ignore_aspect` and `ws_extra` (derived from
`GwedDisplay_ComputeFrameWidth`, so a 4:3 host publishes 0 — itself the
"do not patch" signal).

* **Presentation needs no agreement.** The PPU widescreen fields sit outside
  `ppu_saveload`, and widescreen on-vs-off was measured `crc32_wram`-identical
  on every one of 1200 framedump frames.
* **The ROM patch does need agreement**, because it changes OAM staging WRAM.
  `netplay_allows()`: offline -> arm; peer `ws_extra == ours` -> arm; anything
  else, **including -1** (a legacy peer with no caps blob) -> decline every
  site, log, and leave sprite bounds native while keeping the wide backgrounds.
  Fail closed, and the peer with the smaller feature set decides — the only
  direction that converges.
* Rollback is unaffected: `cart_saveload` streams `cart->ram`, cx4/dsp1/sa1
  state and nothing else, so `cart->rom` is never serialized and resim
  re-executes against the same patched image it recorded against.

### Kill switches (P14)

All read once, cached and logged.

| variable | effect |
|---|---|
| `SNESRECOMP_WS_EXTRA=<n>` | override the per-side margin (probes); clamped to `kWsExtraMax` (95). `0` is 4:3 |
| `SNESRECOMP_WS_BG=0` | keep the wide framebuffer, drop every layer/margin policy |
| `SNESRECOMP_WS_BG1_MIRROR=0` | drop BG1's world-mirror band |
| `SNESRECOMP_WS_BG2_MODE=wrap\|mirror\|clamp` | BG2 margin treatment; default `wrap` (no policy — the hardware map wrap) |
| `SNESRECOMP_WS_BG3=0` | drop the BG3 clamp |
| `SNESRECOMP_WS_HUD=0` | restore the previous centred clamp over lines 22..72 exactly |
| `SNESRECOMP_WS_OAM=0` | decline the `oam_x_clip` patch (per-site) |
| `SNESRECOMP_WS_PATCH_DISABLE=<name>[,...]` | generic patch escape hatch, whole-token match; the only site name is `oam_x_clip` |

`SNESRECOMP_WS_CULL` is deliberately **undefined** — see P7 below.

Both patch switches are read fresh at arm time (a rematch may legitimately
answer differently) and both are logged.

## P1-P16 status

`verify/<stamp>` paths are under `analysis/widescreen/verify/`,
`p16/<stamp>` under `analysis/widescreen/p16/`, `recon/*.json` under
`analysis/widescreen/recon/`. That whole tree is gitignored; the findings live
in Beads.

| # | status | measurement |
|---|---|---|
| **P1** scroll phase from the PPU | **verified** | recon R2 (`recon_camera.py`, `recon/camera.json`): rendered `ppu_hscroll` from the frame ring vs the WRAM camera over 701 fight frames — `$7E:0114` disagrees on **44/701** (every frame the camera moves, always leading by one frame of motion), the vblank latch `$7E:068C` on **0/701**; live interleaved sampling 29/240 samples disagree by ±1..3. The world-mirror band takes phase from `ppu->hScroll[]` as the line was rendered |
| **P2** margins populated before display | **N/A — no streaming to hook** | recon R3: the always-on VRAM write ring accumulated across the whole attract fight (**865/865 frames covered**) shows every write inside BG1's map range landing in rows 58-63 x cols 0-31 (the HUD band); BG2's and BG3's map ranges get **no writes at all**. The maps are static, so there is no first-visit column to prefill and no `WS-SHADOW` history needed. `recon/tilemap.json`, `recon/screens.json` |
| **P2b** reflect about the authored world edge | **verified** | Offline synthesis at both camera walls from the recon VRAM dump, scored with `ws_metrics.edge_score` (seam thresholds ratio>=3.0 **and** excess>=18.0): left wall x=43 natural wrap **ratio 5.628 / excess 24.209 = SEAM**, mirror-arena-edge **0.000 / -7.761 clean**; right wall x=299 natural wrap **5.563 / 20.138 SEAM**, mirror **0.000 / -6.124 clean**. Live: `ws_verdicts.py margins attract_fight` PASS `margin_non_blank` 6/6 layers + PASS `native_boundary_seam` (`verify/20260902T152222Z`), against FAIL on all six masks at the pre-change baseline. Engine unit test `tests/ppu/ppu_world_mirror_test.c` PASS, verified discriminating (a viewport fold and axes pinned to 0/256 both fail it) |
| **P2c** anchor rigid groups, stretch elastic material | **verified** | `ws_verdicts.py hud-anchor attract_fight` PASS 4/4 (`elastic_bands_wellformed`, `hud_lines_covered`, `hud_no_transparent_gap` on a BG1-isolated capture, `hud_pixels_anchored`) — `verify/20260902T152129Z`. Control: `SNESRECOMP_WS_HUD=0` FAILs the first two and SKIPs the rest. Band geometry from BG1-isolated captures in all three HUD scenes: row 58's 25-column chrome run -> 68 (25+43), row 59's 112-column bar -> 155, row 60's 20 -> 63, rows 61-63's single column -> 45-49, every rigid run shifted by exactly ∓43. **Amended 2026-09-03:** that single column moved from px 62 to px 87 (mirror 193 -> 168). 62 sits inside the combo readout, so a multi-hit "nn HIT" box was repeated out to the centre group; 87 is chrome/transparent in both HUD states (native cols 86/87/88 identical on 24/24 band lines, 61/61 frames). Verified frame-exact on BG1-isolated captures at extra=71 with the combo readout on screen: rigid segments byte-exact, the run exactly one column repeated. Fill fraction on real content (offline remap of the recon bands, frame-exact): P2 health 99/112 = 0.8839 -> 137/155 = **0.8839 exactly**; P1 boost 71/96 = 0.7396 -> 103/139 = 0.7410. Engine test `ppu_elastic_band_test` PASS, discriminating (the `floor` resample fails it) |
| **P3** periodic layers fold, world-anchored use history | **N/A both ways** | BG2 is periodic and the **hardware's own wrap is already exact**: map column 31 is the X-flipped twin of column 0 for every visible row, `edge_score` 0.000 at both x=43 and x=299, opacity agreement 1.0 (`recon/tilemap.json`) — so no fold policy is installed. No layer needs history because none streams (P2) |
| **P4** key to the scroll rendered this frame | **verified, by construction** | No WRAM camera shadow is read anywhere in `gwed_display.c`; the world band's phase is the line's own rendered `hScroll` inside the PPU. Same measurement as P1 quantifies what using the mirror would have cost |
| **P5** gate is a real state discriminator | **verified for every screen recon could reach; incomplete** | `$7E:1004 == 0x0012` is unique to live fight across **19 scene samples** (5 live-fight, 14 non-fight), `recon/gate.json`. Rejected as a gate: `$2107 == 0x6b` (shared by the victory quote *and* the KO screen — used only as the arena precondition). The P5 HDMAEN trap is live on this title and avoided: the fight's HDMA is effect-driven and intermittent, and `get_dma_state` at a different instant in the *same* fight shows zero active channels. **Not checked** on pause, character select, VS, options/key-config or trial mode — see the gaps section |
| **P6** a mode byte proves the mode, not liveness | **verified as a fact; deliberately unused** | Demonstrated twice: the victory quote passes the coarse `$1000==0x0010` but fails `$1004==0x0012` and fails liveness; and inside the attract cycle `$1004` turns `0x0012` **76 frames before** `$7E:0600` starts incrementing (stage load / round intro). Liveness signal is `$7E:0600`, a 1-byte fight frame counter (**701 changes in a 700-frame window** live, **0 changes** across the victory quote). Rejected as liveness: `$7E:060C`, the round timer — the attract demo runs with the clock disabled (2 changes in a whole attract cycle). The resolver omits liveness on purpose (see above) |
| **P7** cull windows widen symmetrically | **N/A — no lifetime cull exists (disproven by measurement)** | Over an 1800-frame live attract fight (ring sampled every 2 frames, 902 frames read) objects reached screen X **256..268 and stayed live for up to 165 consecutive samples (~330 guest frames)** with the metasprite pointer still animating, while no OAM entry ever carried a signed X outside `[0,255]`. A lifetime cull would have cleared the slot on the crossing frame. 9 such excursions; the clip is entirely in the emitter, so this is one site (P8), not two. `recon/cull.json`. `SNESRECOMP_WS_CULL` is undefined |
| **P8** the OAM emitter is a second gate | **verified (right margin in pixels; left in simulation only)** | `scripts/widescreen/verify_oam_patch.py`, three checks all PASS: `expect` (the vanilla 13 bytes are at file offset `0x209F`); `sim` (a 13-byte 65816 simulator runs both sequences over **all 65536** accumulator values and asserts the accept set is exactly contiguous signed `[-75,298]`, every vanilla-accepted X keeps an **identical carry**, every accepted X round-trips through `PpuDecodeOamX`, and exactly `2*43 = 86` positions are newly accepted — and at extra 0, `[-32,255]` with 0 newly accepted, i.e. inert); `reentry` (byte-exhaustive over-approximating scan of bank `$00` for every relative branch, BRL and abs/long JMP/JSR landing in `$A09F..$A0AB` finds **only the two branches being replaced**). Live: right-margin staging entries (raw X 256..298) appear **only** with the patch armed (`f3310` slots 111@271 112@263, `f3330` 93@258 118@264, ...) and never with `WS_OAM=0`; frame 3400's same-frame pixel diff is left `[0,43)` **0 px**, native `[43,299)` **0 px**, right `[299,342)` **30 px**, with differing columns 305..315 matching `43+raw`. `ws_verdicts.py sprite-nocull attract_fight` PASS both stages (`verify/20260902T070501Z`); control with `WS_OAM=0` FAILs stage 1 (`verify/20260902T070604Z`) |
| **P9** spawn anchor needs margin+1 column | **N/A** | Single-screen arena with a ROM-clamped camera. There is no off-screen spawn scan or level-record walk to widen — the display-list walker `$00:9FB2` consumes per-priority request lists that the fight logic populates for on-stage objects, and P7's measurement shows objects live well past the native edge already |
| **P10** widened spawning must not widen progression | **N/A** | Nothing spawns off-screen (P9), so there is no dual pass and no progression record to protect |
| **P11** the native pass must be a balanced synthetic call | **N/A** | No re-entrant record walk exists (P10). No `cpu_dispatch_call_pc` is issued by any widescreen code path |
| **P12** large objects need widened activation | **N/A** | No activation distance exists: OBSEL `0x62` gives 16x16 / 32x32 sprites all managed by the same emitter, and object lifetime is unconditioned on screen X (P7) |
| **P13** stage-trigger lead must not exceed the margin | **N/A** | No tilemap or CHR staging to bias. The VRAM write ring shows BG map ranges receiving no writes at all during a fight (P2); the per-stage camera routine selected through `$00:D4F1` sets scroll only |
| **P14** every widening gets a kill switch | **verified** | Eight switches, table above, each read once / cached / logged. Two demonstrated **discriminating**, not merely present: `SNESRECOMP_WS_HUD=0` makes `hud-anchor` FAIL `elastic_bands_wellformed` + `hud_lines_covered` (`verify/killswitch-hud-off`), and `SNESRECOMP_WS_OAM=0` makes `sprite-nocull` FAIL `object_render_or_cull` |
| **P15** renderer previews stand down for real objects | **N/A** | No preview/prefill layer exists on this title — the margins are filled by the layers themselves and by the backdrop, so there is nothing to composite over a genuine object |
| **P16** simulation untouched, 4:3 bit-identical | **verified, repeatedly, on the shipping config** | `p16_gate.py` with the package OFF, Release build, no debug server, baseline `build-p16-base2` (a true fork-point build at `a7c55cc`): `wram_crc_identical` / `frame_pixels_identical` / `no_new_runtime_events` all PASS — 3600/3600 frames at offset 0 (`p16/20260902T045536Z-plumbing-crc`), and again over 1800 frames of `boot_attract` after the world-mirror (`p16/20260902T061302Z-worldmirror`), after the OAM patch (`p16/20260902T065151Z-nocull`) and after the HUD anchoring (`p16/20260902T152803Z-hud-final2`). Superset check: with the package **on**, all 21 sampled wide frames are 342 px with columns `[43,299)` byte-identical to the 4:3 run's same-numbered frames and both margins one uniform value (`p16/20260902T045820Z-wide-superset`). Guest-neutrality: widescreen on vs off, `crc32_wram` identical on all 1200 framedump frames (`p16/20260902T045951Z-ws-on-crc`); and `ws_extra` 0 vs 43 with `WS_OAM=0` identical on **201/201** frames. The ROM patch is the *only* thing that moves WRAM, and its footprint is bounded and attributed (see gaps) |

## Validation

Everything runs from PowerShell with the **native** Windows interpreter.
`python` on this box resolves to MSYS and returns POSIX paths the exe cannot
`fopen`; `ws_metrics.require_windows_python()` hard-fails rather than
mis-measuring. See `scripts/widescreen/README.md` for the full harness contract.

```powershell
$py = "C:\Users\Matthew\AppData\Local\Programs\Python\Python312\python.exe"
cd F:\Projects\snesrecomp\_wt-gwed-widescreen\scripts\widescreen
```

### The P16 gate (the release gate)

```powershell
& $py p16_gate.py `
    --baseline-exe  build-p16-base2\GundamWingEndlessDuelSNESRecomp.exe `
    --candidate-exe build-ws-release\GundamWingEndlessDuelSNESRecomp.exe `
    --rom "...\Shin Kidou Senki Gundam W - Endless Duel (J).smc" `
    --frames 1800 --bmp-step 30 --out analysis\widescreen\p16\<stamp>
```

Three checks, in descending order of authority: `wram_crc_identical` (per-frame
crc32 of the guest's 128 KB WRAM from the `--framedump` JSON sidecars —
resolution-independent, so it stays meaningful once the candidate renders 342 px
wide, and it catches guest divergence pixels miss); `frame_pixels_identical`
(BMPs from `SNESRECOMP_FRAME_BMP_DIR`; equal widths compare byte-for-byte, a
wider candidate must match inside the centred native 256 and show one uniform
colour per margin); `no_new_runtime_events` (stderr must not gain stub traps,
watchdog trips, aborts or assertions).

Run it with the package **off** and on the shipping Release config. Boot
latency is not reproducible across processes, so the WRAM series are aligned by
an integer frame-offset search (±120) and identical-but-shifted is a PASS with
the offset recorded — the claim is "same execution", not "same start-up
latency". No wall clock decides anything: `--exit-at-frame` stops both sides on
a known frame. `state:<name>` scenarios need `load_state` and are gated behind
`--trace`, refusing rather than silently measuring a savestate that never
loaded.

### The five verdicts

```powershell
& $py ws_verdicts.py center-parity  --scenario boot_attract:900 --frames 60 --port 4481
& $py ws_verdicts.py margins        --scenario attract_fight               --port 4484
& $py ws_verdicts.py hud-anchor     --scenario attract_fight --hud-json hud.json --port 4485
& $py ws_verdicts.py text-letterbox --scenario state:pre_title --frames 60 --port 4482
& $py ws_verdicts.py sprite-nocull  --scenario attract_fight --port 4486 `
      --expect-margin-object expect_margin_attract_fight.json
```

| check | asserts | owns which scenarios |
|---|---|---|
| `center-parity` | `wide[:, 43:299] == native[:, 0:256]` byte-exact over >=60 frames | any Bounded scenario, and any World scenario only outside the HUD scanlines (see caveats) |
| `margins` | one fresh process per `SNESRECOMP_LAYER_MASK`; per-layer margin fill vs the dominant backdrop, plus a seam detector at x=43/299 corroborated on an adjacent sample with camera motion (motion read from `get_ppu_state` hScroll, never from pixels) | **every World scenario** |
| `hud-anchor` | the engine's exported elastic mapping is well-formed, every recon-declared HUD scanline is governed by a band, a BG1-isolated wide capture is painted in exactly the columns the mapping predicts, and on measured-static scanlines the wide band equals the 4:3 band remapped through that mapping | World scenarios carrying the fight HUD |
| `text-letterbox` | uniform margins, `PpuSetExtraSpaceCentered` budget, centre identical to the 4:3 sibling | **every Bounded scenario** |
| `sprite-nocull` | margin OAM entries must EXIST (`object_render_or_cull`) and DRAW (`object_ppu_or_presenter`); both 9-bit-X readings are reported, neither assumed | World scenarios with objects near an edge |

Exit status: `0` all passed or skipped, `1` a check FAILED, `2` harness error —
the measurement did not happen, which is not a verdict either way. Reports are
DKC2 `report.json` shape and land in
`analysis/widescreen/verify/<utc>/<check>_<scenario>/`.

**Check ownership after the policy table.** `text-letterbox` was the right check
for the attract fight and the victory quote only while *every* screen was
Bounded. Now that they are World, they must be judged by `margins` (+
`hud-anchor`, `sprite-nocull`); `text-letterbox` would correctly FAIL them and
that FAIL would be a check-selection bug, not a feature bug. Each new scenario
must be assigned to a check by its **gate result**, not by what it looks like.

### Entry-state inventory

Already recorded, in `tools/validation_states/` of the primary checkout
(savestates are gitignored, so they are absent from this worktree):
`pre_title`, `pre_quote`, `pre_stage_battle_dialogue_3`,
`pre_stage_ending_dialogue`, `pre_final_convo`, `pre_final_convo_2`,
`pre_ending`, `pre_ending_2`.

`attract_fight` — cold boot, free-run polling the WRAM gate — is **the only
live gameplay reachable without an owner-recorded state**, and its arrival frame
varies run to run, so it is a *sampling* scenario that the frame-exact parity
checks cannot use. The cached `recon/scene_states/attract_fight.state` reloads a
*frozen* fight (mode word still `0x0012`, but `$7E:0600` pinned for 300+ frames,
camera stuck at 172, no object moving) — fine for static per-layer captures,
useless for anything needing motion.

Still needed, and only a human can record them. Raw `snes_saveload` dumps, so
any new PPU field must stay **outside** the `PPU_SAVESTATE_REGS_SIZE` span or
every banked state dies (the new world/elastic band storage does).

| state | what it unblocks |
|---|---|
| `ws_fight_left_wall` | P2b's left-wall reflection in a live process (today: offline synthesis only). The attract demo walks hScroll 128..191 and never reaches the left clamp |
| `ws_fight_right_wall` | P2b's right wall in a live process at the actual clamp (attract reaches h=191, one below the 0xC0 clamp, so up to 42 of the 43 columns) |
| `ws_fight_midscroll` | the P2b mid-scroll assertion — margins byte-identical to the natural render — as a *live* frame-exact measurement, plus a `margins` run with real camera motion so the seam verdict stops being uncorroborated |
| `ws_projectile_at_left_edge` | P8's left accept bound in pixels. Proven in simulation over all 65536 values, never seen drawn |
| `ws_projectile_at_right_edge` | a frozen, frame-exact P8 right-margin capture (today's evidence is sampled from a free-run) |
| `ws_ko` | `margins` + `hud-anchor` on the KO / round-end screen as a live scenario rather than by inheritance |
| `ws_victory` (with the quote box **up**) | the victory-quote text surface, which is still unresolved: a 1700-frame inputless free-run from `pre_quote` never produced quote text (BG3 enabled with an entirely empty tilemap). BG3 is the only enabled text-capable surface and it is idle, so the text almost certainly lands there — but that is inference, and BG3 is clamped on the assumption |
| `ws_charselect` | `$7E:1004` has **never** been checked against character select. Unblocks the P5 completeness claim and a `text-letterbox` run |
| `ws_vs_screen` | same for the VS screen |
| `ws_menu_options` | same for options / key-config, plus the pause menu and trial mode |

## Known gaps and caveats

Stated plainly, because each one is a place a future session would otherwise
re-derive a wrong conclusion.

* **The camera walls are not verified in a live process.** P2b's wall behaviour
  rests on *offline synthesis* — overriding the journal's scroll to 64 / 192 and
  re-rendering the recon VRAM dump. That is legitimate (BG1's hScroll *is* the
  camera at slope 1.0000 / r² 1.0000 and the tilemap is static), but it is not a
  running process. Needs `ws_fight_left_wall` / `ws_fight_right_wall`.
* **The camera clamp constants are ROM-derived, not observed.**
  X `[0x0040,0x00C0]` at `$04:870B-$04:8725`, Y `[0x0070,0x0100]` at
  `$04:8790-$04:87BF`. Both are `target = midpoint(fighters) - 0x80`, clamped,
  then `cam = (target + cam) >> 1` — asymptotic, which is why the observed
  attract maximum is 191 = `0xBF`, one below the clamp. The world bounds
  `[64,448)` follow from the X clamp, so a stage with a different clamp would
  need re-deriving.
* **The gate byte is unchecked on the human-only screens.** `$7E:1004 == 0x0012`
  has never been evaluated on the pause menu, character select, the VS screen,
  options / key-config, or trial mode. The resolver fails closed to Bounded, so
  the worst case is an unnecessary pillarbox, not a torn screen — but P5 is not
  complete until those are sampled.
* **No two-peer netplay run.** The caps publication, the agreement gate and the
  mismatch degradation are implemented and reasoned (and the presentation side
  is measured guest-neutral), but they have never been exercised against a real
  second peer.
* **The launcher's Mods row is not visually confirmed.** The page cannot be
  rendered headlessly (ImGui + a real video driver). Indirect proof only: the
  mod runtime scanned the package, matched `game_id` + `rom_sha256`, committed
  the plan and dispatched the `gwed.widescreen` plugin
  (`[ws] widescreen on — frame 342x224 (margin 43/side)` in the wide run's
  stderr log). An owner playtest is still needed.
* **The combo readout box is ~43 px longer during a combo.** Consequence of the
  one-column elastic run in rows 61-63. It reads as a wider readout panel, not
  as damage, and no glyph is touched — but it is a real difference from 4:3.
* **`center-parity` no longer holds byte-exactly on HUD scanlines**, and that is
  the anchoring working. `boot_attract:900` still passes 60/60 because it never
  reaches a World fight screen (the attract fight arrives ~frame 3286). A future
  centre-parity scenario that *does* include a fight screen must exclude lines
  24..72 or apply the exported elastic mapping before comparing. **Do not relax
  the byte-exactness.**
* **Cross-process byte-exactness is not a safe assumption on this engine.**
  Every pixel-parity check runs a third 4:3 **control** process against the
  first, and a mismatch counts as a widescreen defect only if it starts earlier
  than the control's own divergence. `state:pre_quote` diverges 4:3-vs-4:3 at
  frame 0 of the compared window and its gate read `0x0014/live=true` on one run
  and `0x0000/live=false` on another (it was banked with the PAR freeze applied,
  so the round ends by itself) — it therefore yields **no** centre-parity
  evidence and is reported SKIP, not PASS. One `text-letterbox` run gave a
  4-frame prefix where an earlier run of identical code gave 60/60: run-to-run
  variance in the APU-paced control, not a regression. Treat a single harness
  death on a `state:` scenario as a flake, not as savestate breakage.
* **The ROM patch's WRAM footprint** (the one place widescreen is *not*
  guest-neutral, and the reason it is caps-gated): with the patch armed,
  191/201 frames differ from the unpatched run, and over the whole window
  exactly 203 distinct byte offsets ever differ — all of them in `$0020` (the
  emitter's OAM slot cursor), `$0E4C..$0F1F` (the tail of the OAM staging
  buffer `$0D00..$0F1F`), and `$0FD3..$0FE5` + `$0FFD`, which is **dead stack
  below SP** — attributed, not guessed, via `set_wram_watch 7E 0fd3` returning
  `S=0x0FD3` on the writing event. Nothing outside those ranges ever differs.
* **`margins`' own per-layer metric is weak.** It reported BG4 — which this game
  never enables — as painting both margins at a non-backdrop fraction of 0.857,
  and reported the identical 0.857 for bg3/bg4/obj/composite. The PASS is real;
  the per-layer numbers must not be read as evidence about a specific layer.
  The seam sub-check on that run had `camera_motion false` and all-zero hScroll
  deltas, so the seam verdict is uncorroborated by motion.
* **Honest visual read of the reflected margins:** no seam, and at the walls the
  reflection reads as plausible architecture because this arena's art is already
  a symmetric plaza — but the bilateral symmetry *is* visible in the pavement
  diagonals and the repeated tree spacing if you look for it.
* **The shockwave effect will be unwindowed in the margins.** `$04:9CE3` builds
  the per-scanline `$2128` window table for it and has its own `CMP #$00FF`
  clip, deliberately unpatched. Cosmetic follow-up, not a cull.

### Harness traps (each produced a confident wrong answer first)

* **`screenshot` is not frame-exact.** It copies the last presented buffer and
  reads `snes_frame_counter` afterwards: a poll at frame 917 returned a reply
  labelled **920**, which first appeared as a "one-pixel palette difference"
  FAIL on `center-parity` (50/60 frames). Frame-exact checks capture through
  `runner/src/widescreen.c`'s `SNESRECOMP_FRAME_BMP_DIR` dumper, which writes
  from inside the present path and names each file after the frame it presented.
  `screenshot` is still fine where the label only identifies a sample.
* **`run_to_frame` pauses the guest on arrival** and is never called. Everything
  free-runs and polls `frame`; rings are queried, never armed.
  `clear_controller` runs in every `finally` path.
* **The exe will not start when spawned from the agent harness's Bash tool** —
  the child dies in the loader (`0xC0000079` / `0xC0000135`) before printing
  anything, with a full or a minimal environment block alike. The identical
  `subprocess.Popen` from PowerShell works. **Run these scripts from
  PowerShell.**
* **`vwring_get` caps at 4096 returned entries**, which at ~1350 VRAM
  writes/frame is only ~3-11 frames, far shorter than the 1<<17-entry ring.
  Sample repeatedly and accumulate, or "no writes" means nothing. Likewise
  `get_frame_range_extended` caps its reply at ~30 KB (~120 rows), so its
  documented 500-frame maximum silently truncates — query in <=90-frame chunks.
* **`sprite_timeseries` is SMW-hardcoded.** Do not use it here.
* **Blankness on a layer-isolated frame is per-column-over-the-band, not
  per-scanline.** The modal colour of a HUD line is the *chrome* (253 of 342
  columns read "blank" on line 24) while the authentic band has 63 transparent
  interior pixels on that line and every column in px 1..254 is painted by
  *some* line.
* **The unpainted colour must be identified within each frame.** The two
  compared sides are separate processes at different guest frames, and GWED's
  arena paints its backdrop per scanline through HDMA — requiring the two to
  agree measured only that the gradient had moved (it had, on every band line).
  For the same reason `rect_has_ink` takes per-row backdrops
  (`ws_metrics.row_backdrops`): an OBJ-isolated capture measures ~85%
  "non-backdrop" against a single frame-wide mode and decides nothing.
* **Pillarbox margins are opaque black, not CGRAM 0.** `gwed_display.c`'s
  Bounded branch memsets them, so a switch out of World cannot leave a stale
  world frame behind. Measured at `state:pre_quote`: CGRAM0 `#000083`, margins
  `#000000`. A check written as "margins == backdrop" would FAIL a correct
  implementation, so `margins_uniform` is the unconditional assertion and the
  colour is a separate check that accepts backdrop-or-cleared and names which it
  saw.
* **Activation is asserted from geometry, not from stderr.** The exe's captured
  stderr comes back empty on this build, so the `[ws] widescreen on` line is not
  usable as proof. The checks assert captured width 342 and
  `get_ppu_state.widescreen.budget == 43`, neither reachable unless
  `GwedDisplay_ComputeFrameWidth` saw the package enabled; a wrong width is
  raised as a harness *error*, never as a verdict.
* **A seam hit on a layer whose margin is empty is the pillarbox edge**, so it
  is downgraded to an informational finding rather than double-counting one root
  cause — which is why `native_boundary_seam` read PASS on the baseline run
  while `margin_non_blank` FAILed.
* **Elastic runs must be measured on the layer in isolation**
  (`SNESRECOMP_LAYER_MASK`), never on the composite, where the bar's transparent
  pixels show whatever is behind them and vary per column and per frame.
* **One process per build directory at a time.** `mods/preloaded/state.toml` is
  shared by every process launched from one exe directory. Port blocks:
  4471-4479 recon, 4481-4489 verdicts, 4491-4499 P16 gate. When copying an exe
  dir, `Copy-Item src\dir\* dst\dir\` — never `Copy-Item src\dir dst\dir`, which
  nests.

## Disproved theories

Recorded so nobody re-investigates them.

* **`$7E:060C` (the round timer) as the liveness signal** — REJECTED. The
  attract demo runs with the clock disabled (the HUD shows the infinity glyph
  `$35C-$35F`): 2 changes in a whole attract cycle. `$7E:0600`, a 1-byte fight
  frame counter, is the signal.
* **`$2107`/BG1SC `== 0x6b` as the screen gate** — REJECTED. Shared by the live
  fight, the victory quote *and* the KO screen. It survives only as a
  *precondition* on the world bounds, never as a classifier.
* **`$7E:1004 == 0x001E` as "round end / dialogue"** — insufficient on its own.
  It is also set for `black_transition`, `final_convo` and `ending`, which are
  not the arena.
* **A raw full-WRAM diff as the route to the gate byte** — DISPROVED. 6600 raw
  byte candidates from a live-vs-non-live diff are almost all noise; bank `$7F`
  alone accounts for 6337 and is decompression/tile scratch. And any candidate
  the fight merely *initialises* passes a diff whose non-fight samples are all
  taken before the fight — post-fight samples are mandatory.
* **P7 (a lifetime cull) as a second site** — DISPROVEN by measurement, see the
  P7 row. `SNESRECOMP_WS_CULL` is deliberately undefined.
* **"`PpuWsSetOamRightHints` is REQUIRED for raw 256..298"** (recon B) — WRONG,
  and it predates reading `PpuDecodeOamX`. The wrap threshold moves out with the
  margin, so no hints are needed at extra 43; hints only make that band strict
  per slot, which matters only when the two raw bands overlap — which the
  emitter now refuses to allow. `SNESRECOMP_WS_HINTS` is not defined.
* **`tools/apply_overrides.py` (MMX's generated-C override route) as the patch
  mechanism** — not applicable. `src/gen/program_manifest.json` has 37 nodes
  with `max_pc24 0x8A01`, so `$00:A09F` has no AOT body in any `(m,x)` variant
  and the override would match nothing. The guest-side work is a guarded
  in-memory ROM-image patch instead.
* **"Just widen the two immediates" at `$00:A09F`** — wrong, and it is the P8
  trap. The accepting carry *is* the 9th OAM X bit, so widening in place clears
  it for every right-margin sprite.
* **A six-column elastic run (px 62-67) in rows 61-63** — measured identical in
  all three HUD scenes and still wrong. A multi-hit combo replaces the energy
  box with a wider readout whose right cap, charge arrow and shoulder land
  exactly there; the 6 -> 49 px stretch rendered as six visible 8-9 px steps.
  There is no multi-column run flat in *both* states — the combo state has no
  run wider than two anywhere in px 56..85 — so the shipped run is one column,
  which can only ever be repeated. **"Identical in every scene recon captured"
  is not "identical in every state the game can draw."**
* **`oam_write_get` naming the metasprite emitter** — it cannot on this game.
  The emitter writes the WRAM staging buffer `$7E:0D00..$0F1F`; the only writer
  of OAM proper is the DMA at `$00:85BE`, which is the single name it returns
  for every OAM byte in an 1800-frame fight. The patch target came from a ROM
  decode of the `$0D00,X` stores.
* **The always-on ring's `pc24` as writer attribution** — for a `WRAM_WRITE` it
  degenerates to `PB<<16` (its low 16 bits carry the WRAM address), and
  `func_pc`/`block_pc` come from a backscan that in the interpreter tier lands
  on the most recent raster-IRQ handler. It blamed `$00:88E7`/`$8900`/`$891F`
  for `$7E:0114`, none of which contain a store to `$0114`. Always cross-check
  with a ROM decode.
* **`ws_verdicts.py hud-anchor`'s original BG assertion** (that the HUD layer's
  hardware window edges expand past x=256) — can never pass on this game: BG1's
  window clipping is *disabled* inside the band (`screenWindowed` 0x1f->0x1e on
  the fight, 0x15->0x14 on the quote), and `ppu_window 40 0` replies
  `valid:false`. Replaced by four assertions on the engine's own exported
  mapping plus a BG1-isolated pixel check.
* **`sprite-nocull` positioning by the hardware X reading** — a genuine
  right-margin sprite at raw 257 was looked for at framebuffer x -212, clipped
  out, and reported as "in the margin but blank": a fake defect.
  `ws_metrics.signed_x_interpretations` now also reports `engine`
  (`raw >= 256+extra -> raw-512`, i.e. what `PpuDecodeOamX` does) and the check
  positions by it.

## Map overlap warning (for any future tile-writing approach)

BG1's map (`$D000`, 64x64) and BG3's (`$C000`, 64x64) **overlap** at
`$D000-$DFFF`: BG3's bottom quadrants are BG1's top quadrants. The game gets
away with it because BG1 displays only rows 29-56 and BG3 only uses columns
0-31 (during the attract fight BG3 shows map rows 0-6; on `ko_1p_win`, rows
5-10). The presentation-only implementation writes **no tiles at all**, so this
is currently harmless — but any future scheme that writes BG1 margin tiles must
stay out of BG1 rows 10-31 (= BG3 rows 42-63).
