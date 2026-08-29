/* Codegen identity for Shin Kidou Senki Gundam W - Endless Duel.
 *
 * These digests are the single source of truth for "is this the right ROM".
 * tools/regen.sh and the CI workflow both read them from here via
 * rom_identity.txt, so there is one place to change when a revision changes.
 */

#include "codegen_setup.h"

const GameCodegenIdentity kGameCodegenIdentity = {
    .display_name   = "Shin Kidou Senki Gundam W - Endless Duel",
    .rom_file       = "Shin Kidou Senki Gundam W - Endless Duel (Japan).sfc",
    .expected_crc32 = "c0aecdca",
    .expected_sha256= "dd94308d822636c6ddf73c5e2644c84f2eb8fb4d9201150fc5f37d44d6f423f1",
    .mapping        = "lorom",
    .region         = "JPN",
    .cfg_dir        = "recomp",
    .out_dir        = "src/gen",
    .funcs_h        = "recomp/funcs.h",
};
