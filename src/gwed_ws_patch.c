#include "gwed_ws_patch.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "snes/cart.h"
#include "snes/snes.h"
#include "widescreen.h"

extern Snes *g_snes;

/* ── The one site: the OAM emitter's X clip ──────────────────────────────
 *
 * $00:A03F..$00:A125 is the metasprite emitter (PHB/PHK/PLB .. PLB/RTS),
 * called by the display-list walker at $00:9FB2 once per requested object.
 * $00:A09D adds the object's screen X ($E0, 16-bit SIGNED, unclamped) to the
 * metasprite cell's relative X, and $00:A09F is the ONLY X clip anywhere in
 * the path — measured, m=0 x=0 (16-bit A):
 *
 *   $00:A09F  c9 00 01   CMP #$0100   ; C = A >= 256
 *   $00:A0A2  90 08      BCC $A0AC    ; accept 0..255, carry CLEAR
 *   $00:A0A4  c9 e0 ff   CMP #$ffe0   ; C = A >= -32
 *   $00:A0A7  b0 03      BCS $A0AC    ; accept -32..-1, carry SET
 *   $00:A0A9  4c 0e a1   JMP $A10E    ; skip: no OAM write at all
 *   $00:A0AC  08         PHP          ; <- the accepting carry is saved HERE
 *
 * THE TRAP (P8, and the reason "just widen the two immediates" is wrong):
 * the carry that decided acceptance is the SAME carry that $00:A0DB consumes
 * (`PLP` / `BCC $A0EF`) to decide whether to OR this sprite's 9th X bit into
 * the high OAM table at $0F00. Widening `CMP #$0100` to `CMP #$012B` would
 * make every right-margin sprite arrive with carry CLEAR and lose its 9th
 * bit, i.e. draw at X-256 instead of X. The replacement therefore splits the
 * two jobs: two compares decide accept/reject, then a THIRD compare against
 * the unchanged #$0100 regenerates the authentic 9th-bit carry.
 *
 *   $00:A09F  c9 2b 01   CMP #(256+extra)   ; 299
 *   $00:A0A2  90 05      BCC $A0A9          ; 0..298 -> accept
 *   $00:A0A4  c9 b5 ff   CMP #-(32+extra)   ; -75
 *   $00:A0A7  90 65      BCC $A10E          ; 299..-76 -> skip
 *   $00:A0A9  c9 00 01   CMP #$0100         ; C = 9th X bit, as vanilla
 *   $00:A0AC  08         PHP                ; unchanged
 *
 * Same 13 bytes, so nothing downstream shifts, and $A0AC still holds the PHP
 * that $A0C6/$A0DA pop.
 *
 * PROOF, and how to re-run it: scripts/widescreen/verify_oam_patch.py
 * simulates both 13-byte sequences over ALL 65536 accumulator values and
 * asserts (a) the accept set is exactly signed [-(32+extra), 256+extra-1],
 * (b) every X vanilla accepted keeps an IDENTICAL carry, (c) every accepted X
 * survives a round trip through the engine's PpuDecodeOamX at that margin,
 * and (d) exactly 2*extra positions are newly accepted. It also proves, by a
 * byte-exhaustive scan of bank $00 for every relative branch / BRL / JMP /
 * JSR, that nothing else in the bank enters $A09F..$A0AB. Pass the `emit=`
 * hex this file logs at arm time to `--bytes` and it checks the shipped
 * bytes with no shared source of truth.
 *
 * WHY THE 9-BIT BUDGET IS NOT AMBIGUOUS AT 43px, so no OAM hints are needed:
 * the sprites are 16x16/32x32 (OBSEL 0x62), so the widened accept range is
 * signed [-75, 298]. Negative X occupies raw OAM codes 437..511 and the right
 * margin occupies 256..298; 298 < 437, so the two bands do not overlap and
 * the engine's plain decode (ppu.c PpuDecodeOamX: raw >= 256 + extraRightCur
 * -> raw - 512) already reads both correctly. PpuWsSetOamRightHints exists
 * for games where they DO overlap; publishing a hint set here would be dead
 * code carrying a per-frame cost and a second thing to keep in sync, so this
 * feature deliberately does not. (Recon B's note that hints are "REQUIRED"
 * predates reading PpuDecodeOamX and is corrected in the Beads notes.)
 *
 * SITES DELIBERATELY NOT PATCHED (recorded so a later reader does not have to
 * re-derive them):
 *   $00:A0B6  the 13-byte Y twin (CMP #$00E0 / CMP #$FFE0, parks the sprite
 *             at $E0E0). 16:9 widens X only; it sits 23 bytes from this site
 *             and is easy to confuse with it.
 *   $00:A07F  the H-flip mirror (LDA #$FFF0 / #$FFE0 minus relX) — where the
 *             sprite sizes appear in code, not a clip.
 *   $04:9CE3  builds the per-scanline window table HDMA ch7 feeds to $2128
 *             for the shockwave effect, with its own X clip. Cosmetic
 *             follow-up (the effect will be unwindowed in the margins), and
 *             widening it would change a WRAM table, not a sprite bound.
 *
 * AND THERE IS NO P7 SITE. Measured over an 1800-frame live attract fight:
 * objects reached screen X 256..268 and STAYED LIVE there for up to ~330
 * guest frames with their metasprite pointers still animating, while no OAM
 * entry ever carried a signed X outside [0,255]. A lifetime cull would have
 * cleared the slot on the crossing frame. So this table has one entry and
 * SNESRECOMP_WS_CULL would be a switch for nothing; it is not defined.
 */

