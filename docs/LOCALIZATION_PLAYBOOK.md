# SNES Recomp Localization Playbook

Endless Duel is the reference implementation for runtime localization in
the SNES recomp tree. This document is the reproducible process: how to
take a new SNES game from "untranslated" to "every text surface renders
natively in the selected language", using the mechanisms proven here.
It complements `TRANSLATION_TILEMAP_REFERENCE.md` (data-format details)
and `../translations/README.md` (this game's specific asset map).

Everything below was validated in practice during the 2026-08 Endless
Duel localization hardening (beads-8wg.13 / beads-8wg.14). Where a rule
exists because something burned us, the burn is documented — those are
the parts that make this reproducible rather than aspirational.

---

## 1. Doctrine

**Localization is asset interception through the game's own render
path. Nothing else.**

1. NEVER detect game screens with framebuffer pixel heuristics.
2. NEVER draw replacement text over the presented frame from the host.
3. Patch the assets the game itself reads — ROM bytes, uploaded VRAM
   bytes, RAM-resident text — behind byte-exact guards, and let the
   game's own engine draw them.
4. If an asset path is genuinely not recoverable yet, gate any interim
   mechanism on WRAM game state, never on sampled pixels. Then recover
   the asset path.
5. Always ship the complete mechanism, not the narrow patch. A
   generator that covers every label beats a hand patch for the one
   label under investigation.

Why this is non-negotiable: Endless Duel originally shipped a
screen-space overlay that color-sampled hardcoded pixels to guess which
screen was up, then erased/redrew text in the framebuffer. Every one of
its failure classes is structural, not a tuning problem:

- **False negatives on transitions.** The title menu's fade-in slides
  the labels up via tilemap animation; a detector keyed to final-position
  pixels can't fire mid-animation, so the source language showed during
  every transition.
- **False positives on unrelated scenes.** "4 of 6 sampled points are
  dark" matched the intro FMV, so option/key-config text got painted
  over cinematics.
- **It can never be right**, because the presented frame is an output of
  game state, not a readable encoding of it.

Asset interception is immune by construction: patched tiles render
wherever and whenever the game references them (fades, slides, palette
animation, typewriter effects all included), and can never appear on a
screen the game doesn't draw them on.

---

## 2. The runtime patch engine

`snesrecomp/runner/src/snes_text_xlate.cpp`, loaded from a per-game
TOML table (here `translations/endless_duel.toml`). The game-side glue
(`src/translation_mod.c`) resolves the table path, reads the selected
language from the mod runtime, and calls `snes_text_xlate_init_c` +
per-frame `snes_text_xlate_on_frame_c`. That is the entire game-side
footprint: the mod never touches the framebuffer.

### Patch kinds

| kind | target | applied | use for |
|---|---|---|---|
| `[[rom_patch]]` | in-memory cart image | once, at language set, in FILE ORDER | anything the game re-reads from ROM: script bytes, tilemaps, display lists, uncompressed tile art |
| `[[ram_patch]]` | WRAM | every frame | RAM-resident text only |
| `[[vram_patch]]` | VRAM bytes | whenever VRAM was written (dirty flag), re-checked continuously | uploaded assets whose ROM source is compressed, generated, or shared — the guard IS the upload interception point |

### Semantics that matter (each learned the hard way)

- **Guard:** a patch applies only when the current bytes equal
  `source_hex` OR any language's target payload (so runtime language
  switching can overwrite a previously applied language).
- **Fallback chains:** root keys like `fallback_fr = "en"` build a
  per-language lookup chain. A patch with no native payload for the
  selected language resolves through the chain.
