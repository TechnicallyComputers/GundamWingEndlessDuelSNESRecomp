#pragma once

/*
 * Gundam Wing Endless Duel — photosensitivity flash guard, game-side policy.
 *
 * Activation only. The filter is a shared recomp-ui capability
 * (recomp_flash_guard.h); src/flashguard_mod.c turns the Mods-page selection
 * into a step limit; src/main.c wires that into the present path.
 *
 * PRESENTATION ONLY, like src/gwed_display.h: nothing behind this touches
 * WRAM, VRAM, OAM, CGRAM, the CPU or the APU, so a session with the guard on
 * executes the identical guest frames as one without and two netplay peers
 * stay digest-equal whichever of them is running it. The manifest declares
 * the feature presentation_only, which is what keeps it out of the mod set
 * peers compare -- see Feature::presentation_only in
 * snesrecomp/runner/src/mod_runtime.cpp.
 */

/* The package id, shared because src/main.c names it too -- in the cosmetic
 * allowlist it publishes when hosting. Two spellings of a package id is a
 * whitelist that silently stops matching the thing it is meant to allow. */
#define GWED_FLASHGUARD_PACKAGE_ID "gwed.accessibility.flashguard"
#define GWED_FLASHGUARD_VERSION    "1.0.0"

#ifdef __cplusplus
extern "C" {
#endif

/* The step limit this session resolved to, 0 when the feature is off. Valid
 * only after the session's plugin activation pass has run. */
int GwedFlashGuard_Limit(void);

/* Whether the Mods-page selection enables the guard for the NEXT session,
 * read straight from the mod runtime. Correct before the activation pass, and
 * therefore the one to ask from the launcher or a diagnostics header -- the
 * same distinction GwedDisplay_IsWidescreenSelected draws. */
int GwedFlashGuard_IsSelected(void);

#ifdef __cplusplus
}
#endif
