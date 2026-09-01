# Endless Duel Runtime Localization

`endless_duel.toml` is a runtime patch table. It patches the in-memory cartridge
image after boot and does not alter the user's ROM file.

The reproducible end-to-end process for porting this to other games is
docs/LOCALIZATION_PLAYBOOK.md.
The broader implementation pattern is documented in
`docs/TRANSLATION_TILEMAP_REFERENCE.md`; this file is the Endless Duel-specific
map of current assets, commands, and open surfaces.

## Current Coverage

- `en`: English reference patch data from Aeon Genesis.
- `es`: Spanish reference patch data from Max1323.
- `fr`, `it`: native throughout — title mode-selector labels, option-menu and
  key-config labels, opening/fight crawl, intro caption sprite art, and all 162
  battle/ending dialogue rows (with accented glyph cells added to the dialogue
  font by `generate_dialogue_accent_patch.py`).
- `pt`: native crawl, menus, and intro caption; 109 of the 162 dialogue rows are
  authored, the rest fall back to English.
- `ko`, `zh`: both exposed in the launcher, both native for the title menu,
  option menu, key config, opening/fight crawl, intro caption, and the
  battle dialogue, the post-final Treize conversation and the per-pilot
  epilogues — all six dialogue groups. Dialogue is delivered as per-quote 16x16
  glyph pages written into free VRAM tiles behind byte-exact BG3 tilemap
  guards — see `endless_duel_dialogue_cjk.toml` and
  `generate_dialogue_cjk_patch.py`. 95 quotes / 161 rows per language across
  three surfaces:

  | surface | groups | map base | 1st text row | guard | char base | word OR | page window |
  |---|---|---|---|---|---|---|---|
  | `battle_quote` | `battle_dialogue_0/1/2/4` | 0xc000 | 22 | 0xc584 | 0x4000 | 0x2000 | 0x300-0x3ff |
  | `final_conversation` | `battle_dialogue_3` | 0xf000 | 22 | 0xf584 | 0xc000 | 0x2000 | 0x07a-0x0ff |
  | `ending` | `ending_dialogue` | 0xf000 | 21 | 0xf544 | 0xc000 | 0x2400 | 0x07a-0x0ff |

  The two 0xc000-based screens are tile-starved by construction: their BG1/BG2/
  BG3 maps fill 0xd000-0xffff, so only ids 0x000-0x0ff have tile data at all and
  0x000-0x07f is the Latin font. The free-tile survey (unreferenced by the BG3
  map on that screen across every capture of it, and byte-stable across them)
  found 178 free ids on `final_conversation` and 213 on `ending`; both take the
  common contiguous run 0x07a-0x0ff, 134 tiles. Peak page use is 0x0c5 (ko
  `ending`), leaving 58 tiles of headroom on the tightest surface.

Every surface is drawn by the game itself; nothing is drawn over the presented
frame.

English and Spanish are broader because they were imported from existing full
fan-translation ROM hacks as binary reference diffs. The runtime table applies
those byte ranges to the in-memory cartridge image after boot. For languages
without full reference hacks, we currently add mapped surfaces by hand or by
source-backed generators.

The opening crawl is encoded as a 64-tile-wide BG tilemap strip. Latin glyphs
use a top tile entry and a bottom tile entry, with the bottom half stored 32
tile indices after the top half. The Latin crawl overlays reuse the glyph set
recovered from the English and Spanish references; the supported Latin set is
intentionally limited and avoids accents and `x` until new font/tile assets are
added.

`scripts/generate_cjk_glyph_tiles.ps1` and
`scripts/generate_cjk_crawl_patch.py` produce language-gated CJK crawl tile
patches from authored candidate text. Korean uses authored 16x16 Hangul cells
encoded as BG3 2bpp tiles and validated with TCP screenshots. Chinese uses the
same compact CJK crawl path with authored Han cells; both are generated,
screenshot-validated, and shipping.

