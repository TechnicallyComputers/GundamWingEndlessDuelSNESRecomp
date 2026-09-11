/*
 * Mods-package activation for the opt-in photosensitivity flash guard.
 *
 * The filter itself lives in recomp-ui (recomp_flash_guard.h) because nothing
 * about it is specific to this game or this console -- it is arithmetic on two
 * ARGB frames, and every port has flashes. This file carries only the
 * player-facing switch and the strength it resolves to, so the Mods page is
 * the single place it can be turned on; src/main.c owns wiring it into the
 * present path.
 *
 * Same shape as src/widescreen_mod.c, with one difference that matters: the
 * feature is declared presentation_only in the manifest, so it is left out of
 * the mod set netplay peers compare and a player may run it against an
 * opponent who does not. See Feature::presentation_only in
 * snesrecomp/runner/src/mod_runtime.cpp.
 */

#include <string.h>

#include "mod_runtime.h"
#include "recomp_flash_guard.h"

#include "flashguard_mod.h"

#define GWED_FLASHGUARD_PACKAGE GWED_FLASHGUARD_PACKAGE_ID
#define GWED_FLASHGUARD_FEATURE "flash_guard"
#define GWED_FLASHGUARD_PLUGIN  "gwed.flashguard"

/* Resolved at activation, read by main.c when it builds the present path.
 * 0 = off, otherwise the per-frame step limit handed to the shared filter. */
static int s_limit;

/* Runs before every session's plugin activation pass, so a session whose
 * package selection no longer enables the guard starts unfiltered rather than
 * inheriting the previous match's state -- the same contract as
 * gwed_widescreen_reset. */
static void gwed_flashguard_reset(void)
{
    s_limit = 0;
}

static void gwed_flashguard_activate(void)
{
    char strength[32] = "standard";

    if (!snes_mod_runtime_feature_enabled_c(GWED_FLASHGUARD_PACKAGE,
                                            GWED_FLASHGUARD_FEATURE))
        return;
    if (!snes_mod_runtime_feature_option_value_c(GWED_FLASHGUARD_PACKAGE,
                                                 GWED_FLASHGUARD_FEATURE,
                                                 "strength", strength,
                                                 sizeof(strength))) {
        /* An unreadable option is not a reason to run unprotected: the player
         * enabled the feature, so honour that at the documented default
         * rather than silently doing nothing. */
        strncpy(strength, "standard", sizeof(strength) - 1);
        strength[sizeof(strength) - 1] = '\0';
    }

    if (strcmp(strength, "mild") == 0)
        s_limit = kRecompFlashGuardMild;
    else if (strcmp(strength, "maximum") == 0)
        s_limit = kRecompFlashGuardMaximum;
    else
        s_limit = kRecompFlashGuardStandard;
}

/* The resolved step limit for this session, 0 when the feature is off. */
int GwedFlashGuard_Limit(void)
{
    return s_limit;
}

/* The selection, not the activation -- the same distinction
 * GwedDisplay_IsWidescreenSelected draws, and needed for the same reason: the
 * launcher and the diagnostics header want the answer before the session's
 * plugin pass has run. */
int GwedFlashGuard_IsSelected(void)
{
    return snes_mod_runtime_feature_enabled_c(GWED_FLASHGUARD_PACKAGE,
                                              GWED_FLASHGUARD_FEATURE) != 0;
}

SNES_MOD_CONSTRUCTOR(gwed_flashguard_register)
{
    snes_mod_register_reset_callback(gwed_flashguard_reset);
    /* Registered as PRESENTATION, which is this executable vouching that the
     * callback above only ever changes what is drawn -- and it is: all it does
     * is resolve a strength into an integer that src/main.c hands to a filter
     * running on the finished framebuffer. Nothing here reaches the CPU, WRAM,
     * VRAM, OAM, CGRAM, the APU or a save state.
     *
     * Without this the manifest's presentation_only would be refused and the
     * feature treated as simulation-affecting, which is the correct default
     * for every plugin that has not been looked at. Do not copy this call to
     * a plugin without checking that claim is true of it. */
    snes_mod_register_presentation_plugin(GWED_FLASHGUARD_PLUGIN,
                                          gwed_flashguard_activate);
}
