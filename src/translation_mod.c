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

static int gwed_file_readable(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    fclose(f);
    return 1;
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
    /* The table lives INSIDE the package, because the package is what gets
     * transferred to another player.
     *
     * It used to be resolved against the executable's directory, so the
     * package was a manifest describing a translation whose 1.8 MB of data
     * shipped separately with the build. A guest that downloaded the mod from
     * a host received the description and none of the data: it enabled
     * localization, failed to open the table, and ran the game untranslated
     * while reporting the same mod set as the host -- which is a desync the
     * transfer itself created.
     *
     * The executable-relative path stays as a fallback for an installation
     * laid out the old way; a package copy always wins so a transferred mod
     * behaves the same as a shipped one. */
    {
        char pkg_root[900];
        table_path[0] = '\0';
        if (snes_mod_runtime_package_root_c(GWED_LOCALIZATION_PACKAGE, NULL,
                                            pkg_root, sizeof(pkg_root))) {
            snprintf(table_path, sizeof(table_path), "%s/endless_duel.toml",
                     pkg_root);
            if (!gwed_file_readable(table_path))
                table_path[0] = '\0';
        }
        if (table_path[0]) {
            fprintf(stderr, "translation: table from the package: %s\n",
                    table_path);
        } else {
            if (!snesrecomp_exe_dir_path("translations/endless_duel.toml",
                                         table_path, sizeof(table_path))) {
                fprintf(stderr, "translation: cannot resolve table path\n");
                return;
            }
            /* Which copy was used decides whether a DOWNLOADED package works,
             * so it has to be in the log. A guest holding a package that
             * predates the table being put inside it lands here, and the only
             * previous sign was the exe-relative path in an error message. */
            fprintf(stderr, "translation: no table inside the package; falling "
                            "back to %s\n", table_path);
        }
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