## Adding Native Language Data

Add per-patch fields such as `fr_hex`, `it_hex`, `pt_hex`, `ko_hex`, or `zh_hex`
to the existing `[[rom_patch]]`, `[[ram_patch]]`, or `[[vram_patch]]` entries.
The runtime tries the selected language first and then follows the root-level
`fallback_<lang>` mapping. A native entry therefore overrides the English
fallback one patch at a time.

For tile-art text such as the opening crawl, first add or identify a complete
glyph sheet for the target language, then generate the same tilemap format the
game uploads to VRAM. Do not add native overlay bytes without screenshot
validation; arbitrary Latin letter codes can render as unrelated pixels in that
layer.

The current Latin crawl text for English, Spanish, French, Italian, and
Portuguese lives in `endless_duel_crawl.toml`. Run the generator after editing
those lines:

```powershell
python scripts\generate_crawl_patch.py --write
python scripts\generate_crawl_patch.py --check
```

The generator fails on unsupported glyphs or lines that do not fit their
validated tilemap slots.

The generated aggregate crawl patch runs after earlier English/Spanish
reference-patch chunks at the same ROM region. Its `source_hex` is therefore the
post-reference English bytes that are present at that point in patch order, not
the original compressed Japanese bytes. Preserve that compatibility source when
editing the source-backed crawl text; otherwise the later native language
overlay cannot match and the runtime will fall back to the earlier English
reference chunks.

The fixed-width option-menu and key-config labels live in
`endless_duel_options.toml`. They are plain ASCII text entries that generate the
matching byte patches in `endless_duel.toml`:

```powershell
python scripts\generate_option_patch.py --write
python scripts\generate_option_patch.py --check
```

The option generator fails on non-ASCII text or any translation whose byte
length does not match the source patch width. It covers `es`, `fr`, `it`, `pt`
only: those entries patch sub-spans of a record and are deliberately inert for
`ko`/`zh`, which replace whole records instead (below).

### Native CJK option / key-config text (font-slot injection)

Both option screens are drawn from a display list in ROM at
`0x00c000-0x00c800`. Its opcodes are 16-bit: `0x000c` sets column/row, `0x0006`
sets the attribute mask, `0x0002` prints font codes until a `0xff` terminator,
`0x0018` prints a variable, and `0x0004` ends the list. There are 44 print
records; the translated ones (28) live in `endless_duel_option_text.toml`, keyed
by the address of the record's first string byte, with the exact stock `source`
text and `cursor = true` for records whose first byte is the `0x09` selection
cursor.

The screens use a BG2 Mode 0 2bpp font at char base `0x0000`, 16 bytes per tile.
A character code `c` in `0x20-0x9f` draws an 8x16 cell from two stacked tiles:

```
top    = 2 * (c & 0xf0) - 0x40 + (c & 0x0f)
bottom = top + 0x10
```

Codes `0x60-0x9f` are free: their tiles `0x80-0xff` (VRAM `0x0800-0x0fff`) are
all-zero on these screens and no record references them. Because the font is
generated rather than stored raw in ROM, the glyph art ships as source-guarded
`[[vram_patch]]` entries over that all-zero region -- a real guard, since the
same VRAM is non-zero on the title screen, so the patches no-op everywhere else.

`scripts/generate_option_cjk_patch.py` allocates one free code per distinct
character per language over the sorted charset, packs the 8x8 masks from
`endless_duel_title_glyphs.toml` vertically centred into the 8x16 cell, and
emits both the `[[rom_patch]]` record bytes and the `[[vram_patch]]` font tiles,
plus a code allocation table. Source bytes are verified against the ROM at the
repo root at generation time.

```powershell
py -3 scripts\generate_option_cjk_patch.py --write
py -3 scripts\generate_option_cjk_patch.py --check
```

