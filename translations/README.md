# Endless Duel Runtime Localization

`endless_duel.toml` is a runtime patch table. It patches the in-memory cartridge
image after boot and does not alter the user's ROM file.

The broader implementation pattern is documented in
`docs/TRANSLATION_TILEMAP_REFERENCE.md`; this file is the Endless Duel-specific
map of current assets, commands, and open surfaces.

## Current Coverage

- `en`: English reference patch data from Aeon Genesis.
- `es`: Spanish reference patch data from Max1323.
- `fr`, `it`, `pt`: native opening/fight crawl tilemap overlays, option-menu
  labels, and decoded battle/ending dialogue tilemap rows. Remaining title/menu
  graphics and still-undecoded reference spans fall back to English patch data.
- `ko`: exposed as a prototype. Korean has native title/menu, option-screen,
  key-config, and opening/fight crawl coverage through generated runtime
  glyph/tile patches.
- `zh`: not exposed in the launcher. Chinese remains research input.

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
encoded as BG3 2bpp tiles and validated with TCP screenshots. Chinese remains a
prototype input until its glyph set and screenshots are validated.

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
length does not match the source patch width.

The title-screen mode selector labels live in
`endless_duel_title_menu.toml`. These are `STORY MODE`, `VS. MODE`,
`TRIAL MODE`, and `OPTION` on the first selectable title menu. Their title font
is seeded from glyph masks extracted from native selected-row captures, and
`endless_duel_title_glyphs.toml` contains authored overrides for translation
letters that do not appear in the original English labels, starting with `H`
and `C`. It also supports `codepoint = "U+...."` entries for UTF-8 labels.
The checked-in Chinese and Korean title-menu strings are prototype data for
validating this external glyph path. Korean is exposed in the launcher now that
the title/options/crawl path has screenshot validation; Chinese remains hidden
until the broader CJK experience has been visually validated.

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
title labels:

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

The audit reports both pending drafts and any runtime prototype text. Korean
uses authored Hangul tiles for the current crawl prototype; the full Chinese
draft needs additional Chinese glyph tiles beyond the original Japanese crawl
set, and the compact Chinese prototype still needs a visually acceptable
tile-art pass before it can ship.

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
