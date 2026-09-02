#pragma once

/*
 * Gundam Wing Endless Duel — host display policy.
 *
 * Everything in here is PRESENTATION ONLY. Nothing this module does touches
 * WRAM, VRAM, OAM, CGRAM, the CPU or the APU, so a widescreen session and a
 * 4:3 session execute the identical guest frames (WIDESCREEN_PATTERNS P16)
 * and two netplay peers stay digest-equal regardless of what either one is
 * looking at (the PPU widescreen fields sit outside ppu_saveload).
 *
 * Ownership split, mirroring MegamanXRecomp/src/mmx_display.{c,h}:
 *   - the ENGINE owns the capability (PpuSetExtraSpace* / PpuSetWidescreen*),
 *     all of it inert at a zero margin;
 *   - this file owns the GAME's policy: how wide the frame is, and which PPU
 *     policy each screen gets;
 *   - src/widescreen_mod.c owns ACTIVATION (the Mods package is the single
 *     authority — no config.ini key, see Beads beads-8wg.1.10).
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "desktop/display_aspect.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef SnesDisplayViewport GwedDisplayViewport;

/* Per-screen widescreen policy.
 *
 * Bounded  = pillarbox. The PPU renders the authentic 256 columns centred in
 *            the wider framebuffer and never writes the margins, so they stay
 *            the cleared backdrop. Text cannot stretch and cannot be sliced,
 *            by construction — which is the safe default for every screen
 *            until the WRAM gate exists.
 * World    = the world layers extend into the margins (symmetric border).
 *
 * TODO(beads-8wg.9.13.5): GwedDisplay_ResolveScreen() currently answers
 * Bounded for everything. The R1 recon work replaces its body with a lookup
 * on the proven WRAM mode/liveness gate (fight + attract demo -> World, every
 * text/menu/quote/ending screen -> Bounded). Classification is by guest state
 * ONLY; never by sampling framebuffer pixels.
 */
typedef enum GwedWsScreen {
    kGwedWsScreen_Bounded = 0,
    kGwedWsScreen_World   = 1,
} GwedWsScreen;

/* ── Activation (called by the Mods plugin, never by config-file code) ───── */
void GwedDisplay_SetWidescreenEnabled(bool enabled);
bool GwedDisplay_IsWidescreenEnabled(void);
/* True once a session has been pinned wide (i.e. g_ws_extra != 0). */
bool GwedDisplay_IsWidescreenActive(void);

/* ── Geometry ───────────────────────────────────────────────────────────── */

/* 342 for widescreen (ceil_even(256*4/3), which is 16:9 at the 7:6 CRT pixel
 * aspect), 256 otherwise. SNESRECOMP_WS_EXTRA overrides the per-side margin
 * for probes and CI; the result is always clamped to kWsExtraMax. */
int GwedDisplay_ComputeFrameWidth(bool widescreen);

/* Pin the session's frame width. Call once per session, AFTER the mod runtime
 * has activated its plugins and BEFORE the first frame is drawn; call again
 * after a session reboot (main.c's `session_reboot:` path).
 *
 * The width is pinned rather than re-derived per frame on purpose: it is baked
 * into the framebuffer pitch, the SDL texture, netplay capability agreement
 * and every P16 capture, and a width that silently follows the window aspect
 * makes two observers measure two different games. */
void GwedDisplay_BeginSession(uint8_t *render_pixels, size_t render_pixels_bytes,
                              uint32_t base_render_flags);

/* The pinned width (256 until a session begins). */
int GwedDisplay_GetCurrentFrameWidth(void);

/* Per-frame PPU policy. MUST run before g_rtl_game_info->draw_ppu_frame(),
 * and must run EVERY frame: ppu_reset() zeroes the whole Ppu struct apart
 * from the render target, so a savestate load or a reset drops every policy
 * field on the floor. */
void GwedDisplay_PreparePpuFrame(void);

/* Which policy this frame gets. See the TODO on GwedWsScreen. */
GwedWsScreen GwedDisplay_ResolveScreen(void);

/* ── Host presentation (window/viewport geometry only) ──────────────────── */
void GwedDisplay_SetPresentation(SnesDisplayAspect aspect, bool ignore_aspect,
                                 bool integer_scale);
SnesDisplayAspect GwedDisplay_GetAspect(void);
bool GwedDisplay_GetIgnoreAspect(void);
bool GwedDisplay_GetIntegerScale(void);

void GwedDisplay_ComputeViewport(int source_width, int source_height,
                                 int drawable_width, int drawable_height,
                                 GwedDisplayViewport *viewport);
int GwedDisplay_GetWindowBaseWidth(int frame_width, int window_height);

#ifdef __cplusplus
}
#endif
