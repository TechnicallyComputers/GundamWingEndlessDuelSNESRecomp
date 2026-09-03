#include "gwed_display.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "common_rtl.h"   /* g_ram: the WRAM the screen gate is read from */
#include "snes/ppu.h"
#include "widescreen.h"

/* Required by the shared PPU widescreen runtime. widescreen.h *declares* these
 * two; every game that opts in *defines* them next to its own policy so the
 * engine-side and injector-side `extern` both resolve to one symbol. Nothing
 * in GWED defined them before this file existed. */
bool g_ws_active;
int g_ws_extra;

extern Ppu *g_ppu;

#define GWED_FRAME_HEIGHT 224

static bool s_widescreen_enabled;      /* what the Mods package asked for */
static int s_frame_width = 256;        /* pinned for the whole session */
static uint8_t *s_render_pixels;
static size_t s_render_pixels_bytes;
static uint32_t s_base_render_flags;

static SnesDisplayAspect s_aspect = kSnesDisplayAspect_Crt4x3;
static bool s_ignore_aspect;
static bool s_integer_scale;

/* ── Kill switch (WIDESCREEN_PATTERNS P14) ───────────────────────────────
 * SNESRECOMP_WS_BG=0 keeps the wide framebuffer but drops every layer/margin
 * policy call, so a misbehaving background policy can be bisected out without
 * rebuilding. Read once, cached, logged — a switch nobody can see the state of
 * is a switch that gets blamed for the wrong thing. */
static bool GwedWsBgEnabled(void)
{
    static int s_state = -1;
    if (s_state < 0) {
        const char *env = getenv("SNESRECOMP_WS_BG");
        s_state = (env && env[0] && atoi(env) == 0) ? 0 : 1;
        if (!s_state)
            fprintf(stderr, "[ws] SNESRECOMP_WS_BG=0 — background/layer "
                            "policy disabled\n");
    }
    return s_state != 0;
}

/* Per-policy kill switches (P14). Same contract as SNESRECOMP_WS_BG: read
 * once, cached, logged, and never able to make the guest diverge. */
static bool GwedWsSwitch(const char *name, const char *what, int *cache)
{
    if (*cache < 0) {
        const char *env = getenv(name);
        *cache = (env && env[0] && atoi(env) == 0) ? 0 : 1;
        if (!*cache)
            fprintf(stderr, "[ws] %s=0 - %s disabled\n", name, what);
    }
    return *cache != 0;
}

static bool GwedWsBg1MirrorEnabled(void)
{
    static int s_state = -1;
    return GwedWsSwitch("SNESRECOMP_WS_BG1_MIRROR",
                        "BG1 world-mirror margins", &s_state);
}

static bool GwedWsBg3Enabled(void)
{
    static int s_state = -1;
    return GwedWsSwitch("SNESRECOMP_WS_BG3", "BG3 clamp policy", &s_state);
}

static bool GwedWsHudEnabled(void)
{
    static int s_state = -1;
    return GwedWsSwitch("SNESRECOMP_WS_HUD",
                        "HUD anchoring (falls back to a centred clamp)",
                        &s_state);
}

/* SNESRECOMP_WS_BG2_MODE=wrap|mirror|clamp. wrap (default) leaves BG2 with no
 * policy at all: its map is 256 px wide and its scroll is pinned to 0, so the
 * hardware's own map wrap tiles the skyline into the margins seamlessly.
 * mirror reflects it about the map instead; clamp keeps it native. Exposed so
 * the owner can A/B the skyline without a rebuild. */
enum { kGwedBg2Wrap = 0, kGwedBg2Mirror = 1, kGwedBg2Clamp = 2 };

