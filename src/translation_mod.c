#include <stdio.h>
#include <string.h>

#include "host_paths.h"
#include "mod_runtime.h"
#include "snes_text_xlate.h"
#include "title_menu_overlay.h"

#define GWED_LOCALIZATION_PACKAGE "gwed.localization"
#define GWED_LOCALIZATION_FEATURE "localization"
#define GWED_LOCALIZATION_PLUGIN  "gwed.localization.runtime"

static void gwed_translation_reset(void)
{
    gwed_title_menu_overlay_clear();
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
    char title_menu_path[1024];

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
        gwed_title_menu_overlay_clear();
        return;
    }
    if (snesrecomp_exe_dir_path("translations/endless_duel_title_menu.toml",
                                title_menu_path, sizeof(title_menu_path))) {
        gwed_title_menu_overlay_load(title_menu_path, language);
    }
    if (!snes_text_xlate_init_c(table_path, language)) {
        fprintf(stderr, "translation: %s\n", snes_text_xlate_last_error_c());
        gwed_title_menu_overlay_clear();
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
