#include "title_menu_overlay.h"

#include "common_rtl.h"

#include <stdio.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

typedef struct TitleMenuLabel {
    int x, y, w, h;
    int text_y;
    int source_x, source_y;
    char id[32];
    char source[64];
    char text[64];
    int present;
} TitleMenuLabel;

typedef struct TitleGlyph {
    unsigned char width;
    unsigned short rows[8];
} TitleGlyph;

static TitleMenuLabel g_title_labels[4];
static int g_title_menu_active;

static const TitleGlyph kEmptyGlyph = { 8, { 0 } };
static const TitleGlyph kSpaceGlyph = { 5, { 0 } };
static const TitleGlyph kDotGlyph = {
    2, { 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0003, 0x0003 }
};
static const TitleGlyph kNativeTitleGlyphs[26] = {
    /* A */ { 8, { 0x007e, 0x00ff, 0x00c3, 0x00df, 0x00df, 0x00c3, 0x00c3, 0x00c3 } },
    /* B */ { 8, { 0x00fe, 0x00ff, 0x00c3, 0x00fe, 0x00ff, 0x00c3, 0x00ff, 0x00fe } },
    /* C */ { 8, { 0x007e, 0x00ff, 0x00c0, 0x00c0, 0x00c0, 0x00c0, 0x00ff, 0x007e } },
    /* D */ { 8, { 0x00fe, 0x00ff, 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00df, 0x00de } },
    /* E */ { 8, { 0x007f, 0x00ff, 0x00e0, 0x00ff, 0x00ff, 0x00e0, 0x00ff, 0x00ff } },
    /* F */ { 8, { 0x007f, 0x00ff, 0x00e0, 0x00ff, 0x00ff, 0x00e0, 0x00e0, 0x00e0 } },
    /* G */ { 8, { 0x007f, 0x00ff, 0x00c0, 0x00c0, 0x00cf, 0x00c3, 0x00ff, 0x007f } },
    /* H */ { 8, { 0x00c3, 0x00c3, 0x00c3, 0x00ff, 0x00ff, 0x00c3, 0x00c3, 0x00c3 } },
    /* I */ { 3, { 0x0007, 0x0007, 0x0007, 0x0007, 0x0007, 0x0007, 0x0007, 0x0007 } },
    /* J */ { 8, { 0x001f, 0x001f, 0x0006, 0x0006, 0x00c6, 0x00c6, 0x00fe, 0x007c } },
    /* K */ { 8, { 0x00c3, 0x00c6, 0x00cc, 0x00f8, 0x00fc, 0x00ce, 0x00c7, 0x00c3 } },
    /* L */ { 7, { 0x0070, 0x0070, 0x0070, 0x0070, 0x0070, 0x0070, 0x007f, 0x007f } },
    /* M */ { 8, { 0x00fe, 0x00ff, 0x00db, 0x00db, 0x00db, 0x00db, 0x00db, 0x00db } },
    /* N */ { 8, { 0x00fe, 0x00ff, 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00c3 } },
    /* O */ { 8, { 0x007e, 0x00ff, 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00ff, 0x007e } },
    /* P */ { 8, { 0x00fe, 0x00ff, 0x00c3, 0x00df, 0x00de, 0x00c0, 0x00c0, 0x00c0 } },
    /* Q */ { 8, { 0x007e, 0x00ff, 0x00c3, 0x00c3, 0x00db, 0x00cf, 0x00ff, 0x007b } },
    /* R */ { 8, { 0x00fe, 0x00ff, 0x00c3, 0x00df, 0x00de, 0x00c3, 0x00c3, 0x00c3 } },
    /* S */ { 8, { 0x007f, 0x00ff, 0x00c0, 0x00fe, 0x007f, 0x0003, 0x00ff, 0x00fe } },
    /* T */ { 7, { 0x007f, 0x007f, 0x001c, 0x001c, 0x001c, 0x001c, 0x001c, 0x001c } },
    /* U */ { 8, { 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00ff, 0x007e } },
    /* V */ { 10, { 0x0303, 0x0303, 0x0387, 0x01ce, 0x00cc, 0x00fc, 0x0078, 0x0030 } },
    /* W */ { 8, { 0x00db, 0x00db, 0x00db, 0x00db, 0x00db, 0x00db, 0x00ff, 0x0066 } },
    /* X */ { 8, { 0x00c3, 0x00e7, 0x007e, 0x003c, 0x003c, 0x007e, 0x00e7, 0x00c3 } },
    /* Y */ { 8, { 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00ff, 0x007e, 0x0018, 0x0018 } },
    /* Z */ { 8, { 0x00ff, 0x00ff, 0x0006, 0x000c, 0x0018, 0x0030, 0x00ff, 0x00ff } },
};
static TitleGlyph g_title_glyphs[128];

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