static int GwedWsBg2Mode(void)
{
    static int s_mode = -1;
    if (s_mode < 0) {
        const char *env = getenv("SNESRECOMP_WS_BG2_MODE");
        s_mode = kGwedBg2Wrap;
        if (env && env[0]) {
            if (strcmp(env, "mirror") == 0)
                s_mode = kGwedBg2Mirror;
            else if (strcmp(env, "clamp") == 0)
                s_mode = kGwedBg2Clamp;
            else if (strcmp(env, "wrap") != 0)
                fprintf(stderr, "[ws] SNESRECOMP_WS_BG2_MODE=%s not "
                                "recognised - using wrap\n", env);
        }
        if (s_mode != kGwedBg2Wrap)
            fprintf(stderr, "[ws] BG2 margin mode = %s\n",
                    s_mode == kGwedBg2Mirror ? "mirror" : "clamp");
    }
    return s_mode;
}

/* -- The fight-screen gate (recon A, Beads beads-8wg.9.13.2) --------------
 *
 * $7E:1000 and $7E:1004 were read by recon as a coarse mode word (0x0010 for
 * "the whole battle family") and a sub-mode (0x0012 live fight, 0x0014
 * victory quote, 0x001E round end). That held for the one attract fight recon
 * sampled and for the player-fight savestates. It does NOT hold across the
 * attract cycle. Logged with the arena map loaded, 2026-09-03:
 *
 *     attract fight   $1000   $1004
 *     city            0010    0012      <- the recon sample
 *     purple sky      0016    0018
 *     dark arena      0012    0014
 *     purple sky 2    0014    0016
 *
 * $1004 == $1000 + 2 on every one: for attract demos the "mode" is a demo
 * INDEX stepping by two, not a family, and a gate keyed on 0x0010/0x0012
 * refused three stages out of four while BG1 held the arena map on all of
 * them. That was the field report: the first demo plays wide, the rest do
 * not.
 *
 * The words are still read -- for the log, where they name what a refused
 * screen was doing -- but they no longer classify. The BG1 arena precondition
 * below does, alone: it is the leg recon called load-bearing, it is the leg
 * recon's whole-cycle sweep proved unique to arena screens, and it is the
 * leg that agreed on every stage. Read as raw WRAM bytes, never from
 * framebuffer pixels (standing rule), and never from the PPU scroll. */
#define GWED_WRAM_MODE      0x1000u
#define GWED_WRAM_SUBMODE   0x1004u
#define GWED_MODE_BATTLE    0x0010u
#define GWED_SUB_FIGHT      0x0012u
#define GWED_SUB_QUOTE      0x0014u
#define GWED_SUB_ROUND_END  0x001Eu

static uint16_t GwedWram16(uint32_t addr)
{
    return (uint16_t)(g_ram[addr] | ((uint16_t)g_ram[addr + 1] << 8));
}

/* -- The arena geometry the world-mirror policy depends on ---------------
 *
 * BG1's tilemap is authored only over map px [64,448) - 64x64 tiles, columns
 * 8..55, tile 0 either side (recon B). The camera X clamp is [64,192], so the
 * authentic 256 columns span exactly the authored region at the walls and
 * only the widescreen margins can ever leave it.
 *
 * $2107 == 0x6B (map at word 0x6800 = byte $D000, 64x64) with BG1's character
 * base at word 0x0000 is unique to the arena screens: every non-arena screen
 * recon captured - title, logo, menu, crawl, cinematic, inter-stage dialogue,
 * ending, black transition - reads 0x69 there and points BG1 somewhere else.
 * That check is what makes the sub-mode families below safe: 0x001E is shared
 * by the KO/round-end screen (which IS the arena) and by the dialogue and
 * ending screens (which are not), so the sub-mode alone cannot decide. This
 * is emulated PPU configuration, not a rendered pixel, and it is a
 * PRECONDITION on the world bounds rather than a screen classifier: if BG1 is
 * not the arena map then reflecting about the arena's edges is meaningless,
 * so the policy fails closed to a pillarbox. */
