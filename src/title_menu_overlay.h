#ifndef TITLE_MENU_OVERLAY_H
#define TITLE_MENU_OVERLAY_H

#include <stdint.h>

int gwed_title_menu_overlay_load(const char *path, const char *glyph_path,
                                 const char *language);
int gwed_option_menu_overlay_load(const char *path, const char *language);
void gwed_title_menu_overlay_clear(void);
void gwed_title_menu_overlay_prepare_frame(void);
void gwed_title_menu_overlay_apply(uint32_t *pixels, int width, int height,
                                   int pitch_pixels);

#endif /* TITLE_MENU_OVERLAY_H */
