# Validation savestates

Savestates banked **just before** a text surface draws its text, so
per-language validation is *load state → let the scene redraw → capture*
instead of replaying the game. Reaching the late-game surfaces by playing
takes ~9 minutes of unattended driving; loading a state takes ~20 seconds.

The `.state` files themselves are **not committed** — they are raw
`snes_saveload` dumps, versioned by `L3_SNAP_MAGIC` / `L3_SNAP_VERSION` in
`snesrecomp/runner/src/debug_server.c`, and any engine change to the SNES
state layout invalidates them. `tools/validation_states/*.state` is in
`.gitignore`. This README is the committed part: what each state is for and
how to regenerate it.

## Harness

```powershell
# quickest A/B: same state, two languages
scripts\validate_from_state.ps1 -State pre_quote -Language ko -ParFreezeSeconds 70
scripts\validate_from_state.ps1 -State pre_quote -Language it -ParFreezeSeconds 70

# walk a multi-page text sequence forward from the state
scripts\validate_from_state.ps1 -State pre_ending -Language en -Advance 6 -WaitSeconds 4
```

Captures land in `analysis/state_validation/<state>_<lang>/`
(`shot.bmp`, `ppu.json`, `cgram.json`, plus `vram.json` with `-DumpVram`).
Default build dir is `build-agent`; default port 6650.

## Cross-language semantics (verified 2026-08-31)

* `load_state` into a **freshly launched process works** — no fiber-era
  breakage. Verified by loading an `en`-saved state into a `ko` process on
  the current engine; the game ran on from the loaded state normally.
* A state carries **the VRAM of the process that saved it**. Right after a
  load, on-screen text is still the *saving* language's. Only the next
  **redraw** of a surface comes out in the *loading* language, because that
  process patched its own in-memory cart image at boot. This is expected and
  fine — it is exactly why states must be banked *before* the text draws,
  and why the harness waits (`-WaitSeconds`, `-Advance`) for a redraw before
  capturing.
* `xlate_stats` reports `"effective_language"` as the **last** entry of the
  fallback chain (`ko` → `en`), not the selected language. `"language"` is
  the selected one. An `effective_language` of `en` under `ko` is normal.
* `load_state` does **not** restore the cart ROM. `cart_saveload` streams only
  `cart->ram` (battery SRAM) plus coprocessor state, so the loading process
  keeps its own language-patched in-memory cart image. Worth knowing, because
  "load_state undoes my ROM patches" is the first theory anyone reaches for
  when a loaded state renders the wrong language — and it is wrong.

## "Before the draw" is not early enough for a STAGED surface

`battle_dialogue_3` and `ending_dialogue` copy their whole script out of the
cart into WRAM when the **scene** loads, and the draw routine then feeds off
WRAM, never re-reading the cart:

| group | staged to | rows |
|---|---|---|
| `battle_dialogue_3` | `$7f:0100` + row*0x100 | 30 |
| `ending_dialogue` | `$7e:6000` + row*0x100 | 28 |

A state banked between that copy and the first character typed is already too
late: the loading process patched its cart, but nothing re-reads it, so the text
comes out in the **saving** language forever. `pre_final_convo` and `pre_ending`
are both such states — verified 2026-08-31 by loading `pre_final_convo` in `it`
(a language whose dialogue payload has been shipping for days) and finding the
**English** tilemap words at WRAM `0x10104`, `0x10204`, ... So a capture from
either of those states says nothing about the loading language, in any language.

Diagnose it with a search, not a stare: after the load, `dump_ram 0 131072` and
look for the surface's source-language tilemap words (`en_hex[4:16]` of each row
in `endless_duel_dialogue.toml` is a good 12-byte needle). Finding them is proof
of staging and tells you the staging address.

Then key the state hunter on the **staging**, not on the draw. `statehunt.py`
polls VRAM for the drawn tilemap, which is structurally late for a staged
surface. `stagehunt.py` (same ring-buffer discipline, earlier trigger) polls
WRAM for the row block and promotes the older rolling state the moment >=5 rows
of a group appear:

```powershell
py -3 tools\validation_states\stagehunt.py <port> <seconds>
```

The states it banks are named `pre_stage_<group>.state`.

`battle_dialogue_0` stages too — this README claimed otherwise until 2026-09-03.
The victory quotes are copied to `$7f:0000` + row*0x80 (the same 0x80 row stride
the cart uses, so the block is a verbatim run of rows) and the box then types out
of WRAM. `pre_quote` is still a valid state for it, and for the reason the old
note gave — it is mid-fight, so the copy happens *after* the load, in the loading
language. What is not true is that the surface reads the cart at draw time. If a
`pre_quote` capture ever comes out in the wrong language, dump WRAM at `0x10000`
before concluding anything about the patch table.

## Never capture a typewriter scene at a fixed offset

`validate_from_state.ps1` takes one screenshot at `-WaitSeconds`. On a dialogue
box that is a coin flip, and it produced a false bug report: a `pre_quote` capture
in `es` showed line 1 complete and line 2 blank, which read as "the Spanish second
line is missing" and was investigated as a data defect for hours. It was the
inter-line pause. Polling the same scene with no input shows the real sequence:

```
t+1.6  L1 |Cumplire mi mision |   L2 |                |
t+2.4  L1 |Cumplire mi mision |   L2 |so              |
t+3.2  L1 |Cumplire mi mision |   L2 |solo si debo!   |
```