#define GWED_BG1_ARENA_BGXSC   0x6Bu
#define GWED_ARENA_WORLD_LEFT  64u
#define GWED_ARENA_WORLD_RIGHT 448u
/* BG2's 32x64 skyline map at word 0x7800 (byte $F000). The victory quote
 * re-points BG2 at a 64x32 portrait panel (0x79) whose off-screen columns are
 * not known to be authored, so "let the map wrap" is only claimed for 0x7A. */
#define GWED_BG2_SKYLINE_BGXSC 0x7Au

/* -- The fight HUD (recon B / R5, Beads beads-8wg.9.13.3) -----------------
 *
 * The raster IRQ pins BG1 to hScroll 0 / vScroll 440 for lines 22..71 and
 * restores vScroll on line 73, so line 72 still renders a HUD tile row but at
 * the arena's own scroll. World bounds are meaningless on any of those lines
 * (their hScroll is not the camera), which is why the whole band is excluded
 * from the world-mirror policy.
 *
 * Content is map rows 58-63 = lines 24..71. Lines 22-23 are the bottom two
 * pixel rows of map row 57 - arena art in the fight, blank in the other two
 * HUD scenes - drawn at the HUD's forced hScroll 0, so they are clamped, not
 * remapped. Measured identically in attract_fight, ko_1p_win and
 * victory_quote (analysis/widescreen/recon/hud.json).
 *
 * The bar is ONE continuous graphic: there is no fully transparent column
 * anywhere in px 1..254 of rows 58-63. Anchoring alone would therefore open a
 * 43 px transparent hole either side of the TIME pod, so each tile row gets an
 * ELASTIC anchor band (WIDESCREEN_PATTERNS P2c): the rigid groups - frame
 * caps, name plates, digits, the TIME label, the round-win markers - copy 1:1
 * out to the 16:9 edges, and the margin is absorbed by material that can take
 * it. The elastic runs below are spans whose pixel columns are IDENTICAL to
 * one another in all three HUD scenes (so the stretch is exactly invisible),
 * or a gauge interior (so the stretch is proportional and the fill fraction
 * survives). All of them are tile-aligned, and all are mirror-symmetric about
 * px 128 like the rest of the HUD. */
#define GWED_HUD_BAND_Y0 22u   /* first line of the pinned-scroll band */
#define GWED_HUD_BAND_Y1 73u   /* one past the last (line 72 = transitional) */
/* Lines 22-23: leftover map row 57. Clamped. */
#define GWED_HUD_LEADIN_Y1 24u
/* Row 58, lines 24..31: name plates (px 16-79 / 176-239) anchored out, the
 * TIME label (px 112-143) centred, and the plain chrome between them - map
 * cols 11-13 and 18-20, three identical $0777 tiles each - stretched. */
#define GWED_HUD_R58_Y0 24u
#define GWED_HUD_R58_Y1 32u
#define GWED_HUD_R58_LX0 88u
#define GWED_HUD_R58_LX1 112u
#define GWED_HUD_R58_RX0 144u
#define GWED_HUD_R58_RX1 168u
/* Row 59, lines 32..39: the health gauges. The whole bar interior is elastic,
 * so the bar gets 43 px longer and keeps its fill fraction; the 8 px frame
 * caps stay rigid at the edges and the TIME digits' pod (px 120-135) is the
 * centre group. */
#define GWED_HUD_R59_Y0 32u
#define GWED_HUD_R59_Y1 40u
#define GWED_HUD_R59_LX0 8u
#define GWED_HUD_R59_LX1 120u
#define GWED_HUD_R59_RX0 136u
#define GWED_HUD_R59_RX1 248u
/* Row 60, lines 40..47: the boost gauges, map cols 3-14 / 17-28. Narrower
 * than row 59 because this row's outer 24 px carry shaped chrome, not bar. */
