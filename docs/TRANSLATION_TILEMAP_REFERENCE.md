# SNES Runtime Translation And Tilemap Workflow

Endless Duel is the first reference implementation for runtime localization in
this SNES recomp tree. The model is deliberately close to the Tsumu Light
PlayStation project: translators edit source data, generators produce
language-gated patches, and the runtime applies them to memory without changing
the user's ROM file.

## Runtime Patch Model

`translations/endless_duel.toml` is the shipped patch table. Each patch carries
a verification source and one or more language payloads:

```toml
[[rom_patch]]
address = 0x017000
source_hex = "..."
fr_hex = "..."
it_hex = "..."
pt_hex = "..."
```

The runtime checks `source_hex` before replacing bytes. If the selected language
does not have native data for a patch, it follows root fallbacks such as
`fallback_fr = "en"`. This is why a language can be partially translated
without corrupting unrelated surfaces.

Use the patch kind that matches the asset:

- `[[rom_patch]]`: patch the in-memory cartridge image after boot. Use this for
  IPS-derived script, graphics, tilemap, or pointer bytes.
- `[[ram_patch]]` / `[[glyph_label]]`: patch WRAM labels or RAM-resident text
  only when the game stores the target surface in RAM.
- `[[vram_patch]]`: patch uploaded VRAM bytes after the game writes them. Use
  this for runtime asset injection when the ROM source is compressed, shared, or
  not worth mutating.

## Source-Backed Files

Do not hand-edit generated hex in `endless_duel.toml` except during a short
probe. Keep human-authored strings and asset descriptions in smaller source
files, then regenerate.

Current Endless Duel source files:

- `translations/endless_duel_crawl.toml`: opening/fight crawl strings.
- `translations/endless_duel_options.toml`: fixed-width option and key-config
  labels.
- `translations/endless_duel_dialogue.toml`: decoded English/Spanish reference
  dialogue tilemap rows.
- `translations/endless_duel_dialogue_targets.toml`: authored French, Italian,
  and Portuguese dialogue text.
- `translations/endless_duel_title_menu.toml`: editable rectangles and strings
  for the title-screen mode selector runtime overlay.
- `translations/endless_duel_cjk_candidates.toml`: inactive CJK research input.

The generators are intentionally strict. They fail on unsupported glyphs, text
that does not fit a known tile slot, target rows whose top/bottom tile halves do
not match, and patch payloads whose byte width differs from the source span.

Run the full local validation pass with:

```powershell
python scripts\check_localization.py
```

For a faster pass that skips generated preview files:

```powershell
python scripts\check_localization.py --skip-previews
```

## Mapping A New Surface

Use reference patches first when they exist. For Endless Duel, the English
Aeon Genesis IPS and Spanish Max1323 IPS give paired examples of what changed
from the Japanese ROM. The same approach should work for future games.

1. Parse and normalize patch files.
   `scripts/analyze_reference_ips.py` parses IPS records, applies them to an
   in-memory ROM, and writes `translations/reference_patch_map.md`.
2. Classify spans.
   Small ASCII-like ranges may be plain text. Repeating two-byte words often
   indicate tilemaps. Large medium-entropy ranges are usually compressed or raw
   tile graphics.
3. Capture the live screen.
   Use the debug TCP server to drive input, take a screenshot, and dump
   PPU/VRAM/CGRAM/OAM state at the exact frame.
4. Match the screen to data.
   Compare visible pixels, BG map words, sprite slots, and VRAM tile bytes
   against the patched ROM image.
5. Create an editable source table.
   Store strings, tile IDs, row geometry, palette assumptions, and any source
   bytes needed to verify the patch.
6. Generate language patches.
   Emit `fr_hex`, `it_hex`, `pt_hex`, etc. into the runtime table. Do not
   replace one language's source data with another language's generated data.
7. Validate visually.
   Keep screenshot or contact-sheet artifacts for the changed surface.

## Tilemap Rows

The decoded Endless Duel battle/ending dialogue is a simple SNES tilemap case.
Each line is 32 tile entries wide, with a top row followed by a bottom row. A
visible 16px glyph uses the top tile ID for the first row and `top_tile + 0x10`
for the second row. Spaces use a known blank-ish tile and trailing padding uses
blank entries.

The implementation is split this way:

- `scripts/decode_reference_tilemaps.py` recovers readable English/Spanish
  rows and the char-to-tile table from the reference IPS files.
- `translations/endless_duel_dialogue_targets.toml` stores human-authored
  French, Italian, and Portuguese strings keyed by ROM address.