- **ROM patches with no payload anywhere in the chain write `source`
  back** — that is the restore path for language switching and is safe
  for ROM (no ambient-state hazard; at boot it's a no-op).
- **RAM/VRAM patches with no native payload are NO-OPS. They must never
  write `source` back.** This is an engine rule (snesrecomp `2406e0c`).
  The failure it prevents: six vram_patches blanked the title-label map
  rows for CJK with all-zero payloads. All-zero also happens to be the
  game's own "cleared VRAM" state, so the guard matched during the
  attract fight for *every* language; the pre-fix engine then "restored"
  the title tilemap into a scene the game had deliberately cleared —
  re-stamping it every frame, visible as sequential-letter garble
  ("cdefst") wherever that map region scrolled into view. An all-zero
  target is a *wildcard guard*; combined with write-source fallback it
  turns "no translation for this language" into "corrupt live VRAM".
- **File order is application order** for rom_patches, and it is load
  bearing: a generated aggregate patch placed after reference chunks
  must use the *post-chunk* bytes as its `source_hex` (see the crawl
  grid), while a patch guarding on *original* bytes must be certain the
  earlier chunks are inert for its languages (see the intro caption,
  which relies on the Latin sub-span entries carrying no payload
  reachable from ko/zh).
- **Check every fallback key exists before relying on it.** This table
  has no `fallback_es`; an es-less rom_patch therefore restores
  *Japanese* bytes for Spanish. Generators must compute the actual
  chain from the table and emit explicit payloads for any language that
  cannot reach the baseline.
- **Patches must not overlap** (stated invariant of the table). When a
  new aggregate patch must own a region already covered by imported
  IPS fragments, the generator splits/removes the fragments and proves
  equivalence: replaying the old table and the new table over the
  original ROM must produce byte-identical images for every language
  that had data there (`generate_intro_caption_patch.py --check` does
  exactly this for en and es; copy that pattern).

---

## 3. Surface taxonomy

Every text surface met so far is one of four shapes. Identify the shape
first (Section 4); the mechanism, generator pattern, and pitfalls
follow from it.

### 3a. Encoded BG tilemap text (crawl, battle/ending dialogue)

**Recognition:** the visible text is a BG layer; the tilemap words map
glyphs through a recoverable char→tile table; the tile art is a font
the reference translation installed. Reference-hack IPS spans that are
"repeating two-byte words" are usually this.

**Mechanism:** `rom_patch` rewriting the tilemap words. Glyph words
come from a recovered table (e.g. crawl `214c`='c', bottom half stored
+32 words; dialogue top tile + `top_tile+0x10`). CJK needs real glyph
tiles first — authored 16x16 cells shipped as guarded `vram_patch`
entries over the font tile region (`generate_cjk_crawl_patch.py`).

**Endless Duel instances:** opening/fight crawl (64-wide strip at
`0x02f23f`, `generate_crawl_patch.py`), dialogue rows at `0x017000+`
(`generate_dialogue_patch.py`). Formats in
`TRANSLATION_TILEMAP_REFERENCE.md`.

**When the script is not one-codepoint-per-cell: line as image.** Thai
stacks up to two vowel/tone marks over a base consonant plus one vowel
below, and it needs shaping, so *no* per-character cell model can express
it — the marks would occupy their own advancing cells. The answer is to
stop allocating per character and allocate per **8x8 tile of a rendered
line**: shape and rasterise the whole row as one strip, slice it, dedup by
tile bytes, and have the ROM row address those tiles positionally
(`mode = "line"` in `generate_dialogue_cjk_patch.py`). Three things this
changed, all reusable:

- **Shaping must not go through Pillow** unless it was built against
  libraqm (`PIL.features.check("raqm")`). Without it Pillow does no
  complex-script layout and the marks advance as spacing glyphs — which
  looks like a font bug and is not one. `scripts/gdi_text.py` shells out
  to GDI/Uniscribe instead (`scripts/render_text_gdi.ps1`), a
  GENERATION-time dependency on Windows plus an installed Thai font. The
  shipped table is baked tile bytes, so players depend on neither.
- **Band compression is what makes a tall script fit a short line.**
  A Thai line needs 17-20 rows at a fixed baseline for any em size where
  the base consonants keep the loops that tell ก/ถ/ภ, ด/ต, บ/ป apart, but
  the dialogue line is 16. Render at the larger em and squash the
  above-mark and below-vowel bands 2:1 while keeping the base-consonant
  band at full height — exactly what hand-made Thai pixel fonts do.
  Measure the band boundaries from the font's own rendering of probe
  strings; never hardcode pixel rows, and never centre per line (the
  baseline has to be constant or the text bounces line to line).
- **The all-background tile is not the blank cell.** In these boxes an
  empty tile is colour index 1 (the box's black fill), not 0, so it can
  NOT be replaced by the game's own blank map word — that word is tile 0
  under a different palette. Emit it as a real page tile; dedup collapses
  every blank column onto that one.

Line mode is also a strict budget WIN: the zero-dedup ceiling is
2 rows x 28 columns x 2 halves = 112 tiles, below the tightest page
window, so it can never overflow. And the fit test becomes pixels rather
than cells, so horizontal budget stops being a constraint at all.

### 3b. Display-list text through a runtime font (option / key-config)

**Recognition:** ROM contains readable ASCII near opcode-looking words;
strings render through a font where tile index is a function of the
character code. Endless Duel's list (at `0x00c000-0x00c800`): 16-bit
opcodes `0x000c` set col/row, `0x0006` set attribute mask, `0x0002`
print bytes until `0xff`, `0x0018` print variable, `0x0004` end. Font:
BG2 Mode 0 2bpp, char base 0x0000, code `c` (0x20-0x9f) → top tile
`2*(c&0xf0)-0x40+(c&0x0f)`, bottom `+0x10` (8x16 cells).

**Mechanism, Latin:** fixed-width `rom_patch` of the string bytes
(`generate_option_patch.py`).

**Mechanism, CJK — font-slot injection** (`generate_option_cjk_patch.py`):
1. Find free codes: font tiles that are all-zero on these screens AND
   never referenced by any record (here codes 0x60-0x9f).
2. Ship glyph art into those tiles as `vram_patch` with all-zero
   `source_hex` — a genuine guard *only after verifying* the same VRAM
   region is non-zero on every other screen (here the title screen uses
   it), so the patches no-op elsewhere. Verify that; do not assume.
3. Rewrite whole records (`rom_patch`, cursor byte preserved, both the
   selected AND deselected variant of every row — menus keep redraw
   records per state) to use the allocated codes.
4. If the font were raw in ROM, glyphs could be rom_patches — check by
   searching the ROM for the tile bytes in every plane orientation.
   Endless Duel's option font is generated at runtime (zero hits), so
   VRAM interception is the only hook. That is fine: it is the same
   proven mechanism.

### 3c. Pre-rendered unique BG tile art (title menu labels)

**Recognition:** each on-screen label is a run of unique consecutive
tile indices in the map (no repeats for repeated letters = not a font).
Dump the map rows and count tile reuse; blanks aside, unique means
pre-rendered.

**Mechanism:** re-render the label text as tile art per language and
ship it as `vram_patch` over the uploaded tiles (ROM source compressed
here — again verified by searching for the VRAM bytes in ROM), guarded
by the captured stock art (`generate_title_menu_vram_patch.py`, stock
art checked in as `endless_duel_title_menu_assets.toml`).

**Details that decided the design (measure these on a new game):**
- Selection highlighting was CGRAM-only (each label owns one palette
  index; selection swaps CGRAM colors). One art set per language then
  inherits highlighting. If selection had been separate tiles, both
  variants would need authoring.
- Labels straddle tile rows (12px pitch, 8px glyphs, adjacent labels
  sharing tiles) → author whole row-block canvases, never per label.
- The fade-in animation moves the *map words*, not the scroll — which
  is precisely why any fixed-position replacement fails and tile-art
  interception succeeds without doing anything.
- Re-check the tile addresses on other scenes: guards make collisions
  inert, but know what you're relying on.

### 3d. Pre-rendered OBJ sprite art (intro caption)

**Recognition:** the text moves/types like a sprite; `dump_oam` shows
sprites over it; the BG layers under it carry no text. Compare OAM
across languages — if OAM is byte-identical and only tile art differs,
the whole translation is art.

**Mechanism:** `rom_patch` on the sprite tile art when the reference
hack stores the OBJ bank uncompressed in ROM (here `0x00e000-0x00ffff`,
VRAM `0x8000-0x9fff` a byte-exact linear copy, tile n at
`base + n*32`). Free-form pixels — no glyph constraints at all, any
script fits, only geometry matters (map the sprite cells from OAM,
including dead gaps and shared blank tiles you must not draw into, and
decorative sprites like blinking cursors you must not clobber).

**Generator:** `generate_intro_caption_patch.py` — renders text with a
real font (Pillow) into the cell geometry, reuses the surface's own
palette ramp indices, records the pre-existing baseline art, and does
the fragment split-and-prove dance from Section 2.

---

## 4. Recon workflow

The debug-TCP trace build is the instrument. Never guess; capture.

1. **Reference hacks first.** If fan-translation IPS files exist, run
   `analyze_reference_ips.py`: it classifies changed spans
   (text / tilemap / tile art / code) and is the treasure map. The
   Endless Duel caption hunt ended at a span that report had already
   flagged as "8192 bytes likely_snes_4bpp_tile_graphics".
2. **Capture the surface live**: drive input over TCP (the
   `validate_localization_tcp.ps1` route is the seed to copy), dump
   screenshot + VRAM + CGRAM + PPU state + OAM at the exact moment.
   Capture `off` (original) and the reference language, and capture
   *transitions* (fades, slides), not just the settled screen.
3. **Read the PPU state, don't assume**: BG mode, per-BG map base and
   char base (`bgXsc`, `bgTileAdr` — and note BG12NBA vs BG34NBA; a doc
   error about which base belonged to which BG cost a day here), main
   screen enables. Then decode map words (tile/palette/priority/flip)
   and tile art (2bpp vs 4bpp per mode).
4. **Trace bytes to their source**: search the ROM (and the per-language
   *post-patch* ROM image, reconstructable offline by replaying the
   table's semantics) for tilemap words and tile bytes, in every plane
   orientation before concluding "compressed". Raw in ROM → rom_patch.
   Not found → compressed/generated → vram_patch on the upload.
5. **Enumerate the whole surface class** before building: every record
   in the display list, every caption in the intro, every redraw
   variant. Partial coverage ships fallback-language fragments.
6. **Prove the mechanism with a throwaway POC** (one label, one
   language, visibly wrong-on-purpose like a mirrored word or a changed
   digit), screenshot it, then revert. Only then build the generator.
7. **Write findings into the issue tracker as you go** — including
   disproved theories. The expensive part of multi-session work is
   re-deriving what was already established.

### 4a. Reaching late-game text: poke, don't play

Some surfaces only draw after content the harness cannot reliably win —
Endless Duel's `battle_dialogue_3` (final-boss quotes and the post-final
conversation) and `ending_dialogue` (per-pilot epilogues) are behind a
full story-mode clear, and random-input brawling loses. Do not grind
inputs; **the debug TCP server can write WRAM**, so make the game hand
you the state:

```
write_ram <addr_hex> <hex_bytes>     # writes g_ram (WRAM, 0..0x1ffff)
zero_ram                             # clears all of WRAM
set_cpu a=.. x=.. pc=..              # registers, hex
save_state / load_state / savestate  # snapshots
dump_ram <addr_hex> <len_decimal>    # note: address hex, length DECIMAL
```

Locating the value to poke is a three-step search that needs no symbols:

1. Reach the scene and read a number the HUD shows you (here both HP
   counters read `300`). Scan all of WRAM for that 16-bit value.
2. Re-sample WRAM while the value changes on screen; keep only the
   candidates that moved the same way. (This alone leaves mirrors and
   max-value copies in the list — the game keeps several.)
3. Disambiguate by *writing*: poke one candidate at a time to a
   distinctive value and screenshot. The candidate whose number appears
   under the right-hand portrait is the opponent's.

**Then check for published cheat codes — they are the same thing, already
done.** A Pro Action Replay code *is* a per-frame WRAM freeze, so a PAR
list for the game is a ready-made map of exactly the addresses this
search is looking for, and `write_ram` in a poll loop reproduces one
exactly. (Game Genie codes are ROM patches instead — only worth mapping
if no PAR list exists.) Always verify against a live scene: code lists
are usually for the US release and this may be a different region.

For Endless Duel the search and the published list agree, and together
they show why the search alone was not enough. Two *separate* pairs of
words back the two HP readouts:

| address | effect |
|---|---|
| `$7E:1B70` / `$7E:1B74` | P1 / P2 health (the bar) |
| `$7E:1B80` / `$7E:1B84` | P1 / P2 energy (the numeric counter) |
| `$7E:060C` | round timer |

The HUD-number search finds only the second pair. Freezing just that
pair produces the memorable failure: the player loses the round with
`300` still painted on screen, because the *health* words reached zero
untouched. Freeze all four (P1 to full, P2 to 1) and every round ends
instantly in the player's favour — a single unattended run clears story
mode in about seven minutes while polling VRAM for the dialogue tilemap
signatures. Record the working codes and their source in the project's
state README so the next session does not re-derive them.

Two traps: mirrors of the value are written back from the master every
frame, so a poke that "doesn't stick" means you found a copy, not that
writing failed; and the attract demo is also a playable-looking fight —
if your boot delay is long enough for attract to start, you will capture
the demo and think you are in story mode. Check the screenshot for the
mode's own furniture (the timer, the crawl overlay) before trusting a
capture.

---

## 5. Generator requirements

Every surface ships as a generator + source file(s), never hand-edited
hex (a short-lived probe edit is the only exception). House rules,
all exercised by the six generators in `scripts/`:

- `--write` regenerates a marked section
  (`# BEGIN/END GENERATED <X> PATCHES`) in the runtime table; `--check`
  fails if the section differs. Both strict: unsupported glyph, text
  that doesn't fit its geometry, payload width ≠ source width, source
  bytes ≠ actual ROM bytes are hard errors, never silent degradation.
- Source files carry the human-editable strings and the captured
  original bytes needed to verify. Generated hex is never authoritative.
- Wire every generator into `check_localization.py` (the omission of
  the title generator went unnoticed for a while — the meta-check is
  part of the deliverable).
- Emit reviewable side artifacts: preview PNGs rendered from the
  generated hex through the real palette, and allocation tables for
  font-slot schemes.
- Where the generator restructures existing patches, it must carry the
  equivalence invariants (Section 2) inside its own `--check`.
- Idempotence: a second `--write` must be a no-op.

---

## 6. Validation methodology

- **Per-language TCP runs, screenshots + state dumps**, using the
  existing harnesses (`validate_localization_tcp.ps1`,
  `validate_localization_crawl_tcp.ps1`, the probe scripts). Cover the
  surface AND its neighbors: transitions in/out, the attract cycle, the
  intro, at least one gameplay scene.
- **Assert regressions on VRAM/OAM state, not pixels**, whenever the
  scene animates. Typewriter reveals, palette cycling, and starfield
  scroll make raw screenshot diffs useless; the OBJ tile bank or BG map
  region is timing-robust.
- **Baseline languages must be byte-identical.** "en unchanged" means a
  0-diff on the relevant VRAM/ROM regions against pre-change captures,
  not "the screenshot looks the same".
- **Attract/intro timing drifts between runs and between builds.**
  Capture sweeps at multiple seconds and identify the scene from the
  screenshot; never trust a single fixed timestamp twice.
- **The human is the verifier.** Ship nothing visual on generator
  output alone: launch side-by-side instances (one Latin, one CJK, from
  separate working directories so `mods/preloaded/state.toml` doesn't
  collide) and hand the user a concrete checklist of what to look at.
- **Verify the tested exe is the built exe** (stale-build trap: compare
  mtimes) and that the build dir's `translations/` copy is current
  (toml changes need the copy step, not a compile).
- **Bank savestates in front of every surface, and validate from those.**
  Replaying to a late-game surface costs minutes per language and is
  flaky; a `save_state` taken *just before* the text draws costs seconds
  and is deterministic. The debug server has synchronous
  `save_state <path>` / `load_state <path>` (full CPU/PPU/DMA/APU/cart/
  WRAM), and loading into a *freshly launched* process works on the
  current fiber-free engine — verified, because it was broken under the
  old fiber implementation and that trap is worth re-testing on any
  engine you port this to.

  The semantics that make this a *localization* tool: a state carries the
  VRAM of the process that saved it, but the loading process patched its
  own in-memory cart image at boot. So immediately after a load the
  screen still shows the *saving* language, and only the next **redraw**
  comes out in the *loading* language. One `en` state therefore validates
  every language — as long as it is banked *before* the draw and the
  harness waits for the redraw. Endless Duel's harness is
  `scripts/validate_from_state.ps1` (load → optionally drive → screenshot
  + PPU/CGRAM/VRAM), with the state inventory and regeneration recipe in
  `tools/validation_states/README.md`. Keep the states out of git: they
  are engine-version-fragile binaries, and only the README is durable.

  Bank them the ring-buffer way, never by arming at the moment of
  interest: drive the game while keeping a **rolling pair** of states a
  few seconds apart, and when the surface detector fires, promote the
  *older* one. You cannot save a state for an event you have already
  seen, so always be holding one from before it.

  **"Before the draw" is not early enough when the surface is staged.**
  Some screens memcpy their whole script out of the cart into WRAM when
  the *scene* loads and then draw from WRAM — Endless Duel's
  `battle_dialogue_3` copies its 30 rows to `$7F:0100+` and
  `ending_dialogue` copies its 28 to `$7E:6000+`. A state banked between
  that copy and the first character typed is already too late: the
  loading process patched its cart image, but nothing re-reads the cart,
  so the text comes out in the *saving* language forever. This looks
  exactly like "my patch didn't apply" and it is not — it reproduced on
  `it`, a language whose payload had been shipping for days.

  Two rules follow. **Diagnose it with a search, not a stare**: dump WRAM
  after the load and look for the surface's *source-language* tilemap
  words; finding them is proof of staging and tells you the staging
  address. **Key the state hunter on the staging, not on the draw**: poll
  WRAM for the row block and promote the older rolling state when the
  block appears. The draw-keyed detector is structurally late for any
  staged surface.

---

## 7. Pitfall ledger

Environment and tooling traps hit during this work. Each one wasted
real time once; none should waste it twice.

| trap | symptom | rule |
|---|---|---|
| PS 5.1 `Out-File`/`Set-Content -Encoding utf8` writes a BOM | TOML parser silently loads nothing (`rom_patches: 0` in xlate_stats) | write config files BOM-free (`git show >`, .NET `UTF8Encoding($false)`, python `newline="\n"`) |
| debug TCP `dump_vram` parses addresses as hex (`%x`) | `dump_vram 32768` → "out of range" | pass `8000`, not `32768` |
| `powershell -File` cannot bind array parameters | `Cannot convert value "a,b" to type Int32[]` | invoke scripts in-process with `&` |
| robocopy exit codes 1-7 are success | harness reports failure on a good copy | treat <8 as success |
| stale exe vs fresh commit | "fix didn't work" on an unbuilt fix | compare exe mtime to source mtimes before concluding anything |
| generated-section writers normalize boundary blank lines differently | a neighboring generator's `--check` breaks after an unrelated append | after adding a section, run every generator's `--write` then all `--check`s |
| screenshot-based diffs on animated scenes | phantom regressions / missed regressions | diff VRAM/OAM regions instead |
| mid-animation captures | "garbled" text that is actually half-typed | corroborate with the generated bytes before diagnosing corruption |
| per-language state file shared between instances | second launch flips the first instance's language on reload | separate working directory per instance |
| missing `fallback_<lang>` key | that language restores ORIGINAL (Japanese) bytes wherever it lacks a payload | generators compute chains from the table and emit explicit payloads |
| a savestate banked after the scene staged its script into WRAM | every language renders the SAVING language; reads as "the patch didn't apply" | grep WRAM for the source-language tilemap words after the load; bank states keyed on the staging, not the draw |
| relaxed N-glyph tilemap signatures | captures tagged with the wrong dialogue group ("This w" is two different rows in two different groups) | partition captures by SCREEN (PPU map/char base + box row), then decode the map to read what is actually up |
| PS `Set-Content -Encoding utf8` in the capture harness | `json.load` on a capture dies with "Expecting value: line 1 column 1" | read capture JSON with `encoding="utf-8-sig"` |
| a 64-wide BG map is two 32-wide screens | decoding at a 64-word row stride shows an empty tilemap | row stride is 0x40 **bytes** (32 words); the right half lives at +0x800 |
| a language code that collides with a source table's own key — `id` (Indonesian) vs `id = "story_mode"` | `tomllib`: "Cannot overwrite a value"; and every per-entry audit counts the identity string as an `id` translation | never name an entry-identity key after anything that could be an ISO 639-1 code; rename it (`label_id`, `record_id`) the moment you add such a language |
| a generator that appends a NEW `<lang>_hex` line at the end of a `[[rom_patch]]` block | the key lands *after* a following `# BEGIN GENERATED …` marker, i.e. inside the next generator's section, and that generator's next `--write` silently deletes it | blocks run to the next `[[rom_patch]]`, so insert new keys after the LAST existing `*_hex` line, never at block end |
| `Path.write_text(..., encoding=…)` without `newline="\n"` | one generator's `--write` rewrites the whole LF table as CRLF and every other generator's `--check` diffs | always pass `newline="\n"` when writing the runtime table |
| Pillow built without libraqm | Thai/Indic marks render as SPACING glyphs, one advance each — looks like bad authoring, isn't | check `PIL.features.check("raqm")`; if False, shape through GDI/Uniscribe (`scripts/gdi_text.py`) — do NOT lay out complex scripts in Pillow |
| a per-line vertical centring in a fixed-height band | the text bounces line to line as the tallest mark changes | derive ONE band geometry from the font and reuse it for every line; the baseline must be constant |
| a glyph atlas keyed by single codepoint | a cluster script (Thai) cannot be expressed at all — combining marks each take their own advance | either allocate per CLUSTER (option screens here) or per rendered tile (dialogue here); check for a zero-advance path before assuming an atlas can carry a script |
| the launcher font is Latin + Japanese only | a non-Latin `[[option.choice]] label` renders as tofu | keep the language label ASCII (`label = "Thai"`), as ko/zh already do, unless you extend recomp-ui's font ranges |
| assuming a font has the ASCII glyph its code implies | the option font drew `MAH.` as `MAHL` — code 0x2e's cell is not a period on these screens | render the label live before shipping punctuation; the Latin option font here is safe for A-Z and space only |

---

## 8. Porting checklist for a new game

1. Wire the engine: per-game patch table TOML + a `translation_mod.c`
   equivalent registering the activation plugin and frame callback.
   Copy Endless Duel's; it is ~70 lines and draws nothing.
2. Obtain reference translation hacks if any exist; run the IPS
   analyzer; import the reference bytes as guarded rom_patches with
   fallback chains (this alone yields the reference languages).
3. Build the game's TCP probe routes (boot → each text screen) by
   copying `validate_localization_tcp.ps1` and adjusting inputs.
4. For each text surface: classify against Section 3 (capture → decode
   → trace to ROM → POC), then build the surface's generator per
   Section 5. Order by user-visible impact; finish each surface
   completely (every record/label/variant) before the next.
5. For CJK: author glyph assets (16x16 cells via the font-render
   pipeline; 8x8/8x16 masks for small fonts), deliver via font-slot
   injection or tile-art patches; never expose a CJK language in the
   launcher while any of its surfaces still shows fallback text
   unreviewed.
6. Validate per Section 6; human sign-off per surface, one Latin + one
   CJK side by side.
7. Record every mechanism, address, formula, and disproved theory in
   the issue tracker (beads) as you go, and keep this playbook current
   when a new surface shape or trap appears.