#define GWED_HUD_R60_Y0 40u
#define GWED_HUD_R60_Y1 48u
#define GWED_HUD_R60_LX0 24u
#define GWED_HUD_R60_LX1 120u
#define GWED_HUD_R60_RX0 136u
#define GWED_HUD_R60_RX1 232u
/* Rows 61-63, lines 48..71: energy counters (px 8-55 / 200-247) and the
 * round-win markers (px 88-119 / 136-167) are all rigid; the elastic run is
 * ONE column, px 87 and its mirror px 168.
 *
 * It was px 62. That column is inside the combo readout: during a multi-hit
 * combo the game replaces the energy box with a wider "nn HIT" box whose
 * interior runs to px 62 and whose cap, charge arrow and shoulder occupy
 * 63..~85, so repeating 62 seventy-odd times drew the box all the way to the
 * centre group. The note that used to sit here saw that coming and accepted
 * it -- "just makes the box longer" -- on the grounds that no multi-column
 * run in 56..85 is flat in both states. True, and beside the point: the
 * column does not have to be in 56..85 at all.
 *
 * Measured on the combo-state capture (4x crop of the cap region): the
 * shoulder ends by ~px 80, and from there to the win markers at 88 BG1 is
 * TRANSPARENT on every line below the thin chrome bar at the top of the
 * band, and that bar is a uniform horizontal run. A transparent column
 * repeated is invisible (the arena's own band shows through, as it does
 * natively), and a uniform bar column repeated is invisible. In the normal
 * state the energy box ends at 55 and the recon found 62-67 identical
 * plate, so 87 is plate there as well. 87 rather than 86: one column of
 * margin from the shoulder's furthest measured reach, and still short of the
 * rigid marker at 88. Either state now keeps its box rigid on the left
 * edge, which is the whole point of anchoring. */
#define GWED_HUD_R61_Y0 48u
#define GWED_HUD_R61_Y1 72u
#define GWED_HUD_R61_LX0 87u
#define GWED_HUD_R61_LX1 88u
#define GWED_HUD_R61_RX0 168u
#define GWED_HUD_R61_RX1 169u
/* Line 72: a HUD tile row drawn at the ARENA's hScroll, so none of the px
 * constants above apply to it. An identity elastic band renders its authentic
 * 256 columns and contributes nothing to the margins - a clamp, expressed in
 * the same vocabulary, because PpuSetWidescreenLayerClampBand only holds one
 * range per layer and lines 22-23 already own it. */
#define GWED_HUD_TAIL_Y0 72u
#define GWED_HUD_TAIL_Y1 73u
/* The full visible picture. ppu_runLine() is called with 1..224 and the band
 * test is a half-open [y0,y1), so the end has to be 225. */
#define GWED_PICTURE_Y0  0u
#define GWED_PICTURE_Y1  225u

static bool GwedBg1IsArenaMap(void)
{
    return g_ppu && g_ppu->bgXsc[0] == GWED_BG1_ARENA_BGXSC &&
           (g_ppu->bgTileAdr & 0xF) == 0;
}

static int ClampEven(int64_t value)
{
    value &= ~(int64_t)1;
    if (value < 256)
        value = 256;
    if (value > 256 + 2 * (int64_t)kWsExtraMax)
        value = 256 + 2 * kWsExtraMax;
    return (int)value;
}

int GwedDisplay_ComputeFrameWidth(bool widescreen)
{
    int width = widescreen
        ? ClampEven(SnesDisplayAspect_ComputeWideFrameWidth(256)) : 256;
    /* Probe/CI determinism: pin the margin so measured widescreen geometry
     * never silently follows the window aspect. 0 forces authentic 4:3 even
     * with the package enabled, which is what the P16 gate runs on. */
    int override_extra = PpuWsExtraOverride();
    if (override_extra >= 0)
        width = widescreen ? ClampEven(256 + 2 * (int64_t)override_extra) : 256;
    return width;
}

