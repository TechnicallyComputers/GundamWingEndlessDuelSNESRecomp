# Endless Duel Runtime Localization

`endless_duel.toml` is a runtime patch table. It patches the in-memory cartridge
image after boot and does not alter the user's ROM file.

## Current Coverage

- `en`: English reference patch data from Aeon Genesis.
- `es`: Spanish reference patch data from Max1323.
- `fr`, `it`, `pt`: native opening/fight crawl tilemap overlays and option-menu
  labels; other text falls back to English patch data.
- `ko`, `zh`: selectable, currently fall back to English patch data. Draft
  crawl translations live in `endless_duel_cjk_candidates.toml`, but they are
  not emitted until native tile assets exist.

The opening crawl is encoded as a 64-tile-wide BG tilemap strip. Each visible
glyph uses a top tile entry and a bottom tile entry, with the bottom half stored
32 tile indices after the top half. The current native crawl overlays reuse the
Latin glyph set recovered from the English and Spanish references; the supported
set is intentionally limited and avoids accents, `x`, and CJK glyphs until new
font/tile assets are added. Korean and Chinese therefore still need native CJK
assets before readable non-fallback text can be rendered safely.

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

To audit the pending Korean and Chinese crawl drafts against the CJK/kana glyphs
identified in the original Japanese crawl, run:

```powershell
python scripts\audit_cjk_feasibility.py
```

The current audit shows Korean needs authored Hangul tiles and the Chinese draft
needs additional Chinese glyph tiles beyond the original Japanese crawl set.

## Visual QA Harness

Use `scripts/validate_localization_tcp.ps1` with a trace build to verify runtime
activation and capture TCP-driven screenshots:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\validate_localization_tcp.ps1 `
  -RomPath C:\path\to\gwedj.smc -Languages en,es,fr,it,pt,ko,zh
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
replacement work; the existing Korean and Chinese selections are fallback-only
because no native Hangul/Chinese tile assets are currently authored.

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
