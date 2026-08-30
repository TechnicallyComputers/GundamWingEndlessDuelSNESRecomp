# Endless Duel Runtime Localization

`endless_duel.toml` is a runtime patch table. It patches the in-memory cartridge
image after boot and does not alter the user's ROM file.

## Current Coverage

- `en`: English reference patch data from Aeon Genesis.
- `es`: Spanish reference patch data from Max1323.
- `fr`, `it`, `pt`: selectable, with a native overlay for the first intro
  line and English fallback for all other text.
- `ko`, `zh`: selectable, currently fall back to English patch data.

The Korean and Chinese entries also need new glyph/tile work before native text
can be rendered. The current table can encode text through `[[glyph]]` mappings,
but this project does not yet contain a CJK font sheet or table entries.

## Adding Native Language Data

Add per-patch fields such as `fr_hex`, `it_hex`, `pt_hex`, `ko_hex`, or `zh_hex`
to the existing `[[rom_patch]]`, `[[ram_patch]]`, or `[[vram_patch]]` entries.
The runtime tries the selected language first and then follows the root-level
`fallback_<lang>` mapping. A native entry therefore overrides the English
fallback one patch at a time.

The current French/Italian/Portuguese overlay demonstrates that model by using
the English-patched intro bytes as `source_hex` and replacing them after the
base English fallback has been applied.
