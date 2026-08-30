#include "title_menu_overlay.h"

#include "common_rtl.h"

#include <stdio.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

typedef struct TitleMenuLabel {
    int x, y, w, h;
    char id[32];
    char text[64];
    int present;
} TitleMenuLabel;

static TitleMenuLabel g_title_labels[4];
static int g_title_menu_active;

static const unsigned char kFont5x7[][7] = {
    /* space */ {0x00,0x00,0x00,0x00,0x00,0x00,0x00},
    /* . */     {0x00,0x00,0x00,0x00,0x00,0x0c,0x0c},
    /* A */     {0x0e,0x11,0x11,0x1f,0x11,0x11,0x11},
    /* B */     {0x1e,0x11,0x11,0x1e,0x11,0x11,0x1e},
    /* C */     {0x0f,0x10,0x10,0x10,0x10,0x10,0x0f},
    /* D */     {0x1e,0x11,0x11,0x11,0x11,0x11,0x1e},
    /* E */     {0x1f,0x10,0x10,0x1e,0x10,0x10,0x1f},
    /* F */     {0x1f,0x10,0x10,0x1e,0x10,0x10,0x10},
    /* G */     {0x0f,0x10,0x10,0x13,0x11,0x11,0x0f},
    /* H */     {0x11,0x11,0x11,0x1f,0x11,0x11,0x11},
    /* I */     {0x1f,0x04,0x04,0x04,0x04,0x04,0x1f},
    /* J */     {0x07,0x02,0x02,0x02,0x12,0x12,0x0c},
    /* K */     {0x11,0x12,0x14,0x18,0x14,0x12,0x11},
    /* L */     {0x10,0x10,0x10,0x10,0x10,0x10,0x1f},
    /* M */     {0x11,0x1b,0x15,0x15,0x11,0x11,0x11},
    /* N */     {0x11,0x19,0x15,0x13,0x11,0x11,0x11},
    /* O */     {0x0e,0x11,0x11,0x11,0x11,0x11,0x0e},
    /* P */     {0x1e,0x11,0x11,0x1e,0x10,0x10,0x10},
    /* Q */     {0x0e,0x11,0x11,0x11,0x15,0x12,0x0d},
    /* R */     {0x1e,0x11,0x11,0x1e,0x14,0x12,0x11},
    /* S */     {0x0f,0x10,0x10,0x0e,0x01,0x01,0x1e},
    /* T */     {0x1f,0x04,0x04,0x04,0x04,0x04,0x04},
    /* U */     {0x11,0x11,0x11,0x11,0x11,0x11,0x0e},
    /* V */     {0x11,0x11,0x11,0x11,0x11,0x0a,0x04},
    /* W */     {0x11,0x11,0x11,0x15,0x15,0x15,0x0a},
    /* X */     {0x11,0x11,0x0a,0x04,0x0a,0x11,0x11},
    /* Y */     {0x11,0x11,0x0a,0x04,0x04,0x04,0x04},
    /* Z */     {0x1f,0x01,0x02,0x04,0x08,0x10,0x1f},
};

enum {
    FONT_SPACE = 0,
    FONT_DOT = 1,
    FONT_A = 2,
};

static uint32_t rgb(unsigned r, unsigned g, unsigned b)
{
    return ((r & 0xffu) << 16) | ((g & 0xffu) << 8) | (b & 0xffu);
}

static void unpack(uint32_t p, int *r, int *g, int *b)
{
    *r = (int)((p >> 16) & 0xffu);
    *g = (int)((p >> 8) & 0xffu);
    *b = (int)(p & 0xffu);
}

static int near_color(uint32_t p, int r, int g, int b, int tolerance)
{
    int pr, pg, pb;
    unpack(p, &pr, &pg, &pb);
    return pr >= r - tolerance && pr <= r + tolerance &&
           pg >= g - tolerance && pg <= g + tolerance &&
           pb >= b - tolerance && pb <= b + tolerance;
}

static int is_title_menu(const uint32_t *pixels, int width, int height,
                         int pitch_pixels)
{
    if (width < 256 || height < 224)
        return 0;

    if (!near_color(pixels[34 * pitch_pixels + 119], 255, 255, 255, 18))
        return 0;
    if (!near_color(pixels[116 * pitch_pixels + 90], 57, 148, 255, 28))
        return 0;
    if (!near_color(pixels[180 * pitch_pixels + 180], 165, 206, 156, 36))
        return 0;
    if (!near_color(pixels[213 * pitch_pixels + 16], 8, 16, 41, 16))
        return 0;
    return 1;
}

static int is_menu_text_pixel(uint32_t p)
{
    int r, g, b;
    unpack(p, &r, &g, &b);

    if (r >= 135 && g >= 135 && b <= 210)
        return 1;
    if (g >= 70 && g >= r + 12 && b <= 150)
        return 1;
    return 0;
}

