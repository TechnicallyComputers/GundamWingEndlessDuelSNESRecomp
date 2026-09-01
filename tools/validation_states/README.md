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