static void reset_title_glyphs(void)
{
    int i;
    memset(g_title_glyphs, 0, sizeof(g_title_glyphs));
    g_title_glyphs[(unsigned char)' '] = kSpaceGlyph;
    g_title_glyphs[(unsigned char)'.'] = kDotGlyph;
    for (i = 0; i < 26; i++) {
        g_title_glyphs[(unsigned char)('A' + i)] = kNativeTitleGlyphs[i];
        g_title_glyphs[(unsigned char)('a' + i)] = kNativeTitleGlyphs[i];
    }
}

static const TitleGlyph *title_glyph(char c)
{
    const TitleGlyph *glyph;
    if ((unsigned char)c >= 128)
        return &kEmptyGlyph;
    glyph = &g_title_glyphs[(unsigned char)c];
    if (glyph->width)
        return glyph;
    return &kEmptyGlyph;
}

static int text_width(const char *text, int scale)
{
    int width = 0;
    int chars = 0;
    while (text && text[chars]) {
        const TitleGlyph *glyph = title_glyph(text[chars]);
        if (chars)
            width += scale;
        width += glyph->width * scale;
        chars++;
    }
    return width;
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
    int scale = 1;
    int cursor = label->source_x ? label->source_x :
                 label->x + (label->w - text_width(label->source, scale) + 1) / 2;
    int y = label->source_y ? label->source_y :
            (label->text_y ? label->text_y : label->y);
    const char *source = label->source;
    int source_x = cursor;
    int source_w = text_width(source, scale);
    int cleanup_x0 = source_x - 1;
    int cleanup_x1 = source_x + source_w + 2;
    int cleanup_y0 = y - 1;
    int cleanup_y1 = y + 10;
    int cy, cx;

    while (source && *source) {
        const TitleGlyph *glyph = title_glyph(*source++);
        int gy, gx;

        for (gy = 0; gy < 8; gy++) {
            for (gx = 0; gx < glyph->width; gx++) {
                int px;
                int py;
                if (!(glyph->rows[gy] & (1u << (glyph->width - 1 - gx))))
                    continue;
                px = cursor + gx * scale;
                py = y + gy * scale;
                if (px >= 0 && px < width && py >= 0 && py < height)
                    pixels[py * pitch_pixels + px] = bg;
                px += scale;
                py += scale;
                if (px >= 0 && px < width && py >= 0 && py < height)
                    pixels[py * pitch_pixels + px] = bg;
            }
        }
        cursor += (glyph->width + 1) * scale;
    }

    if (cleanup_x0 < 0) cleanup_x0 = 0;
    if (cleanup_y0 < 0) cleanup_y0 = 0;
    if (cleanup_x1 > width) cleanup_x1 = width;
    if (cleanup_y1 > height) cleanup_y1 = height;
    for (cy = cleanup_y0; cy < cleanup_y1; cy++) {
        uint32_t *row = pixels + cy * pitch_pixels;
        for (cx = cleanup_x0; cx < cleanup_x1; cx++) {
            if (near_color(row[cx], 255, 247, 189, 18))
                row[cx] = bg;
        }
    }
}