static char *trim(char *s)
{
    char *end;
    while (*s == ' ' || *s == '\t' || *s == '\r' || *s == '\n')
        s++;
    end = s + strlen(s);
    while (end > s &&
           (end[-1] == ' ' || end[-1] == '\t' ||
            end[-1] == '\r' || end[-1] == '\n')) {
        *--end = 0;
    }
    return s;
}

static void strip_comment(char *s)
{
    int quoted = 0;
    int escaped = 0;
    while (*s) {
        if (escaped) {
            escaped = 0;
        } else if (quoted && *s == '\\') {
            escaped = 1;
        } else if (*s == '"') {
            quoted = !quoted;
        } else if (!quoted && *s == '#') {
            *s = 0;
            return;
        }
        s++;
    }
}

static int parse_key_value(char *line, char **key, char **value)
{
    char *eq = strchr(line, '=');
    if (!eq)
        return 0;
    *eq = 0;
    *key = trim(line);
    *value = trim(eq + 1);
    return **key != 0;
}

static int parse_toml_string(const char *value, char *out, size_t cap)
{
    size_t n = 0;
    if (!value || value[0] != '"')
        return 0;
    value++;
    while (*value && *value != '"') {
        char c = *value++;
        if (c == '\\') {
            c = *value++;
            if (c == 'n') c = '\n';
            else if (c == 'r') c = '\r';
            else if (c == 't') c = '\t';
            else if (c != '\\' && c != '"') return 0;
        }
        if (n + 1 >= cap)
            return 0;
        out[n++] = c;
    }
    if (*value != '"')
        return 0;
    out[n] = 0;
    return 1;
}

static int label_slot(const char *id)
{
    if (strcmp(id, "story_mode") == 0) return 0;
    if (strcmp(id, "vs_mode") == 0) return 1;
    if (strcmp(id, "trial_mode") == 0) return 2;
    if (strcmp(id, "option") == 0) return 3;
    return -1;
}

static void reset_label(TitleMenuLabel *label)
{
    memset(label, 0, sizeof(*label));
}

static int language_supported(const char *language)
{
    return strcmp(language, "es") == 0 || strcmp(language, "fr") == 0 ||
           strcmp(language, "it") == 0 || strcmp(language, "pt") == 0;
}

static int glyph_index(char c)
{
    if (c == ' ')
        return FONT_SPACE;
    if (c == '.')
        return FONT_DOT;
    if (c >= 'A' && c <= 'Z')
        return FONT_A + (c - 'A');
    if (c >= 'a' && c <= 'z')
        return FONT_A + (c - 'a');
    return FONT_SPACE;
}

static int text_width(const char *text, int scale)
{
    int chars = 0;
    while (text && text[chars])
        chars++;
    if (!chars)
        return 0;
    return (chars * 6 - 1) * scale;
}

static uint32_t row_background(const uint32_t *pixels, int width, int height,
                               int pitch_pixels, const TitleMenuLabel *label)
{
    unsigned sr = 0, sg = 0, sb = 0, count = 0;
    int x0 = label->x + 10;
    int x1 = label->x + label->w - 16;
    int y0 = label->y;
    int y1 = label->y + label->h;
    int y, x;

    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > width) x1 = width;
    if (y1 > height) y1 = height;
    for (y = y0; y < y1; y++) {
        const uint32_t *row = pixels + y * pitch_pixels;
        for (x = x0; x < x1; x++) {
            int r, g, b;
            uint32_t p = row[x];
            if (is_menu_text_pixel(p))
                continue;
            unpack(p, &r, &g, &b);
            if (r >= 130 && g >= 130 && b >= 130)
                continue;
            sr += (unsigned)r;
            sg += (unsigned)g;
            sb += (unsigned)b;
            count++;
        }
    }
    if (!count)
        return rgb(8, 16, 41);
    return rgb(sr / count, sg / count, sb / count);
}

static void erase_source_label(uint32_t *pixels, int width, int height,
                               int pitch_pixels, const TitleMenuLabel *label)
{
    uint32_t bg = row_background(pixels, width, height, pitch_pixels, label);
    int x0 = label->x + 4;
    int x1 = label->x + label->w - 4;
    int y0 = label->y;
    int y1 = label->y + label->h;
    int y, x;

    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > width) x1 = width;
    if (y1 > height) y1 = height;
    if (x0 >= x1 || y0 >= y1)
        return;

    for (y = y0; y < y1; y++) {
        uint32_t *row = pixels + y * pitch_pixels;
        for (x = x0; x < x1; x++) {
            if (is_menu_text_pixel(row[x]))
                row[x] = bg;
        }
    }
}

