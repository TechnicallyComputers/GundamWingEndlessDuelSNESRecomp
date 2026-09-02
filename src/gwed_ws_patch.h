#pragma once

/*
 * Gundam Wing Endless Duel — guarded in-memory ROM patches for the widescreen
 * sprite bounds (WIDESCREEN_PATTERNS P8; Beads beads-8wg.9.13.6).
 *
 * WHY A ROM PATCH AND NOT A GENERATED-C OVERRIDE
 *
 * GWED is LLE-first: `src/gen/program_manifest.json` covers $00:8000-$00:8A01
 * and nothing else in bank $00 (banks $03/$07 have zero bodies), so the OAM
 * metasprite emitter at $00:A03F-$00:A125 has no AOT body and never will
 * unless somebody promotes it. It executes in the interpreter, which fetches
 * every byte through `RomPtr` -> `cart_getRomPtr` -> `g_snes->cart->rom`.
 * Patching that in-memory image is therefore TIER-INDEPENDENT: it is the one
 * intervention that is equally correct for the interpreter today and for an
 * AOT body compiled from the same bytes tomorrow. MMX's tools/apply_overrides
 * .py approach (patching generated C) would match nothing here.
 *
 * This is the same idiom as the localization mod's
 * `snesrecomp/runner/src/snes_text_xlate.cpp apply_rom_patch`: verify the
 * bytes you expect are actually there, then write. It is not the same memory
 * as the file on disk — `snes_loadRom` mallocs and memcpys a private image per
 * session, so nothing here can ever modify the player's ROM, and every
 * session starts from pristine bytes whether or not Disarm ran.
 *
 * WHAT IT COSTS
 *
 * Unlike everything else in the widescreen feature, this is NOT presentation
 * only: the emitter writes the OAM staging buffer at $7E:0D00-$7E:0F1F, so a
 * widescreen session and a 4:3 session produce different WRAM. That is why
 *   - it is armed ONLY while `g_ws_active` (so P16's 4:3 gate is satisfied by
 *     construction — with the package off, not one byte is written), and
 *   - in netplay it is armed only when the negotiated match caps agree, or
 *     when there is no peer at all (see GwedWsPatch_SetNetplaySession).
 * Savestates do not carry ROM (`cart_saveload` streams only `cart->ram`), so
 * rollback resim re-executes against the same patched image it recorded
 * against — the patch is invisible to the netplay digest machinery and to
 * every existing .state file.
 *
 * Fail-closed everywhere: a site whose bytes do not match its signature
 * EXACTLY is skipped and logged, never force-written.
 */

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Longest signature/replacement a site may declare. */
#define GWED_WS_PATCH_MAX_LEN 16

/* Build the replacement bytes for `ws_extra`. Return 0 to DECLINE (the site
 * stays vanilla and is logged as declined) — e.g. when the requested margin
 * does not fit the encoding. `len` is the site's byte count and the emitter
 * must fill exactly that many: a patch that changed length would shift every
 * following instruction in the bank. */
typedef int (*GwedWsPatchEmitFn)(int ws_extra, uint8_t *out, uint8_t len);

typedef struct GwedWsPatchSite {
    const char *name;        /* stable id, used by SNESRECOMP_WS_PATCH_DISABLE */
    const char *env_kill;    /* per-site kill switch, "<name>=0" disables it */
    const char *what;        /* one-line description for the log */
    uint32_t pc24;           /* 65816 address, for logs and cross-referencing */
    uint32_t rom_offset;     /* LoROM file offset = (bank&0x7F)*0x8000 + (a-0x8000) */
    uint8_t expect[GWED_WS_PATCH_MAX_LEN];  /* vanilla signature, byte-exact */
    uint8_t len;
    GwedWsPatchEmitFn emit;
} GwedWsPatchSite;

/* Netplay agreement, published by the host before Arm.
 *
 * `active`         0 = offline / no peer: arm freely.
 *                  1 = a netplay session is starting.
 * `peer_ws_extra`  the host-published `SnesLobbyMatchCaps.ws_extra`, or -1
 *                  when no caps blob was received (a legacy peer). With a
 *                  peer present the patch arms only when this equals our own
 *                  margin exactly; anything else (including -1) leaves the
 *                  sprite bounds native while presentation stays wide.
 *
 * Call before every GwedWsPatch_Arm; the state is not remembered across
 * sessions on purpose (a rematch renegotiates). */
void GwedWsPatch_SetNetplaySession(int active, int peer_ws_extra);

/* Apply every enabled site whose bytes match its signature exactly.
 *
 * Declines the whole pass unless `g_ws_active` and `ws_extra > 0`, so no
 * caller can arm a 4:3 session by accident. Idempotent: arming an already
 * armed table at the same margin is a no-op. Must run AFTER SnesInit (the
 * cart image is created there) and after the netplay caps are known. */
void GwedWsPatch_Arm(int ws_extra);

/* Restore vanilla bytes at every site this module applied.
 *
 * Tolerant of the ROM image having been replaced underneath (a session
 * reboot mallocs a fresh one): a site whose current bytes are not the ones
 * this module wrote is left alone and the bookkeeping is cleared. Never
 * caches a ROM pointer for that reason. */
void GwedWsPatch_Disarm(void);

bool GwedWsPatch_IsArmed(void);

#ifdef __cplusplus
}
#endif