#define GWED_OAM_SITE_LEN      13
#define GWED_OAM_REJECT_PC     0xA10Eu   /* the emitter's skip continuation */
#define GWED_OAM_SITE_PC       0xA09Fu
#define GWED_NATIVE_SPRITE_BIAS 32       /* vanilla's -32 = the 32px sprite */

static int emit_oam_x_clip(int ws_extra, uint8_t *out, uint8_t len)
{
    if (len != GWED_OAM_SITE_LEN)
        return 0;
    /* The widened bounds are DERIVED, never a literal: at extra 43 they are
     * the 299 / -75 the proof script checks, and at any other margin the
     * script re-proves the same four properties from the same formula. */
    int right = 256 + ws_extra;                        /* first rejected +X */
    int left = -(GWED_NATIVE_SPRITE_BIAS + ws_extra);  /* first accepted -X */
    /* The reject branch is relative to the byte after it ($A0A9 = site + 10)
     * and must reach $A10E. One byte, signed. */
    int disp = (int)GWED_OAM_REJECT_PC - (int)(GWED_OAM_SITE_PC + 10u);

    /* Encoding limits, checked rather than assumed.
     *
     * The load-bearing one is the 9-bit OAM X budget: the right margin
     * occupies raw codes 256..256+extra-1 and accepted negatives occupy raw
     * 512-(32+extra)..511. If those bands ever met, a right-margin sprite and
     * an off-screen-left one would be indistinguishable and the engine's
     * plain PpuDecodeOamX reading would put one of them in the wrong place —
     * that is the condition under which PpuWsSetOamRightHints would become
     * necessary, and declining is the honest answer rather than shipping a
     * silently wrong margin. At extra 43 the bands are 298 and 437, so there
     * are 138 raw codes of headroom; the ceiling is extra 144. */
    if (ws_extra <= 0)
        return 0;
    if (256 + ws_extra - 1 >= 512 - (GWED_NATIVE_SPRITE_BIAS + ws_extra))
        return 0;
    if (disp < -128 || disp > 127)
        return 0;

    out[0]  = 0xC9;                              /* CMP #(256+extra)       */
    out[1]  = (uint8_t)(right & 0xFF);
    out[2]  = (uint8_t)((right >> 8) & 0xFF);
    out[3]  = 0x90;                              /* BCC $A0A9 (accept)     */
    out[4]  = 0x05;
    out[5]  = 0xC9;                              /* CMP #-(32+extra)       */
    out[6]  = (uint8_t)(left & 0xFF);
    out[7]  = (uint8_t)((left >> 8) & 0xFF);
    out[8]  = 0x90;                              /* BCC $A10E (skip)       */
    out[9]  = (uint8_t)(disp & 0xFF);
    out[10] = 0xC9;                              /* CMP #$0100 -> 9th bit  */
    out[11] = 0x00;
    out[12] = 0x01;
    return 1;
}

static const GwedWsPatchSite kSites[] = {
    {
        .name = "oam_x_clip",
        .env_kill = "SNESRECOMP_WS_OAM",
        .what = "OAM metasprite emitter X accept range (P8)",
        .pc24 = 0x00A09Fu,
        /* LoROM: (bank & 0x7F) * 0x8000 + (addr - 0x8000). */
        .rom_offset = 0x0000209Fu,
        .expect = { 0xC9, 0x00, 0x01, 0x90, 0x08, 0xC9, 0xE0, 0xFF,
                    0xB0, 0x03, 0x4C, 0x0E, 0xA1 },
        .len = GWED_OAM_SITE_LEN,
        .emit = &emit_oam_x_clip,
    },
};

#define GWED_WS_PATCH_SITE_COUNT \
    (int)(sizeof(kSites) / sizeof(kSites[0]))

