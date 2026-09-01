#include <stdio.h>
#include <string.h>

#include "host_paths.h"
#include "mod_runtime.h"
#include "snes_text_xlate.h"

#define GWED_LOCALIZATION_PACKAGE "gwed.localization"
#define GWED_LOCALIZATION_FEATURE "localization"
#define GWED_LOCALIZATION_PLUGIN  "gwed.localization.runtime"

static void gwed_translation_reset(void)
{
    snes_text_xlate_shutdown_c();
}

static void gwed_translation_frame(void)
{
    snes_text_xlate_on_frame_c();
}

static void gwed_translation_activate(void)
{
    char language[32] = "en";
    char table_path[1024];

    if (!snes_mod_runtime_feature_enabled_c(GWED_LOCALIZATION_PACKAGE,
                                            GWED_LOCALIZATION_FEATURE))
        return;
    if (!snes_mod_runtime_feature_option_value_c(GWED_LOCALIZATION_PACKAGE,
                                                 GWED_LOCALIZATION_FEATURE,
                                                 "language", language,
                                                 sizeof(language))) {
        snprintf(language, sizeof(language), "en");
    }
    if (strcmp(language, "off") == 0)
        return;
    if (!snesrecomp_exe_dir_path("translations/endless_duel.toml",
                                 table_path, sizeof(table_path))) {
        fprintf(stderr, "translation: cannot resolve table path\n");
        return;
    }
    /* Every surface is translated in guest memory: ROM text patches, RAM
     * patches and guarded VRAM tile-art interception. Nothing is drawn over
     * the presented frame, so no host overlay runs here. */
    if (!snes_text_xlate_init_c(table_path, language)) {
        fprintf(stderr, "translation: %s\n", snes_text_xlate_last_error_c());
        return;
    }
    snes_mod_register_frame_callback(gwed_translation_frame);
    snes_text_xlate_on_frame_c();
}

SNES_MOD_CONSTRUCTOR(gwed_translation_register)
{
    snes_mod_register_reset_callback(gwed_translation_reset);
    snes_mod_register_activation_plugin(GWED_LOCALIZATION_PLUGIN,
                                        gwed_translation_activate);
}