void GwedDisplay_SetWidescreenEnabled(bool enabled)
{
    /* No file is written. mods/preloaded/state.toml, owned by the mod runtime,
     * is the sole persistence for this feature (Beads beads-8wg.1.10); a
     * config.ini key would be a second authority that silently disagrees. */
    s_widescreen_enabled = enabled;
}

bool GwedDisplay_IsWidescreenEnabled(void) { return s_widescreen_enabled; }
bool GwedDisplay_IsWidescreenActive(void) { return g_ws_active; }
int GwedDisplay_GetCurrentFrameWidth(void)
{
    return s_frame_width > 0 ? s_frame_width : 256;
}

void GwedDisplay_BeginSession(uint8_t *render_pixels, size_t render_pixels_bytes,
                              uint32_t base_render_flags)
{
    int width = GwedDisplay_ComputeFrameWidth(s_widescreen_enabled);

    s_render_pixels = render_pixels;
    s_render_pixels_bytes = render_pixels_bytes;
    s_base_render_flags = base_render_flags;

    /* Never pin a width the host buffer cannot hold. */
    if (render_pixels_bytes &&
        (size_t)width * 4u * GWED_FRAME_HEIGHT > render_pixels_bytes) {
        fprintf(stderr, "[ws] frame buffer too small for width %d — "
                        "falling back to 256\n", width);
        width = 256;
    }

    s_frame_width = width;
    g_ws_extra = (width - 256) / 2;
    g_ws_active = g_ws_extra != 0;
    fprintf(stderr, "[ws] widescreen %s — frame %dx%d (margin %d/side)\n",
            g_ws_active ? "on" : "off", width, GWED_FRAME_HEIGHT, g_ws_extra);

    /* Point the PPU at the render target immediately: anything that inspects
     * g_ppu->renderBuffer before the first present (thumbnails, the debug
     * server's screenshot) must not see NULL. */
    GwedDisplay_PreparePpuFrame();
}

/* Say what the gate decided and what it read, whenever that changes.
 *
 * The gate fails closed to a pillarbox, and a pillarbox that is wrong is
 * silent: it just looks like a scene the recon never covered. Reported in the
 * field: the first attract demo plays wide and the next few do not, and
 * nothing in the log could say which of the three legs refused them. The
 * three legs are logged together so a refusal names its cause. Change-
 * triggered, so a long attract cycle costs a handful of lines. */
static GwedWsScreen GwedWsLogScreen(GwedWsScreen verdict)
{
    static int s_last = -1;
    static uint16_t s_mode, s_sub;
    static uint8_t s_bg1sc, s_chr;
    extern int snes_frame_counter;
    uint16_t mode = GwedWram16(GWED_WRAM_MODE);
    uint16_t sub = GwedWram16(GWED_WRAM_SUBMODE);
    uint8_t bg1sc = g_ppu ? g_ppu->bgXsc[0] : 0xFF;
    uint8_t chr = g_ppu ? (uint8_t)(g_ppu->bgTileAdr & 0xF) : 0xFF;
    if ((int)verdict != s_last || mode != s_mode || sub != s_sub ||
        bg1sc != s_bg1sc || chr != s_chr) {
        const char *why = "";
        if (verdict != kGwedWsScreen_World) {
            if (bg1sc == GWED_BG1_ARENA_BGXSC && chr != 0)
                why = "  <-- arena map, but BG1 char base is not 0";
            else if (bg1sc == GWED_BG1_ARENA_BGXSC)
                why = "  <-- arena map: this should not be Bounded";
        }
        fprintf(stderr, "[ws] screen=%s frame=%d mode=%04x sub=%04x "
                        "bg1sc=%02x bg1chr=%x%s\n",
                verdict == kGwedWsScreen_World ? "World" : "Bounded",
                snes_frame_counter, mode, sub, bg1sc, chr, why);
        s_last = (int)verdict; s_mode = mode; s_sub = sub;
        s_bg1sc = bg1sc; s_chr = chr;
    }
    return verdict;
}