The title-screen mode selector labels live in
`endless_duel_title_menu.toml`. These are `STORY MODE`, `VS. MODE`,
`TRIAL MODE`, and `OPTION` on the first selectable title menu. Their title font
is seeded from glyph masks extracted from native selected-row captures, and
`endless_duel_title_glyphs.toml` contains authored overrides for translation
letters that do not appear in the original English labels, starting with `H`
and `C`. It also supports `codepoint = "U+...."` entries for UTF-8 labels.
The checked-in Chinese and Korean title-menu strings drive this external glyph
path. Both languages are exposed in the launcher; the title, options,
key-config, crawl, caption, and dialogue surfaces all have screenshot
validation.

The live replacement is SNES-level asset interception, not host drawing: the
labels are unique, consecutive, pre-rendered 4bpp BG1 tiles (map rows 17-22,
cols 10-21, palette 6) whose ROM source is compressed, so generated
`[[vram_patch]]` entries intercept the game's own tile upload and swap in
translated glyph art. Selection highlighting is CGRAM-only (each label owns one
color index: STORY=14, VS=13, TRIAL=12, OPTION=11), so one art set per language
inherits selection behaviour, and the labels stay translated through every
fade/slide animation because the game's own tilemap references the patched
tiles. The original captured art lives in
`endless_duel_title_menu_assets.toml`; regenerate the runtime patches with:

```powershell
py -3 scripts\generate_title_menu_vram_patch.py --check
py -3 scripts\generate_title_menu_vram_patch.py --write
```

To preview rectangle fit against a captured frame without launching the game:

```powershell
py -3 scripts\render_title_menu_overlay_preview.py `
  C:\path\to\title-menu-capture\en --langs es,fr,it,pt
```

Regenerate the authored/generated title glyph asset after changing non-ASCII
title-menu labels or option records (it is the shared mask source for both the
title tile art and the option font slots):

```powershell
py -3 scripts\generate_title_glyphs.py --check
py -3 scripts\generate_title_glyphs.py
```

The generated BMPs are ignored under `translations/title_menu_previews/`. They
use the same string and title glyph sources as the tile-art generator, but they
are review previews only; the actual in-game path is the generated
`[[vram_patch]]` tile-art section in `endless_duel.toml`.

The battle and ending dialogue decoded from the English and Spanish IPS files
lives in `endless_duel_dialogue.toml`, with a readable audit report in
`reference_dialogue_decode.md`. The authored French, Italian, and Portuguese
target strings live separately in `endless_duel_dialogue_targets.toml` so the
decoder can be regenerated without overwriting human translation work. These
lines are tilemaps, not script text: each line is a 32-tile top row followed by
a 32-tile bottom row, and each visible 16px glyph is encoded as `top_tile` plus
`top_tile + 0x10`.

Regenerate the dialogue source and the language-gated runtime patch section
after editing the decoder or adding `fr`, `it`, or `pt` strings:

```powershell
python scripts\decode_reference_tilemaps.py `
  --en-ips C:\path\to\GUNDAM-W.IPS `
  --es-ips C:\path\to\Shin Kidou Senki Gundam W - Endless Duel v1.0 (S).ips `
  --write --allow-mismatch
python scripts\generate_dialogue_patch.py --write
python scripts\generate_dialogue_patch.py
```

The dialogue generator currently supports the recovered Latin tile set:
`A-Z`, `a-z`, space, `.`, `,`, `'`, `?`, `!`, inverted question mark, and
inverted exclamation mark. It intentionally fails on unsupported glyphs rather
than emitting bytes that would draw unrelated tiles. Two Spanish reference rows
currently report top/bottom tile mismatches, `0x017e00` and `0x027580`; treat
them as visual-QA targets before using those rows as authoritative translation
examples.

To render dependency-free SVG contact sheets for the generated dialogue rows,
run:

```powershell
python scripts\render_dialogue_previews.py
```