static void draw_text(uint32_t *pixels, int width, int height, int pitch_pixels,
                      int x, int y, const char *text, uint32_t color,
                      int scale)
{
    int cursor = x;
    while (text && *text) {
        const TitleGlyph *glyph = title_glyph(*text++);
        int gy, gx, sy, sx;
        for (gy = 0; gy < 8; gy++) {
            for (gx = 0; gx < glyph->width; gx++) {
                if (!(glyph->rows[gy] & (1u << (glyph->width - 1 - gx))))
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
        cursor += (glyph->width + 1) * scale;
    }
}

static void draw_label(uint32_t *pixels, int width, int height, int pitch_pixels,
                       const TitleMenuLabel *label, int selected)
{
    uint32_t text = selected ? rgb(252, 244, 156) : rgb(64, 120, 88);
    uint32_t shadow = selected ? rgb(56, 120, 96) : rgb(24, 56, 48);
    int scale = 1;
    int tw = text_width(label->text, scale);
    int th = 8 * scale;
    int tx, ty;

    erase_source_label(pixels, width, height, pitch_pixels, label);
    tx = label->x + (label->w - tw) / 2;
    ty = label->text_y ? label->text_y : label->y + (label->h - th) / 2;
    if (tx < label->x)
        tx = label->x;
    if (ty < label->y)
        ty = label->y;
    draw_text(pixels, width, height, pitch_pixels, tx + scale, ty + scale,
              label->text, shadow, scale);
    draw_text(pixels, width, height, pitch_pixels, tx, ty,
              label->text, text, scale);
}

static int parse_glyph_row(const char *value, unsigned short *out)
{
    char parsed[32];
    char *end;
    unsigned long row;

    if (!parse_toml_string(value, parsed, sizeof(parsed)))
        return 0;
    row = strtoul(parsed, &end, 16);
    if (*end || row > 0xfffful)
        return 0;
    *out = (unsigned short)row;
    return 1;
}

static void store_loaded_glyph(char glyph_char, const TitleGlyph *glyph)
{
    if ((unsigned char)glyph_char >= 128 || glyph->width < 1 ||
        glyph->width > 16)
        return;
    g_title_glyphs[(unsigned char)glyph_char] = *glyph;
    if (glyph_char >= 'A' && glyph_char <= 'Z')
        g_title_glyphs[(unsigned char)('a' + (glyph_char - 'A'))] = *glyph;
    else if (glyph_char >= 'a' && glyph_char <= 'z')
        g_title_glyphs[(unsigned char)('A' + (glyph_char - 'a'))] = *glyph;
}

static void load_title_glyphs(const char *path)
{
    FILE *f;
    char line[512];
    TitleGlyph glyph;
    char glyph_char = 0;
    int in_glyph = 0;
    int row_count = 0;

    if (!path)
        return;
    f = fopen(path, "rb");
    if (!f)
        return;

    memset(&glyph, 0, sizeof(glyph));
    while (fgets(line, sizeof(line), f)) {
        char *s;
        char *key;
        char *value;
        char parsed[128];

        strip_comment(line);
        s = trim(line);
        if (!*s)
            continue;
        if (strcmp(s, "[[glyph]]") == 0) {
            if (in_glyph && glyph_char && glyph.width && row_count == 8)
                store_loaded_glyph(glyph_char, &glyph);
            memset(&glyph, 0, sizeof(glyph));
            glyph_char = 0;
            row_count = 0;
            in_glyph = 1;
            continue;
        }
        if (!in_glyph || !parse_key_value(s, &key, &value))
            continue;
        if (strcmp(key, "char") == 0) {
            if (parse_toml_string(value, parsed, sizeof(parsed)) &&
                parsed[0] && !parsed[1]) {
                glyph_char = parsed[0];
            }
        } else if (strcmp(key, "width") == 0) {
            glyph.width = (unsigned char)atoi(value);
        } else if (strncmp(key, "row", 3) == 0 && key[3] >= '0' &&
                   key[3] <= '7' && key[4] == 0) {
            int row = key[3] - '0';
            if (parse_glyph_row(value, &glyph.rows[row]))
                row_count++;
        }
    }
    if (in_glyph && glyph_char && glyph.width && row_count == 8)
        store_loaded_glyph(glyph_char, &glyph);
    fclose(f);
}

int gwed_title_menu_overlay_load(const char *path, const char *glyph_path,
                                 const char *language)
{
    FILE *f;
    char line[512];
    int current = -1;
    int i;

    gwed_title_menu_overlay_clear();
    reset_title_glyphs();
    if (!path || !language || !language_supported(language))
        return 0;
    load_title_glyphs(glyph_path);

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
        } else if (strcmp(key, "text_y") == 0) {
            g_title_labels[current].text_y = atoi(value);
        } else if (strcmp(key, "source_x") == 0) {
            g_title_labels[current].source_x = atoi(value);
        } else if (strcmp(key, "source_y") == 0) {
            g_title_labels[current].source_y = atoi(value);
        } else if (strcmp(key, "width") == 0) {
            g_title_labels[current].w = atoi(value);
        } else if (strcmp(key, "height") == 0) {
            g_title_labels[current].h = atoi(value);
        } else if (strcmp(key, "source") == 0) {
            parse_toml_string(value, g_title_labels[current].source,
                              sizeof(g_title_labels[current].source));
        } else if (strcmp(key, language) == 0) {
            parse_toml_string(value, g_title_labels[current].text,
                              sizeof(g_title_labels[current].text));
        }
    }
    fclose(f);

    for (i = 0; i < 4; i++) {
        if (!g_title_labels[i].present || !g_title_labels[i].source[0] ||
            !g_title_labels[i].text[0] || g_title_labels[i].w <= 0 ||
            g_title_labels[i].h <= 0) {
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