/* Per-site runtime bookkeeping. `applied` bytes are what we WROTE, so Disarm
 * can tell "still mine" from "the ROM image was replaced underneath". */
typedef struct {
    int applied;
    uint8_t written[GWED_WS_PATCH_MAX_LEN];
} SiteState;

static SiteState s_state[GWED_WS_PATCH_SITE_COUNT];
static int s_armed;
static int s_armed_extra;
static int s_net_active;
static int s_net_peer_ws_extra = -1;

/* ── Kill switches (P14) ─────────────────────────────────────────────────
 * Two independent layers, both read fresh at arm time rather than cached:
 * arming happens once per session and a rematch may legitimately want a
 * different answer, so a static cache here would be a bug, not an
 * optimisation. Both are logged when they fire — a switch nobody can see the
 * state of gets blamed for the wrong thing. */

static bool site_env_enabled(const GwedWsPatchSite *site)
{
    const char *env;
    if (!site->env_kill)
        return true;
    env = getenv(site->env_kill);
    if (env && env[0] && atoi(env) == 0) {
        fprintf(stderr, "[ws-patch] %s=0 - %s disabled\n", site->env_kill,
                site->name);
        return false;
    }
    return true;
}

/* SNESRECOMP_WS_PATCH_DISABLE=<name>[,<name>...] — the generic escape hatch,
 * so a site added later needs no new env var to be bisectable. Matches whole
 * comma-separated tokens only, so "oam" never disables "oam_x_clip". */
static bool site_name_enabled(const GwedWsPatchSite *site)
{
    const char *list = getenv("SNESRECOMP_WS_PATCH_DISABLE");
    size_t n;
    if (!list || !list[0])
        return true;
    n = strlen(site->name);
    while (*list) {
        const char *sep = strchr(list, ',');
        size_t span = sep ? (size_t)(sep - list) : strlen(list);
        while (span && (list[span - 1] == ' ' || list[span - 1] == '\t'))
            --span;
        while (span && (*list == ' ' || *list == '\t')) { ++list; --span; }
        if (span == n && strncmp(list, site->name, n) == 0) {
            fprintf(stderr, "[ws-patch] SNESRECOMP_WS_PATCH_DISABLE names "
                            "%s - disabled\n", site->name);
            return false;
        }
        if (!sep)
            break;
        list = sep + 1;
    }
    return true;
}

/* Never cached: a session reboot frees the cart and mallocs a new image, so a
 * stored pointer would be a use-after-free on the next Disarm. */
static uint8_t *site_rom_ptr(const GwedWsPatchSite *site)
{
    if (!g_snes || !g_snes->cart || !g_snes->cart->rom)
        return NULL;
    if ((uint64_t)site->rom_offset + site->len > g_snes->cart->romSize)
        return NULL;
    return g_snes->cart->rom + site->rom_offset;
}

static void log_bytes(const char *label, const uint8_t *b, int len)
{
    int i;
    fprintf(stderr, "%s", label);
    for (i = 0; i < len; ++i)
        fprintf(stderr, "%02x", b[i]);
}

/* ── Netplay agreement ───────────────────────────────────────────────────
 *
 * Presentation widescreen needs no agreement at all (the PPU widescreen
 * fields sit outside ppu_saveload, so two peers looking at different aspect
 * ratios stay digest-equal). This patch does: it changes the OAM staging
 * WRAM the digest covers, so both peers must run it or neither may.
 *
 * The host publishes ws_extra in SnesLobbyMatchCaps (GwedFillMatchCaps in
 * main.c) and every guest receives the host's blob, so comparing our own
 * pinned margin against the negotiated one is a real agreement check and not
 * two peers each hoping. A mismatch — including a legacy peer that published
 * no caps at all (-1) — degrades to native sprite bounds on BOTH sides while
 * the wide backgrounds and HUD stay: the guest with the smaller feature set
 * is the one that decides, which is the only direction that converges. */
void GwedWsPatch_SetNetplaySession(int active, int peer_ws_extra)
{
    s_net_active = active ? 1 : 0;
    s_net_peer_ws_extra = peer_ws_extra;
}

static bool netplay_allows(int ws_extra)
{
    if (!s_net_active)
        return true;
    if (s_net_peer_ws_extra == ws_extra)
        return true;
    fprintf(stderr, "[ws-patch] netplay caps disagree (ours ws_extra=%d, "
                    "negotiated=%d) - sprite bounds stay native on both "
                    "peers; wide backgrounds and HUD are unaffected "
                    "(presentation-only, digest-safe)\n",
            ws_extra, s_net_peer_ws_extra);
    return false;
}