GwedWsScreen GwedDisplay_ResolveScreen(void)
{
    /* Guest state only. A pillarbox can never stretch or slice text, so
     * Bounded is the fail-closed answer for anything not positively proven to
     * be the arena: the cost of guessing wrong here is "less widescreen", not
     * "broken widescreen".
     *
     * "Positively proven to be the arena" is one thing: BG1's tilemap register
     * points at the arena map ($2107 == 0x6B, 64x64 at byte $D000) with its
     * character base at 0. Recon established that as the load-bearing
     * precondition -- if BG1 is not the arena map, reflecting about the
     * arena's edges is meaningless -- and its sweep of the whole attract cycle
     * found no non-arena screen that reads it. The mode words that used to
     * gate ahead of it were an attract-demo index misread as a family (see
     * the gate note above) and refused most of the cycle's fights; they are
     * now diagnostic only.
     *
     * Deliberately NOT gated on the P6 liveness signal ($7E:0600 counting).
     * The round intro is a scripted, frozen-counter screen that already shows
     * the arena, and snapping the frame from a wide arena to a pillarboxed
     * 256 for the duration of every round intro would be a worse artefact
     * than anything liveness protects against here. Liveness matters when a
     * gate drives behaviour that could change the simulation; nothing in this
     * file does. */
    return GwedWsLogScreen(GwedBg1IsArenaMap() ? kGwedWsScreen_World
                                               : kGwedWsScreen_Bounded);
}

/* The HUD band's per-line policy. Called only from the World branch, and only
 * after PpuSetExtraSpace (which clears every layer policy).
 *
 * With SNESRECOMP_WS_HUD=0 this degrades to the previous behaviour: one clamp
 * band over the whole band, i.e. the HUD stays native-centred inside the 16:9
 * frame. That is also the fail-closed answer if the engine refuses a band -
 * every setter is validated and every rejection is reported, so a bad constant
 * can only ever cost us the anchoring, never tear a glyph. */
static void GwedApplyHudPolicy(void)
{
    if (!GwedWsHudEnabled()) {
        PpuSetWidescreenLayerClampBand(g_ppu, 0, (uint8_t)GWED_HUD_BAND_Y0,
                                       (uint8_t)GWED_HUD_BAND_Y1);
        return;
    }

    /* Lines 22-23 keep the clamp: they are the tail of an arena tile row and
     * have no HUD layout to remap. */
    PpuSetWidescreenLayerClampBand(g_ppu, 0, (uint8_t)GWED_HUD_BAND_Y0,
                                   (uint8_t)GWED_HUD_LEADIN_Y1);

    static const struct {
        uint8_t y0, y1;
        uint16_t lx0, lx1, rx0, rx1;
        const char *name;
    } kBands[] = {
        { GWED_HUD_R58_Y0, GWED_HUD_R58_Y1, GWED_HUD_R58_LX0,
          GWED_HUD_R58_LX1, GWED_HUD_R58_RX0, GWED_HUD_R58_RX1,
          "row58 names + TIME label" },
        { GWED_HUD_R59_Y0, GWED_HUD_R59_Y1, GWED_HUD_R59_LX0,
          GWED_HUD_R59_LX1, GWED_HUD_R59_RX0, GWED_HUD_R59_RX1,
          "row59 health gauges" },
        { GWED_HUD_R60_Y0, GWED_HUD_R60_Y1, GWED_HUD_R60_LX0,
          GWED_HUD_R60_LX1, GWED_HUD_R60_RX0, GWED_HUD_R60_RX1,
          "row60 boost gauges" },
        { GWED_HUD_R61_Y0, GWED_HUD_R61_Y1, GWED_HUD_R61_LX0,
          GWED_HUD_R61_LX1, GWED_HUD_R61_RX0, GWED_HUD_R61_RX1,
          "rows61-63 counters + win markers" },
        /* Identity: no elastic run named, so the line clamps. */
        { GWED_HUD_TAIL_Y0, GWED_HUD_TAIL_Y1, 0, 0, 0, 0,
          "line72 transitional" },
    };
    enum { kBandCount = (int)(sizeof(kBands) / sizeof(kBands[0])) };
    /* The engine rejects an out-of-range slot and this function then falls
     * back to the clamp, so overflowing would be safe but silent. Fail at
     * compile time instead, where whoever adds the sixth band will see it. */
    _Static_assert(kBandCount <= kPpuWsElasticBands,
                   "more HUD bands than the PPU has elastic band slots");

    for (int i = 0; i < kBandCount; ++i) {
        if (PpuSetWidescreenLayerElasticSplitBandSlot(
                g_ppu, (uint8_t)i, 0, kBands[i].y0, kBands[i].y1,
                kBands[i].lx0, kBands[i].lx1, kBands[i].rx0, kBands[i].rx1))
            continue;
        /* Fail closed and say so once: clamp the whole band instead. */
        static bool s_warned;
        if (!s_warned) {
            s_warned = true;
            fprintf(stderr, "[ws] HUD elastic band %d (%s) refused by the PPU "
                            "- falling back to the centred clamp\n",
                    i, kBands[i].name);
        }
        for (int j = 0; j < kBandCount; ++j)
            PpuSetWidescreenLayerElasticSplitBandSlot(g_ppu, (uint8_t)j, 0,
                                                      0, 0, 0, 0, 0, 0);
        PpuSetWidescreenLayerClampBand(g_ppu, 0, (uint8_t)GWED_HUD_BAND_Y0,
                                       (uint8_t)GWED_HUD_BAND_Y1);
        return;
    }
}

