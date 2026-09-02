/*
 * Mods-package activation for the opt-in 16:9 presentation.
 *
 * The renderer work lives in src/gwed_display.c and the engine PPU; this file
 * carries only the player-facing switch, so the Mods page is the single place
 * widescreen can be turned on (Beads beads-8wg.1.10 — no config.ini key, and
 * gi.widescreen_supported stays 0 so the launcher's legacy Display toggle is
 * never drawn beside it).
 *
 * Same shape as src/translation_mod.c.
 */

#include "gwed_display.h"
#include "gwed_ws_patch.h"
#include "mod_runtime.h"

#define GWED_WIDESCREEN_PACKAGE "gwed.enhancement.widescreen"
#define GWED_WIDESCREEN_FEATURE "widescreen"
#define GWED_WIDESCREEN_PLUGIN  "gwed.widescreen"

/* Runs before every session's plugin activation pass, so a session whose
 * package selection no longer enables widescreen starts 4:3 rather than
 * inheriting the previous match's state. */
static void gwed_widescreen_reset(void)
{
    GwedDisplay_SetWidescreenEnabled(false);
    /* Drop the guest-side sprite-bounds patch with the feature.
     *
     * The mod runtime runs reset callbacks at the top of every session's
     * activation pass, i.e. after SnesInit has already replaced the cart's
     * ROM image, so by the time this fires the bytes are pristine again and
     * the real work here is clearing the bookkeeping so the next
     * GwedWsPatch_Arm re-applies from scratch. Disarm is written to tolerate
     * exactly that (it restores only bytes it still recognises as its own),
     * because the alternative — trusting a stale pointer into a freed cart —
     * is a use-after-free on every rematch. */
    GwedWsPatch_Disarm();
}

static void gwed_widescreen_activate(void)
{
    GwedDisplay_SetWidescreenEnabled(true);
}

SNES_MOD_CONSTRUCTOR(gwed_widescreen_register)
{
    snes_mod_register_reset_callback(gwed_widescreen_reset);
    snes_mod_register_activation_plugin(GWED_WIDESCREEN_PLUGIN,
                                        gwed_widescreen_activate);
}