void GwedWsPatch_Arm(int ws_extra)
{
    int i, applied = 0, skipped = 0, declined = 0, disabled = 0;

    /* P16 by construction: this is the only place the guest can be touched,
     * and it refuses unless the widescreen session is actually live. A 4:3
     * session — package off, or SNESRECOMP_WS_EXTRA=0, which is exactly how
     * the P16 gate runs — writes nothing. */
    if (!g_ws_active || ws_extra <= 0) {
        if (s_armed)
            GwedWsPatch_Disarm();
        return;
    }
    if (s_armed && s_armed_extra == ws_extra)
        return;    /* idempotent: same margin, already applied */
    if (s_armed)
        GwedWsPatch_Disarm();
    if (!netplay_allows(ws_extra))
        return;

    for (i = 0; i < GWED_WS_PATCH_SITE_COUNT; ++i) {
        const GwedWsPatchSite *site = &kSites[i];
        uint8_t out[GWED_WS_PATCH_MAX_LEN];
        uint8_t *rom;

        if (!site_env_enabled(site) || !site_name_enabled(site)) {
            ++disabled;
            continue;
        }
        rom = site_rom_ptr(site);
        if (!rom) {
            fprintf(stderr, "[ws-patch] %s SKIPPED: no ROM image at file "
                            "offset 0x%X (+%u)\n",
                    site->name, (unsigned)site->rom_offset, site->len);
            ++skipped;
            continue;
        }
        /* Fail closed. The signature is the whole safety argument: these
         * bytes were read off THIS ROM revision, and a mismatch means either
         * a different dump or another mod got here first. Writing anyway
         * would corrupt the emitter. */
        if (memcmp(rom, site->expect, site->len) != 0) {
            fprintf(stderr, "[ws-patch] %s SKIPPED at $%06X: signature "
                            "mismatch, ", site->name, (unsigned)site->pc24);
            log_bytes("got ", rom, site->len);
            log_bytes(" want ", site->expect, site->len);
            fprintf(stderr, "\n");
            ++skipped;
            continue;
        }
        memset(out, 0, sizeof(out));
        if (!site->emit(ws_extra, out, site->len)) {
            fprintf(stderr, "[ws-patch] %s DECLINED at $%06X: emitter has no "
                            "same-length encoding for ws_extra=%d\n",
                    site->name, (unsigned)site->pc24, ws_extra);
            ++declined;
            continue;
        }
        memcpy(rom, out, site->len);
        memcpy(s_state[i].written, out, site->len);
        s_state[i].applied = 1;
        ++applied;
        fprintf(stderr, "[ws-patch] %s APPLIED at $%06X (+%u, ws_extra=%d) - "
                        "%s: ", site->name, (unsigned)site->pc24, site->len,
                ws_extra, site->what);
        log_bytes("expect=", site->expect, site->len);
        log_bytes(" emit=", out, site->len);
        fprintf(stderr, "\n");
    }

    s_armed = applied > 0;
    s_armed_extra = s_armed ? ws_extra : 0;
    fprintf(stderr, "[ws-patch] arm ws_extra=%d: %d applied, %d skipped, "
                    "%d declined, %d disabled (of %d sites)\n",
            ws_extra, applied, skipped, declined, disabled,
            GWED_WS_PATCH_SITE_COUNT);
}

void GwedWsPatch_Disarm(void)
{
    int i, restored = 0, stale = 0;

    for (i = 0; i < GWED_WS_PATCH_SITE_COUNT; ++i) {
        const GwedWsPatchSite *site = &kSites[i];
        uint8_t *rom;
        if (!s_state[i].applied)
            continue;
        rom = site_rom_ptr(site);
        /* Only restore bytes that are still the ones we wrote. A session
         * reboot hands us a brand new pristine image; overwriting it with
         * `expect` would be harmless but would also hide the fact that our
         * bookkeeping was stale, and if a third party had rewritten the site
         * we would be clobbering them. */
        if (rom && memcmp(rom, s_state[i].written, site->len) == 0) {
            memcpy(rom, site->expect, site->len);
            ++restored;
        } else {
            ++stale;
        }
        s_state[i].applied = 0;
        memset(s_state[i].written, 0, sizeof(s_state[i].written));
    }
    if (restored || stale)
        fprintf(stderr, "[ws-patch] disarm: %d restored, %d stale (ROM image "
                        "replaced or overwritten elsewhere)\n",
                restored, stale);
    s_armed = 0;
    s_armed_extra = 0;
    /* Agreement is per session: a rematch renegotiates and re-publishes. */
    s_net_active = 0;
    s_net_peer_ws_extra = -1;
}

bool GwedWsPatch_IsArmed(void) { return s_armed != 0; }