The preview renderer re-encodes each target string, decodes the generated tile
row back through the recovered charmap, verifies the top/bottom glyph halves,
and writes ignored review artifacts under `translations/dialogue_previews/`.

To inspect native and fallback coverage across the generated table and the
source-backed text files, run:

```powershell
python scripts\check_localization.py
python scripts\audit_localization_coverage.py
```

This reports runtime patch counts, patched byte counts, root fallback mappings,
and per-language editable crawl/option entries.

To inspect the English/Spanish reference patches as a worklist for expanding
French, Italian, and Portuguese, run:

```powershell
python scripts\audit_reference_patch_map.py
```

The highest-value spans are the `reference_diff_unmapped` ranges: English and
Spanish differ there, but the current table has no French/Italian/Portuguese
override. Those ranges are the next places to decode into text, tilemaps, or
external-rendered overlays.

For a lower-level pass against the original fan-patch IPS files, extract the
English and Spanish archives and run:

```powershell
python scripts\analyze_reference_ips.py `
  --en-ips C:\path\to\GUNDAM-W.IPS `
  --es-ips C:\path\to\Shin Kidou Senki Gundam W - Endless Duel v1.0 (S).ips
```

The English IPS was authored for a copier-headered ROM, so the analyzer
normalizes its offsets by `-0x200` before comparing it to the headerless ROM.
It writes `translations/reference_patch_map.md`, classifying the merged
reference spans as likely code/pointer hooks, tile graphics/font assets, or
tilemap-backed text layouts. The current map shows the remaining unmapped
full-translation surfaces are mainly:

- `0x017000-0x017fff`, `0x026b00-0x027aff`, `0x03eb00-0x03faff`, and
  `0x05e000-0x05efff`: likely tilemap/text-layout pages.
- `0x006f00-0x007eff`, `0x00e000-0x00ffff`, and `0x015400-0x016843`: likely
  tile graphics, title/menu art, or font assets.

To visually inspect those likely graphics ranges, run:

```powershell
python scripts\render_snes_4bpp_tiles.py `
  --en-ips C:\path\to\GUNDAM-W.IPS `
  --es-ips C:\path\to\Shin Kidou Senki Gundam W - Endless Duel v1.0 (S).ips
```

That emits local BMP tile sheets under `translations/reference_tiles/`. The
files are ignored by Git because they are derived from patched ROM bytes.

To render captured PPU/VRAM/CGRAM bundles back into per-BG screenshots without
relaunching the game, use:

```powershell
python scripts\render_vram_bg_capture.py C:\path\to\capture\es --bg 1
python scripts\render_vram_bg_capture.py C:\path\to\capture\es --bg 2
python scripts\render_vram_bg_capture.py C:\path\to\capture\es --bg 3
```

This confirmed the title/menu capture uses Mode 1, BG1 map base `0xd000`, BG2
map base `0xe000`, BG3 map base `0xf000`, BG1/BG2 tile base `0x0000`, and BG3
tile base `0xc000` (BG12NBA=0x00, BG34NBA=0x66; an earlier revision of this
file wrongly claimed BG1/BG2 tile base `0xc000`).
The visible mode labels are translated by intercepting the game's own VRAM
tile upload with the generated `[[vram_patch]]` tile-art section (see the
title-menu section above); no host-side drawing is involved. The ROM-side
upload source remains unrecovered (compressed), which is fine: the VRAM
guard IS the interception point.

## External Reverse-Engineering Context

The public reference pages are useful context, but not precise enough to drive
runtime patching alone:

- Aeon Genesis project page:
  `https://aeongenesis.net/projects/gwed`
- English reference patch page:
  `https://www.romhacking.net/translations/570/`
- Spanish reference patch page:
  `https://www.romhacking.net/translations/4023/`

