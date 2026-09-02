#include "gwed_display.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
    /* TODO(beads-8wg.9.13.5): replace with the WRAM-gate-driven table from R1
     * (mode byte + liveness conjunction). Until that gate is PROVEN, every
     * screen is Bounded: a pillarbox can never stretch or slice text, so the
     * failure mode of guessing wrong here is "less widescreen", not "broken
     * widescreen". Classification must never come from framebuffer pixels. */
    return kGwedWsScreen_Bounded;
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
    case kGwedWsScreen_World:
        /* Symmetric border: the world layers render into the margins. */
        PpuSetExtraSpace(g_ppu, (uint8_t)g_ws_extra);
        break;
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