static void draw_text(uint32_t *pixels, int width, int height, int pitch_pixels,
                      int x, int y, const char *text, uint32_t color,
                      int scale)
{
    int cursor = x;
    while (text && *text) {
        int gi = glyph_index(*text++);
        int gy, gx, sy, sx;
        const unsigned char *glyph = kFont5x7[gi];
        for (gy = 0; gy < 7; gy++) {
            for (gx = 0; gx < 5; gx++) {
                if (!(glyph[gy] & (1u << (4 - gx))))
                    continue;
                for (sy = 0; sy < scale; sy++) {
                    int py = y + gy * scale + sy;
                    if (py < 0 || py >= height)
                        continue;
                    for (sx = 0; sx < scale; sx++) {
                        int px = cursor + gx * scale + sx;
                        if (px >= 0 && px < width)
                            pixels[py * pitch_pixels + px] = color;
                    }
                }
            }
        }
        cursor += 6 * scale;
    }
}

static void draw_label(uint32_t *pixels, int width, int height, int pitch_pixels,
                       const TitleMenuLabel *label, int selected)
{
    uint32_t text = selected ? rgb(252, 244, 156) : rgb(64, 120, 88);
    uint32_t shadow = selected ? rgb(56, 120, 96) : rgb(24, 56, 48);
    int scale = 1;
    int tw = text_width(label->text, scale);
    int th = 7 * scale;
    int tx, ty;

    erase_source_label(pixels, width, height, pitch_pixels, label);
    tx = label->x + (label->w - tw) / 2;
    ty = label->y + (label->h - th) / 2;
    if (tx < label->x)
        tx = label->x;
    if (ty < label->y)
        ty = label->y;
    draw_text(pixels, width, height, pitch_pixels, tx + scale, ty + scale,
              label->text, shadow, scale);
    draw_text(pixels, width, height, pitch_pixels, tx, ty,
              label->text, text, scale);
}

int gwed_title_menu_overlay_load(const char *path, const char *language)
{
    FILE *f;
    char line[512];
    int current = -1;
    int i;

    gwed_title_menu_overlay_clear();
    if (!path || !language || !language_supported(language))
        return 0;

    f = fopen(path, "rb");
    if (!f)
        return 0;

    while (fgets(line, sizeof(line), f)) {
        char *s;
        char *key;
        char *value;
        char parsed[128];

        strip_comment(line);
        s = trim(line);
        if (!*s)
            continue;
        if (strcmp(s, "[[label]]") == 0) {
            current = -1;
            continue;
        }
        if (!parse_key_value(s, &key, &value))
            continue;
        if (strcmp(key, "id") == 0) {
            if (!parse_toml_string(value, parsed, sizeof(parsed))) {
                current = -1;
                continue;
            }
            current = label_slot(parsed);
            if (current >= 0) {
                reset_label(&g_title_labels[current]);
                snprintf(g_title_labels[current].id,
                         sizeof(g_title_labels[current].id), "%s", parsed);
                g_title_labels[current].present = 1;
            }
            continue;
        }
        if (current < 0)
            continue;
        if (strcmp(key, "x") == 0) {
            g_title_labels[current].x = atoi(value);
        } else if (strcmp(key, "y") == 0) {
            g_title_labels[current].y = atoi(value);
        } else if (strcmp(key, "width") == 0) {
            g_title_labels[current].w = atoi(value);
        } else if (strcmp(key, "height") == 0) {
            g_title_labels[current].h = atoi(value);
        } else if (strcmp(key, language) == 0) {
            parse_toml_string(value, g_title_labels[current].text,
                              sizeof(g_title_labels[current].text));
        }
    }
    fclose(f);

    for (i = 0; i < 4; i++) {
        if (!g_title_labels[i].present || !g_title_labels[i].text[0] ||
            g_title_labels[i].w <= 0 || g_title_labels[i].h <= 0) {
            gwed_title_menu_overlay_clear();
            return 0;
        }
    }
    g_title_menu_active = 1;
    return 1;
}

void gwed_title_menu_overlay_clear(void)
{
    memset(g_title_labels, 0, sizeof(g_title_labels));
    g_title_menu_active = 0;
}

void gwed_title_menu_overlay_apply(uint32_t *pixels, int width, int height,
                                   int pitch_pixels)
{
    int selected;

    if (!g_title_menu_active || !pixels || pitch_pixels <= 0)
        return;
    if (!is_title_menu(pixels, width, height, pitch_pixels))
        return;

    selected = (int)(g_ram[0x0504] / 2);
    if (selected < 0 || selected > 3)
        selected = 0;

    draw_label(pixels, width, height, pitch_pixels, &g_title_labels[0],
               selected == 0);
    draw_label(pixels, width, height, pitch_pixels, &g_title_labels[1],
               selected == 1);
    draw_label(pixels, width, height, pitch_pixels, &g_title_labels[2],
               selected == 2);
    draw_label(pixels, width, height, pitch_pixels, &g_title_labels[3],
               selected == 3);
}