Line 1 finishes at ~1.6 s and line 2 starts at ~2.4 s, so there is a ~0.8 s window
where the box looks exactly like a dropped line. Spanish is *more* exposed than
English here, because its rows are shorter and finish earlier — which is precisely
what makes the artifact look language-specific and therefore like a real bug.

Two rules. Poll until the box stops changing rather than guessing an offset; the
`catch_*_dialogue_page.py` scripts already do, and their WRAM/VRAM needle hit is
the assertion. And do not press A while sampling — the taps advance the page, so a
probe that taps and dumps in the same loop dismisses the text it is trying to
observe, and reports a blank box as evidence.

## Don't guess -Advance on a typewriter scene — poll

`validate_from_state.ps1 -Advance N` takes one capture at a guessed offset. On a
dialogue sequence the box is blank between pages, cleared during transitions,
and BG3 is not even enabled for part of it: a sweep of `-Advance 3..7` on
`pre_stage_ending_dialogue` landed on an empty box five times out of five, while
`-Advance 10` hit for `zh` and missed for `ko`. Poll the assertion instead:

```powershell
py -3 scripts\catch_dialogue_page.py <port> ko pre_stage_ending_dialogue endko
```

It walks the scene forward, dumps VRAM each step, stops the moment a generated
page guard matches the live BG3 map, and asserts the page bytes equal the
generated payload byte-for-byte. `scripts\validate_from_state.ps1` now also
dumps VRAM *before* the screenshot for the same reason — the dumps are hundreds
of milliseconds apart on a running game, so the thing you assert on should be
the freshest.

## Banked states

| name | precedes | notes |
|---|---|---|
| `pre_title.state` | the title screen's mode-selector fade-in | boot sweep frame 37; the labels animate in a few seconds later |
| `pre_quote.state` | a story-mode victory/defeat quote (`battle_dialogue_*`) | mid-fight. The fight will NOT end on its own — pass `-ParFreezeSeconds 70` so the harness re-applies the PAR freezes and the round ends into the quote box |
| `pre_final_convo.state` | `battle_dialogue_3`, the post-final Treize conversation | multi-page; use `-Advance N` to step pages |
| `pre_final_convo_2.state` | a later page of the same conversation | |
| `pre_ending.state` | `ending_dialogue`, the per-pilot epilogue (Heero/Wing run) | the expensive one — a full story clear |
| `pre_ending_2.state` | a later page of the same epilogue | |

Not banked yet: the attract-mode crawl (it plays ~25 s after the title
idles; extend the boot sweep past the title to catch it) and the intro
caption sprites.

## Regenerating

Engine/build the current set was made on: `build-agent`, engine tree
`snesrecomp/` at the working-tree revision of 2026-08-31, `save_state`
payload 297352 bytes.

Two scripts in this directory do the work (`tcp.py` and `sigs.py` are their
support modules; both resolve the build dir and ROM from the repo root, and
`GWED_BUILD_DIR` overrides the build dir):

* `bootstates.py <port>` — boots, then banks one state + one screenshot per second
  for 46 s. Pick the frame just before the surface you want.
* `statehunt.py <port> <tag> <seconds>` — drives story mode to completion, polling VRAM for the
  dialogue-row signatures from `endless_duel_dialogue.toml` and keeping a
  **rolling pair** of states 8 s apart. On every new dialogue hit it promotes
  the *older* rolling state to `pre_<group>_<n>.state`, so every capture has
  a state from ~8-16 s before it. This is the ring-buffer pattern: never arm
  a recording and hope to catch the event, keep a buffer and reach back.

Both use `save_state <path>` / `load_state <path>` over the debug TCP server
(synchronous, full CPU/PPU/DMA/APU/cart/WRAM snapshot). Note `dump_ram` and
`dump_vram` parse the **address as hex** and the **length as decimal**.

## PAR codes used to steamroll story mode

Pro Action Replay codes for the Japanese ROM, from
<https://gamefaqs.gamespot.com/snes/564117-shin-kidou-senshi-gundam-w-endless-duel/faqs/11365>
(Darth_Nemesis). A PAR code is a per-frame WRAM freeze, so the debug
server's `write_ram` reproduces it exactly: poke the address every poll tick.
All five were verified live on this ROM.

| address | value | effect |
|---|---|---|
| `7E:1B70` (16-bit) | `ff 70` | P1 health (the bar) |
| `7E:1B74` (16-bit) | `ff 70` / `01 00` | P2 health — set to 1 to end the round |
| `7E:1B80` (16-bit) | `2c 01` (300) | P1 energy (the numeric counter) |
| `7E:1B84` (16-bit) | `2c 01` / `01 00` | P2 energy |
| `7E:060C` | `99` | round timer |

```
write_ram 1b70 ff70    # P1 invincible
write_ram 1b80 2c01
write_ram 1b74 0100    # P2 dead
write_ram 1b84 0100
write_ram 060c 99
```

Independently derived first by scanning WRAM for the `300` both HUD counters
show, then poking each candidate to `50` and reading which counter changed —
which is why `1B80`/`1B84` were found before the code list was consulted.
**Both pairs matter**: freezing only the energy words (`1B80`) still lets the
health words (`1B70`) reach zero, and the player loses with `300` on screen.
Freezing all four clears story mode in about 7 minutes of unattended driving.