Aeon Genesis notes that the translation was easy only after the game-specific
hacking method was understood. The recovered IPS bytes explain why: much of the
English/Spanish text is not ordinary ASCII script data. It is pre-rendered SNES
tilemap and tile-art data, which is normally investigated with a hex editor,
table/charmap notes, tile editors such as YY-CHR or Tile Molester, and emulator
debuggers with tile/tilemap/VRAM viewers such as Mesen-S. In this project, the
same workflow is represented by scripts that parse IPS files, render tile
sheets, decode tilemap rows, and render captured VRAM state.

To audit the pending Korean and Chinese crawl drafts against the CJK/kana glyphs
identified in the original Japanese crawl, run:

```powershell
python scripts\audit_cjk_feasibility.py
```

The audit reports both pending drafts and any runtime text still missing a
native payload. Korean uses authored Hangul tiles and Chinese authored Han
tiles; both ship through the compact CJK crawl generator, and the three
dialogue surfaces use the separate per-quote glyph pager
(`scripts/generate_dialogue_cjk_patch.py`; `--stats` prints the per-surface
page and guard budget, `--previews` renders each quote through that surface's
own CGRAM palette row).

## Visual QA Harness

Use `scripts/validate_localization_tcp.ps1` with a trace build to verify runtime
activation and capture TCP-driven screenshots:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\validate_localization_tcp.ps1 `
  -RomPath C:\path\to\gwedj.smc -Languages en,es,fr,it,pt
```

The harness writes per-language screenshots, `xlate_stats.json`, `contact.html`,
and `contact.png` when `System.Drawing` is available. It uses `set_controller`
over the debug TCP port to skip through the intro, title, menu, VS route, option
menu, option value changes, and key-config route, so spacing or tile corruption
can be checked visually instead of inferred from patch counts.

For the scrolling opening/fight crawl specifically, capture fixed-time frames:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\validate_localization_crawl_tcp.ps1 `
  -RomPath C:\path\to\gwedj.smc -Languages en,es,fr,it,pt -CaptureSeconds 55,60
```

The crawl harness also emits `contact.png` when `System.Drawing` is available,
which is the easiest artifact to inspect or attach to review notes.

To inspect the VRAM/PPU state behind a crawl frame, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\probe_crawl_vram.ps1 `
  -RomPath C:\path\to\gwedj.smc -Languages en,off -CaptureSecond 60
```

This writes `crawl.bmp`, `xlate_stats.json`, `ppu_state.json`, `vram.json`, and
`cgram.json` for each language. Use it before attempting CJK glyph-tile
replacement work.

For remaining title/menu label investigation, capture the visible mode menu and
the exact PPU/VRAM/CGRAM/OAM state used to draw it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\probe_title_menu_vram.ps1 `
  -RomPath C:\path\to\gwedj.smc -Languages off,en,es,fr,it,pt -Visible
```

This probe uses TCP input to advance to the mode menu, then writes
`mode_menu.bmp`, `ppu_state.json`, `vram.json`, `cgram.json`, `oam.json`, and a
contact HTML sheet for each language. Render the captured BG or OBJ layers with:

```powershell
python scripts\render_vram_bg_capture.py C:\path\to\capture\en --bg 1
python scripts\render_oam_capture.py C:\path\to\capture\en
```

The current evidence shows the Japanese ROM already draws the visible
`STORY MODE`, `VS. MODE`, `TRIAL MODE`, and `OPTION` labels in English. A
2026-08-30 visible probe confirmed those labels are byte-identical across
`off`, `en`, `es`, `fr`, `it`, and `pt`; the active runtime table does not touch
this surface. On that capture the mode-menu screen is Mode 1, BG1 map base
`0xd000`, BG2 map base `0xe000`, BG3 map base `0xf000`, BG1/BG2 tile base
`0xc000`, and OBJ base `0x00000`. The selectable label area lines up with BG1
map rows 17-22 and tile-art entries such as `0x3a70-0x3aaa`. Translating these
labels further will need a dedicated menu-font/tile-asset pass, not only plain
text replacement.
