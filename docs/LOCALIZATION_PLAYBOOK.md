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
