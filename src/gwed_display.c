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
 * $7E:1000 is the coarse mode word: 0x0010 for the whole battle family.
 * $7E:1004 is the sub-mode: 0x0012 live fight / round intro, 0x0014 victory
 * quote, 0x001E round end + inter-stage dialogue + ending. 0x000A/0x000C are
 * the attract crawl and the title menu, 0x0000 the cinematic and the logo.
 *
 * Read as raw WRAM bytes, never from framebuffer pixels (standing rule), and
 * never from the PPU scroll: this is a mode discriminator, not pixel phase. */
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

/* HUD band: the raster IRQ pins BG1 to hScroll 0 / vScroll 440 for lines
 * 22..71 and restores vScroll on line 73, so line 72 still renders a HUD tile
 * row, at the arena's scroll. World bounds are meaningless on any of those
 * lines (their hScroll is not the camera), so BG1 is clamped across [22,73)
 * and the arena band gets the world policy everywhere else.
 * TODO(beads-8wg.9.13.3): recon B is to deliver these as measured constants
 * together with the HUD anchor split; until then they come from the
 * attract-fight per-line PPU journal (analysis/widescreen/recon/screens/
 * attract_fight/attract_fight_lines.json). */
#define GWED_HUD_BAND_Y0 22u
#define GWED_HUD_BAND_Y1 73u
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

GwedWsScreen GwedDisplay_ResolveScreen(void)
{
    /* Guest state only. A pillarbox can never stretch or slice text, so
     * Bounded is the fail-closed answer for anything not positively proven to
     * be the arena: the cost of guessing wrong here is "less widescreen", not
     * "broken widescreen".
     *
     * Deliberately NOT gated on the P6 liveness signal ($7E:0600 counting).
     * The round intro is a scripted, frozen-counter screen that already shows
     * the arena, and snapping the frame from a 342-wide arena to a
     * pillarboxed 256 for the duration of every round intro would be a worse
     * artefact than anything liveness protects against here. Liveness matters
     * when a gate drives behaviour that could change the simulation; nothing
     * in this file does. */
    if (GwedWram16(GWED_WRAM_MODE) != GWED_MODE_BATTLE)
        return kGwedWsScreen_Bounded;

    switch (GwedWram16(GWED_WRAM_SUBMODE)) {
    case GWED_SUB_FIGHT:      /* live round + round intro */
    case GWED_SUB_QUOTE:      /* victory quote: portrait panel over the arena */
    case GWED_SUB_ROUND_END:  /* KO banner - but also dialogue and ending */
        break;
    default:
        return kGwedWsScreen_Bounded;
    }

    /* The sub-mode names a family, not a layout. Only the arena tilemap has
     * an authored world to reflect about. */
    return GwedBg1IsArenaMap() ? kGwedWsScreen_World : kGwedWsScreen_Bounded;
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
        PpuSetWidescreenLayerClampBand(g_ppu, 0, (uint8_t)GWED_HUD_BAND_Y0,
                                       (uint8_t)GWED_HUD_BAND_Y1);

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