void GwedDisplay_PreparePpuFrame(void)
{
    if (!g_ppu || !s_render_pixels)
        return;

    if (!g_ws_active) {
        /* P16 by construction: with widescreen off this function issues the
         * single call the faithful host always made and touches no widescreen
         * state at all, so there is no policy field left over to leak into a
         * 4:3 frame. */
        PpuBeginDrawing(g_ppu, s_render_pixels, (size_t)s_frame_width * 4,
                        s_base_render_flags);
        return;
    }

    /* The legacy pixel-at-a-time renderer has a hard-coded 256-column loop and
     * stores but does not apply the widescreen layer policies. Force the
     * priority-buffer compositor while a wide frame is live. */
    PpuBeginDrawing(g_ppu, s_render_pixels, (size_t)s_frame_width * 4,
                    s_base_render_flags | kPpuRenderFlags_NewRenderer);

    if (!GwedWsBgEnabled())
        return;

    /* Re-applied every frame on purpose: ppu_reset() memsets the whole Ppu
     * apart from the render target, so a reset or a savestate load silently
     * drops every one of these. */
    switch (GwedDisplay_ResolveScreen()) {
    case kGwedWsScreen_World: {
        /* Symmetric border: the world layers render into the margins. There is
         * deliberately no margin memset here - the whole point of this branch
         * is that the layers and the backdrop fill them. */
        PpuSetExtraSpace(g_ppu, (uint8_t)g_ws_extra);

        uint8_t clamp_mask = 0;

        /* BG1 - the arena. Mid-scroll the margins hold real authored art, so
         * they must render naturally; only at the camera walls does the view
         * run past the authored world, and there it reflects about the
         * world's own edge rather than the viewport's (P2b). The HUD band's
         * lines are clamped instead: their hScroll is pinned to 0 by the
         * raster IRQ, so an arena world bound would mean nothing there. Clamp
         * bands win over world bands per line inside the PPU, so the relative
         * order of these two calls does not matter - but both must follow
         * PpuSetExtraSpace, which resets every layer policy. */
        if (GwedWsBg1MirrorEnabled())
            PpuSetWidescreenLayerWorldMirrorBand(
                g_ppu, 0, (uint8_t)GWED_PICTURE_Y0, (uint8_t)GWED_PICTURE_Y1,
                GWED_ARENA_WORLD_LEFT, GWED_ARENA_WORLD_RIGHT);
        GwedApplyHudPolicy();

        /* BG2 - the skyline. 256 px wide with its scroll pinned to 0, so the
         * hardware's own map wrap already tiles it into the margins and the
         * default needs no policy at all. Any other BG2 layout on an arena
         * screen (the victory quote's portrait panel) is a full-screen art
         * plane whose off-screen columns are unverified: clamp it. */
        {
            bool skyline = g_ppu->bgXsc[1] == GWED_BG2_SKYLINE_BGXSC;
            int bg2_mode = skyline ? GwedWsBg2Mode() : kGwedBg2Clamp;
            if (bg2_mode == kGwedBg2Mirror)
                PpuSetWidescreenLayerWorldMirrorBand(
                    g_ppu, 1, (uint8_t)GWED_PICTURE_Y0,
                    (uint8_t)GWED_PICTURE_Y1, 0, 256);
            else if (bg2_mode == kGwedBg2Clamp)
                clamp_mask |= 1u << 1;
        }

        /* BG3 - text and banners (the attract band, "1P WIN", the dialogue
         * box). Glyph art never widens: keeping it in the authentic 256 means
         * it can neither stretch nor be sliced. */
        if (GwedWsBg3Enabled())
            clamp_mask |= 1u << 2;

        if (clamp_mask)
            PpuSetWidescreenLayerClamp(g_ppu, clamp_mask);
        break;
    }
    case kGwedWsScreen_Bounded:
    default:
        /* Pillarbox: keep the centring budget, render only the authentic 256
         * columns. PpuSetExtraSpace* resets the layer policies, so the clamp
         * has to follow it, not precede it. */
        PpuSetExtraSpaceCentered(g_ppu, (uint8_t)g_ws_extra);
        PpuSetWidescreenLayerClamp(g_ppu, 0x0F);
        /* The PPU never writes the margin columns in this mode ("the caller
         * blacks out the side margins"), so clear them ourselves rather than
         * relying on the buffer's initial zeroing — otherwise a later switch
         * out of World mode would leave the last world frame's margins frozen
         * on screen. */
        {
            size_t pitch = (size_t)s_frame_width * 4u;
            size_t margin = (size_t)g_ws_extra * 4u;
            for (int y = 0; y < GWED_FRAME_HEIGHT; ++y) {
                uint8_t *row = s_render_pixels + (size_t)y * pitch;
                memset(row, 0, margin);
                memset(row + pitch - margin, 0, margin);
            }
        }
        break;
    }
}

/* ── Host presentation ──────────────────────────────────────────────────── */

void GwedDisplay_SetPresentation(SnesDisplayAspect aspect, bool ignore_aspect,
                                 bool integer_scale)
{
    s_aspect = SnesDisplayAspect_Clamp((int)aspect);
    s_ignore_aspect = ignore_aspect;
    s_integer_scale = integer_scale;
}

SnesDisplayAspect GwedDisplay_GetAspect(void) { return s_aspect; }
bool GwedDisplay_GetIgnoreAspect(void) { return s_ignore_aspect; }
bool GwedDisplay_GetIntegerScale(void) { return s_integer_scale; }

void GwedDisplay_ComputeViewport(int source_width, int source_height,
                                 int drawable_width, int drawable_height,
                                 GwedDisplayViewport *viewport)
{
    SnesDisplayAspect_ComputeViewport(source_width, source_height,
                                      drawable_width, drawable_height,
                                      s_aspect, s_ignore_aspect,
                                      s_integer_scale, viewport);
}

int GwedDisplay_GetWindowBaseWidth(int frame_width, int window_height)
{
    return SnesDisplayAspect_ComputeWindowWidth(frame_width, GWED_FRAME_HEIGHT,
                                                window_height, s_aspect);
}
