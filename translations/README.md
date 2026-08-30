# Endless Duel Runtime Localization

`endless_duel.toml` is a runtime patch table. It patches the in-memory cartridge
image after boot and does not alter the user's ROM file.

## Current Coverage

- `en`: English reference patch data from Aeon Genesis.
- `es`: Spanish reference patch data from Max1323.
- `fr`, `it`, `pt`: selectable, currently fall back to English patch data.
- `ko`, `zh`: selectable, currently fall back to English patch data.

French, Italian, Portuguese, Korean, and Chinese need new glyph/tile work before
native text can be rendered safely. Live VRAM captures of the visible opening
crawl show that its bytes are rendered tile/bitmap data, not reusable character
IDs. Runtime experiments with direct Latin byte substitutions produced corrupted
glyphs in screenshots. The current table can encode text through `[[glyph]]`
mappings, but this project does not yet contain the tile/bitmap encoder or font
assets needed for these languages.

## Adding Native Language Data

Add per-patch fields such as `fr_hex`, `it_hex`, `pt_hex`, `ko_hex`, or `zh_hex`
to the existing `[[rom_patch]]`, `[[ram_patch]]`, or `[[vram_patch]]` entries.
The runtime tries the selected language first and then follows the root-level
`fallback_<lang>` mapping. A native entry therefore overrides the English
fallback one patch at a time.

For tile-art text such as the opening crawl, first add or identify a complete
glyph sheet for the target language, then generate the same tile/bitmap format
the game uploads to VRAM. Do not add native overlay bytes without screenshot
validation; arbitrary Latin letter codes can render as unrelated pixels in that
layer.

## Visual QA Harness

Use `scripts/validate_localization_tcp.ps1` with a trace build to verify runtime
activation and capture TCP-driven screenshots:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\validate_localization_tcp.ps1 `
  -RomPath C:\path\to\gwedj.smc -Languages en,es,fr,it,pt,ko,zh
```

The harness writes per-language screenshots, `xlate_stats.json`, and a
`contact.html` sheet. It uses `set_controller` over the debug TCP port to skip
through the intro, title, menu, and VS route, so spacing or tile corruption can
be checked visually instead of inferred from patch counts.
