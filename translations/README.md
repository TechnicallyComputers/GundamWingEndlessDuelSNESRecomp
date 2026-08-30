# Endless Duel Runtime Localization

`endless_duel.toml` is a runtime patch table. It patches the in-memory cartridge
image after boot and does not alter the user's ROM file.

## Current Coverage

- `en`: English reference patch data from Aeon Genesis.
- `es`: Spanish reference patch data from Max1323.
- `fr`, `it`, `pt`: native opening/fight crawl tilemap overlays and option-menu
  labels; other text falls back to English patch data.
- `ko`, `zh`: not exposed in the launcher yet. Korean and Chinese crawl
  translations live in `endless_duel_cjk_candidates.toml`, but they are not
  emitted into the runtime table until their tile assets pass screenshot
  validation.

English and Spanish are broader because they were imported from existing full
fan-translation ROM hacks as binary reference diffs. The runtime table applies
those byte ranges to the in-memory cartridge image after boot. For languages
without full reference hacks, we currently add mapped surfaces by hand or by
source-backed generators.

The opening crawl is encoded as a 64-tile-wide BG tilemap strip. Each visible
glyph uses a top tile entry and a bottom tile entry, with the bottom half stored
32 tile indices after the top half. The current native crawl overlays reuse the
Latin glyph set recovered from the English and Spanish references; the supported
set is intentionally limited and avoids accents, `x`, and CJK glyphs until new
font/tile assets are added. Korean and Chinese therefore still need native CJK
assets before readable non-fallback text can be rendered safely.

`scripts/generate_cjk_glyph_tiles.ps1` and
`scripts/generate_cjk_crawl_patch.py` are experimental helpers for producing
language-gated CJK crawl tile patches from authored candidate text. A 2026-08-30
TCP validation pass confirmed the generated VRAM patches applied, but the
Chinese/Korean crawl was visually unreadable in-game, so those patches are not
checked into `endless_duel.toml` as active runtime data.

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

To inspect native and fallback coverage across the generated table and the
source-backed text files, run:

```powershell
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

To audit the pending Korean and Chinese crawl drafts against the CJK/kana glyphs
identified in the original Japanese crawl, run:

```powershell
python scripts\audit_cjk_feasibility.py
```

The audit reports both pending drafts and any runtime prototype text. Korean
needs authored Hangul tiles; the full Chinese draft needs additional Chinese
glyph tiles beyond the original Japanese crawl set; the compact Chinese
prototype still needs a visually acceptable tile-art pass before it can ship.

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
  -RomPath C:\path\to\gwedj.smc -Languages off,en,es,fr,it,pt
```

This probe uses TCP input to advance to the mode menu, then writes
`mode_menu.bmp`, `ppu_state.json`, `vram.json`, `cgram.json`, `oam.json`, and a
contact HTML sheet for each language. The current evidence shows the Japanese
ROM already draws the visible `STORY MODE`, `VS. MODE`, `TRIAL MODE`, and
`OPTION` labels in English using tilemap-backed 8x8 graphics; the existing
English/Spanish reference patches and the French/Italian/Portuguese fallback
path leave those label tiles byte-identical. Translating them further will need
a dedicated menu-font/tile-asset pass, not only plain text replacement.
