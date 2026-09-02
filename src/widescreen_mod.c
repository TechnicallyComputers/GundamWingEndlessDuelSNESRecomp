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