- `scripts/generate_dialogue_patch.py` encodes target strings back into tilemap
  rows and updates the generated section in `endless_duel.toml`.
- `scripts/render_dialogue_previews.py` renders dependency-free SVG contact
  sheets and validates that generated rows decode back to the authored strings.

This pattern is the one to copy for future fixed-grid tilemap text.

## Tile Graphics And Asset Injection

Some surfaces are not text strings at all. The title mode menu labels
`STORY MODE`, `VS. MODE`, `TRIAL MODE`, and `OPTION` are visible English text in
the original Japanese ROM. A 2026-08-30 probe showed the current runtime patches
leave this surface byte-identical across `off`, `en`, `es`, `fr`, `it`, and
`pt`.

For these surfaces, use the least invasive replacement that can be visually
validated:

1. Capture the target screen with `scripts/probe_title_menu_vram.ps1`.
2. Identify BG/OAM ownership and tile IDs.
3. Extract or author replacement tile graphics for each language.
4. Patch the ROM-backed upload source, or add language-gated `[[vram_patch]]`
   entries when the ROM source is compressed or shared: the source-guarded
   VRAM patch IS the upload interception point.
5. Screenshot-validate every language on the exact screen.

NEVER detect screens with framebuffer pixel heuristics and never draw
replacement text over the presented frame. Pixel detection false-positives on
unrelated scenes (menu text over the intro FMV) and false-negatives on
transition animations (source-language text during fades and slides). If an
asset path is genuinely not recoverable yet, gate any interim mechanism on
WRAM game state, never on sampled pixels.

The title mode-menu labels are the worked example: unique pre-rendered 4bpp
BG1 tiles, compressed in ROM, intercepted by the generated `[[vram_patch]]`
tile-art section from `scripts/generate_title_menu_vram_patch.py` (sources:
`translations/endless_duel_title_menu.toml` strings,
`translations/endless_duel_title_glyphs.toml` glyph masks,
`translations/endless_duel_title_menu_assets.toml` captured stock art).
Because selection highlighting is CGRAM-only, one art set per language
inherits selection behaviour, and the labels stay translated through every
fade/slide animation. The preview renderer
(`scripts/render_title_menu_overlay_preview.py`) writes ignored BMPs under
`translations/title_menu_previews/` for quick fit checks.

This is also the path for CJK. If the game has no Chinese/Korean glyph tiles,
do not expose the language as a fallback-only option. Add real glyph assets,
map strings to those assets, and validate screenshots first. The title glyph
override format accepts single-byte `char = "H"` entries and Unicode
`codepoint = "U+5267"` entries with 1-16 pixel masks. Run
`scripts/generate_title_glyphs.py --check` to verify generated Chinese/Korean
title and option glyphs remain in sync with
`translations/endless_duel_title_menu.toml` and
`translations/endless_duel_option_menu.toml`.

The attract crawl has two paths:

- Latin languages use `translations/endless_duel_crawl.toml` and
  `scripts/generate_crawl_patch.py`.
- Chinese and Korean use compact 16x16 external glyph tiles from
  `translations/endless_duel_cjk_candidates.toml` and
  `translations/endless_duel_cjk_glyphs.toml`, emitted by
  `scripts/generate_cjk_glyph_tiles.ps1` and
  `scripts/generate_cjk_crawl_patch.py`.

The CJK crawl glyph generator preserves original VRAM source bytes by tile so
asset regeneration does not invalidate runtime `[[vram_patch]]` guards.

## Visual Validation

Representative screenshots are part of the acceptance criteria, not a nice-to
have. Use visible mode when launching probes for manual observation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\validate_localization_tcp.ps1 `
  -RomPath C:\path\to\gwedj.smc -Languages fr,it,pt,zh,ko -Visible

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\probe_title_menu_vram.ps1 `
  -RomPath C:\path\to\gwedj.smc -Languages off,en,es,fr,it,pt,zh,ko -Visible

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\validate_localization_crawl_tcp.ps1 `
  -RomPath C:\path\to\gwedj.smc -Languages fr,it,pt,zh,ko -Turbo -TurboFrames 8
```

The capture bundles are local derived artifacts and are intentionally ignored by
Git. Commit source data, generators, docs, and runtime TOML; do not commit ROMs,
VRAM dumps, or generated tile sheets. Curated review screenshots may live under
`docs/assets/` when they are deliberately selected for documentation or a PR,
but do not commit bulk validation captures.
