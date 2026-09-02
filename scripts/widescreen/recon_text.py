#!/usr/bin/env py -3
"""R7 — GWED text-surface taxonomy (Beads beads-8wg.9.13.3).

For every place the game puts words on screen: which layer carries it, which
map, which class of surface it is, and therefore what the widescreen policy
must be.  The two policies that matter:

  Bounded       the whole screen is pillarboxed (PpuSetExtraSpaceCentered +
                LayerClamp(0x0F)).  No text can stretch or slice, by
                construction.  Every non-fight screen is Bounded.
  BandClamp     the screen is a live fight, so world layers extend into the
                margins — but this particular surface must be clamped to the
                authentic 256 columns over a named scanline band, or its
                glyphs get sliced/duplicated by the margin sampling.

The layer/line evidence is recomputed here from the captured bundles: for each
scene and layer it walks the per-line PPU journal, resolves the displayed map
row for every scanline, and reports the rows that actually hold non-zero
tilemap entries.  That is what turns "BG3 is enabled" into "BG3 shows rows 0-6
on lines 170-223 and nothing else".
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import recon_tilemap as T  # noqa: E402
import ws_recon as R  # noqa: E402

SCENES = ["attract_fight", "ko_1p_win", "victory_quote", "title_logo",
          "title_menu", "attract_crawl", "attract_cinematic", "final_convo",
          "ending", "black_transition"]

# (\$7E:1000, \$7E:1004) per scene, from recon pass A's gate.json.
GATE = {
    "attract_fight": ["0x0010", "0x0012"],
    "ko_1p_win": ["0x0010", "0x001e"],
    "victory_quote": ["0x0010", "0x0014"],
    "title_logo": ["0x000a", "0x000c"],
    "title_menu": ["0x000a", "0x000c"],
    "attract_crawl": ["0x0002", "0x000a"],
    "attract_cinematic": ["0x0008", "0x0000"],
    "final_convo": ["0x0010", "0x001e"],
    "ending": ["0x0010", "0x001e"],
    "black_transition": [None, None],
}


def scene_layers(scene: str) -> dict:
    v, cg, summ, lines = T.load_scene(scene)
    out = {}
    for L in summ["layers"]:
        rows = T.bg3_live_rows(v, L, lines)
        live = [r for r in rows if r["nonzero_cols_0_31"]
                or r["nonzero_cols_32_63"]]
        out["bg%d" % L["layer"]] = dict(
            map_base=L["map_base_byte"], char_base=L["char_base_byte"],
            size=L["screen_size"], hscroll=L["hscroll"], vscroll=L["vscroll"],
            on_main=L["main"], on_sub=L["sub"],
            live_map_rows=[r["map_row"] for r in live],
            live_scanlines=([min(r["lines"][0] for r in live),
                             max(r["lines"][1] for r in live)]
                            if live else None))
    out["_tm"] = summ["screenEnabled"]
    out["_bgmode"] = summ["bgmode"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    inv = {}
    for sc in SCENES:
        try:
            inv[sc] = scene_layers(sc)
        except Exception as ex:                      # noqa: BLE001
            inv[sc] = dict(error=str(ex))

    def ev(scene, layer):
        d = inv.get(scene, {}).get(layer, {})
        return dict(scene=scene, layer=layer, map_base=d.get("map_base"),
                    char_base=d.get("char_base"),
                    live_map_rows=d.get("live_map_rows"),
                    live_scanlines=d.get("live_scanlines"))

    surfaces = [
        dict(surface="title_logo_lettering",
             scenes=["title_logo", "title_menu"], layer="BG1",
             layer_index=0, map_base="$D000", char_base="$00000",
             cls="bg_tilemap_text", policy="Bounded",
             band=None,
             note="'Mobile Suit GUNDAM / ENDLESS DUEL' lettering. Whole "
                  "screen pillarboxed, so no per-band work.",
             evidence=[ev("title_logo", "bg1"), ev("title_menu", "bg1")]),
        dict(surface="mode_menu_labels",
             scenes=["title_menu"], layer="BG1", layer_index=0,
             map_base="$D000", char_base="$00000",
             cls="bg_tilemap_text", policy="Bounded", band=None,
             note="STORY / VS / TRIAL / OPTION plus the copyright line; same "
                  "BG1 tilemap as the logo. The options and key-config menus "
                  "are NOT captured (owner-only) and are assumed to share "
                  "this surface and the Bounded policy - flag for the owner.",
             evidence=[ev("title_menu", "bg1")]),
        dict(surface="attract_story_caption",
             scenes=["attract_crawl"], layer="OBJ", layer_index=4,
             map_base=None, char_base=None,
             cls="obj_caption_art", policy="Bounded", band=None,
             note="recon pass A: the 'After Colony 195' caption on the crawl "
                  "screen is drawn with SPRITES, not a tilemap. Bounded "
                  "pillarboxing covers it; do NOT feed those slots to the "
                  "HUD OAM shifter.",
             evidence=[ev("attract_crawl", "bg2"),
                       ev("attract_crawl", "bg3")]),
        dict(surface="attract_bottom_banner",
             scenes=["attract_fight"], layer="BG3", layer_index=2,
             map_base="$C000", char_base="$06000",
             cls="bg_tilemap_text_2bpp", policy="BandClamp",
             band=[170, 224],
             note="THE ONE TEXT SURFACE THAT SHARES THE FIGHT SCREEN. The "
                  "attract story line ('Like shooting stars, five...') "
                  "scrolls along the bottom of the live attract fight on "
                  "BG3 map rows 0-6, scanlines 170-223, cols 0-31 (256 px "
                  "wide). BG3 has NO world content at all during a fight, so "
                  "the cheapest correct policy is to clamp BG3 for the whole "
                  "fight screen (LayerClamp bit 2), and the band is recorded "
                  "only in case a later stage puts world art on BG3.",
             evidence=[ev("attract_fight", "bg3")]),
        dict(surface="ko_round_win_banner",
             scenes=["ko_1p_win"], layer="BG3", layer_index=2,
             map_base="$C000", char_base="$06000",
             cls="bg_tilemap_text_2bpp", policy="BandClamp",
             band=[88, 136],
             note="the '1P WIN' banner, BG3 map rows 5-10 on scanlines "
                  "88-135, cols 0-18. Same layer and char base as the attract "
                  "banner, a different vScroll (976 vs 854). The round-end "
                  "screen still shows the fight HUD and the arena, so it is "
                  "NOT a Bounded screen: clamping BG3 is required, not "
                  "optional.",
             evidence=[ev("ko_1p_win", "bg3")]),
        dict(surface="victory_quote_text",
             scenes=["victory_quote"], layer="BG3 (inferred)",
             layer_index=2, map_base="$C000", char_base="$06000",
             cls="bg_tilemap_text_2bpp", policy="BandClamp",
             band=None,
             confidence="INFERRED - NOT MEASURED",
             note="UNRESOLVED. Measured: on the pre_quote state the quote "
                  "screen renders BG1 = HUD band only, BG2 = the pilot "
                  "portrait, BG3 = enabled with an entirely empty tilemap, "
                  "OBJ = the mobile suit and a flame effect, and it STAYS "
                  "that way - a 1700-frame inputless free-run from the state "
                  "never produced quote text (the box waits for a button, and "
                  "this probe does not inject input). BG3 is the only "
                  "text-capable enabled surface and it is idle, so the quote "
                  "text almost certainly lands there; the band is unknown. "
                  "Needs an owner-recorded state with the quote box up. "
                  "Until then treat the quote screen as BG3-clamped (safe "
                  "either way: BG3 carries nothing else there).",
             evidence=[ev("victory_quote", "bg1"), ev("victory_quote", "bg2"),
                       ev("victory_quote", "bg3")]),
        dict(surface="stage_dialogue_box",
             scenes=["final_convo", "pre_stage_battle_dialogue_3",
                     "pre_stage_ending_dialogue"],
             layer="BG3", layer_index=2, map_base="$F000",
             char_base="$0C000",
             cls="bg_tilemap_text_2bpp", policy="Bounded", band=None,
             note="the inter-stage / battle dialogue window frame and its "
                  "text - the surface the existing localization mod already "
                  "patches. On final_convo it occupies BG3 map rows 2-13 on "
                  "scanlines 16-215 over a portrait (BG1) and machinery "
                  "(BG2), and NO fight HUD: it is a full-screen scripted "
                  "screen, so Bounded. NOTE the different map ($F000) and "
                  "char base ($0C000) from the fight-screen banners - the "
                  "policy table must key off the WRAM gate, not off BG3's "
                  "registers.",
             evidence=[ev("final_convo", "bg3")]),
        dict(surface="final_conversation_text",
             scenes=["final_convo"], layer="BG3", layer_index=2,
             map_base="$F000", char_base="$0C000",
             cls="bg_tilemap_text_2bpp", policy="Bounded", band=None,
             note="the post-final Treize conversation is the same surface as "
                  "stage_dialogue_box; listed separately because the plan "
                  "names it separately.",
             evidence=[ev("final_convo", "bg3")]),
        dict(surface="ending_captions",
             scenes=["ending"], layer="none observed", layer_index=None,
             map_base=None, char_base=None,
             cls="unknown", policy="Bounded",
             band=None,
             confidence="NOT OBSERVED",
             note="on the pre_ending state the epilogue frame has BG1, BG3 "
                  "and BG4 all empty and only BG2 live (full-screen mecha "
                  "art), TM 0x02. No caption text is on screen at that "
                  "moment. If the epilogue captions appear later they will "
                  "be on BG3 like every other text surface, and the screen "
                  "is Bounded either way, so this is not a risk - it just "
                  "is not measured.",
             evidence=[ev("ending", "bg1"), ev("ending", "bg2"),
                       ev("ending", "bg3")]),
        dict(surface="hud_names_and_time_label",
             scenes=["attract_fight", "ko_1p_win", "victory_quote"],
             layer="BG1", layer_index=0, map_base="$D000",
             char_base="$00000",
             cls="bg_tilemap_text_in_fixed_cells", policy="AnchorBand",
             band=[24, 72],
             note="the P1/P2 name plates, the TIME label, the timer digits "
                  "and the energy counters. Glyphs are blitted into fixed "
                  "tile slots (tiles $368-$36F and $378-$37F for the names, "
                  "$301-$31C for the digit font), so the tilemap never moves "
                  "and a margin policy cannot slice a glyph mid-character - "
                  "but an anchor split CAN, which is why R5 uses two bands. "
                  "See hud.json.",
             evidence=[ev("attract_fight", "bg1"), ev("ko_1p_win", "bg1"),
                       ev("victory_quote", "bg1")]),
    ]

    doc = dict(
        issue="beads-8wg.9.13.3",
        deliverable="R7 text surfaces",
        policy_legend=dict(
            Bounded="PpuSetExtraSpaceCentered(43) + "
                    "PpuSetWidescreenLayerClamp(0x0F): the whole screen is "
                    "pillarboxed, margins are one flat colour. No text can "
                    "stretch or slice.",
            BandClamp="the screen is a live fight (world layers extend), so "
                      "this surface needs PpuSetWidescreenLayerClampBand on "
                      "its layer over the named scanline band - or, for BG3 "
                      "on GWED, a whole-layer clamp, since BG3 carries no "
                      "world content during a fight.",
            AnchorBand="PpuSetWidescreenLayerAnchorBandSlot: the surface "
                       "rides the 16:9 edges. Only the fight HUD does this."),
        screens_needing_per_band_work=["attract_fight", "ko_1p_win",
                                       "victory_quote"],
        screens_fully_bounded=["title_logo", "title_menu", "attract_crawl",
                               "attract_cinematic", "final_convo", "ending",
                               "black_transition"],
        never_stretch="LayerStretchBand must never be applied to BG3 or to "
                      "BG1's HUD band: both are glyph surfaces.",
        surfaces=surfaces,
        layer_inventory=inv,
        gate_words=GATE,
        open_items=[
            "victory_quote_text: surface inferred, band unknown (needs an "
            "owner-recorded state with the quote box on screen).",
            "ending_captions: never observed on the captured frame.",
            "options / key-config / character-select / VS screens: not "
            "captured at all (owner-only). All are expected Bounded, and the "
            "gate word $7E:1004 == 0x0012 has never been checked against "
            "them.",
        ],
    )
    dest = a.out or os.path.join(R.OUT_ROOT, "text_surfaces.json")
    R.write_json(dest, doc)
    print("wrote", dest)
    for s in surfaces:
        print("  %-28s %-22s %-10s band=%s"
              % (s["surface"], s["layer"], s["policy"], s["band"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
