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
    char source[128];
    char text[128];
    int present;
} TitleMenuLabel;

typedef struct TitleGlyph {
    unsigned char width;
    unsigned char height;
    unsigned short rows[16];
} TitleGlyph;

typedef struct TitleGlyphEntry {
    uint32_t codepoint;
    TitleGlyph glyph;
} TitleGlyphEntry;

static TitleMenuLabel g_title_labels[4];
static int g_title_menu_active;

static const TitleGlyph kEmptyGlyph = { 8, 8, { 0 } };
static const TitleGlyph kSpaceGlyph = { 5, 8, { 0 } };
static const TitleGlyph kDotGlyph = {
    2, 8, { 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0003, 0x0003 }
};
static const TitleGlyph kNativeTitleGlyphs[26] = {
    /* A */ { 8, 8, { 0x007e, 0x00ff, 0x00c3, 0x00df, 0x00df, 0x00c3, 0x00c3, 0x00c3 } },
    /* B */ { 8, 8, { 0x00fe, 0x00ff, 0x00c3, 0x00fe, 0x00ff, 0x00c3, 0x00ff, 0x00fe } },
    /* C */ { 8, 8, { 0x007e, 0x00ff, 0x00c0, 0x00c0, 0x00c0, 0x00c0, 0x00ff, 0x007e } },
    /* D */ { 8, 8, { 0x00fe, 0x00ff, 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00df, 0x00de } },
    /* E */ { 8, 8, { 0x007f, 0x00ff, 0x00e0, 0x00ff, 0x00ff, 0x00e0, 0x00ff, 0x00ff } },
    /* F */ { 8, 8, { 0x007f, 0x00ff, 0x00e0, 0x00ff, 0x00ff, 0x00e0, 0x00e0, 0x00e0 } },
    /* G */ { 8, 8, { 0x007f, 0x00ff, 0x00c0, 0x00c0, 0x00cf, 0x00c3, 0x00ff, 0x007f } },
    /* H */ { 8, 8, { 0x00c3, 0x00c3, 0x00c3, 0x00ff, 0x00ff, 0x00c3, 0x00c3, 0x00c3 } },
    /* I */ { 3, 8, { 0x0007, 0x0007, 0x0007, 0x0007, 0x0007, 0x0007, 0x0007, 0x0007 } },
    /* J */ { 8, 8, { 0x001f, 0x001f, 0x0006, 0x0006, 0x00c6, 0x00c6, 0x00fe, 0x007c } },
    /* K */ { 8, 8, { 0x00c3, 0x00c6, 0x00cc, 0x00f8, 0x00fc, 0x00ce, 0x00c7, 0x00c3 } },
    /* L */ { 7, 8, { 0x0070, 0x0070, 0x0070, 0x0070, 0x0070, 0x0070, 0x007f, 0x007f } },
    /* M */ { 8, 8, { 0x00fe, 0x00ff, 0x00db, 0x00db, 0x00db, 0x00db, 0x00db, 0x00db } },
    /* N */ { 8, 8, { 0x00fe, 0x00ff, 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00c3 } },
    /* O */ { 8, 8, { 0x007e, 0x00ff, 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00ff, 0x007e } },
    /* P */ { 8, 8, { 0x00fe, 0x00ff, 0x00c3, 0x00df, 0x00de, 0x00c0, 0x00c0, 0x00c0 } },
    /* Q */ { 8, 8, { 0x007e, 0x00ff, 0x00c3, 0x00c3, 0x00db, 0x00cf, 0x00ff, 0x007b } },
    /* R */ { 8, 8, { 0x00fe, 0x00ff, 0x00c3, 0x00df, 0x00de, 0x00c3, 0x00c3, 0x00c3 } },
    /* S */ { 8, 8, { 0x007f, 0x00ff, 0x00c0, 0x00fe, 0x007f, 0x0003, 0x00ff, 0x00fe } },
    /* T */ { 7, 8, { 0x007f, 0x007f, 0x001c, 0x001c, 0x001c, 0x001c, 0x001c, 0x001c } },
    /* U */ { 8, 8, { 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00ff, 0x007e } },
    /* V */ { 10, 8, { 0x0303, 0x0303, 0x0387, 0x01ce, 0x00cc, 0x00fc, 0x0078, 0x0030 } },
    /* W */ { 8, 8, { 0x00db, 0x00db, 0x00db, 0x00db, 0x00db, 0x00db, 0x00ff, 0x0066 } },
    /* X */ { 8, 8, { 0x00c3, 0x00e7, 0x007e, 0x003c, 0x003c, 0x007e, 0x00e7, 0x00c3 } },
    /* Y */ { 8, 8, { 0x00c3, 0x00c3, 0x00c3, 0x00c3, 0x00ff, 0x007e, 0x0018, 0x0018 } },
    /* Z */ { 8, 8, { 0x00ff, 0x00ff, 0x0006, 0x000c, 0x0018, 0x0030, 0x00ff, 0x00ff } },
};
static TitleGlyph g_title_glyphs[128];
static TitleGlyphEntry g_extra_title_glyphs[512];
static int g_extra_title_glyph_count;

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

static int hex_digit(char c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

static int append_utf8(char *out, size_t cap, size_t *n, uint32_t codepoint)
{
    unsigned char bytes[4];
    int count = 0;
    int i;

    if (codepoint <= 0x7fu) {
        bytes[count++] = (unsigned char)codepoint;
    } else if (codepoint <= 0x7ffu) {
        bytes[count++] = (unsigned char)(0xc0u | (codepoint >> 6));
        bytes[count++] = (unsigned char)(0x80u | (codepoint & 0x3fu));
    } else if (codepoint <= 0xffffu) {
        bytes[count++] = (unsigned char)(0xe0u | (codepoint >> 12));
        bytes[count++] = (unsigned char)(0x80u | ((codepoint >> 6) & 0x3fu));
        bytes[count++] = (unsigned char)(0x80u | (codepoint & 0x3fu));
    } else if (codepoint <= 0x10ffffu) {
        bytes[count++] = (unsigned char)(0xf0u | (codepoint >> 18));
        bytes[count++] = (unsigned char)(0x80u | ((codepoint >> 12) & 0x3fu));
        bytes[count++] = (unsigned char)(0x80u | ((codepoint >> 6) & 0x3fu));
        bytes[count++] = (unsigned char)(0x80u | (codepoint & 0x3fu));
    } else {
        return 0;
    }
    if (*n + (size_t)count >= cap)
        return 0;
    for (i = 0; i < count; i++)
        out[(*n)++] = (char)bytes[i];
    return 1;
}

static int parse_unicode_escape(const char **value, int digits,
                                uint32_t *codepoint)
{
    uint32_t cp = 0;
    int i;
    for (i = 0; i < digits; i++) {
        int digit = hex_digit((*value)[i]);
        if (digit < 0)
            return 0;
        cp = (cp << 4) | (uint32_t)digit;
    }
    *value += digits;
    *codepoint = cp;
    return 1;
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
            else if (c == 'u' || c == 'U') {
                uint32_t codepoint;
                if (!parse_unicode_escape(&value, c == 'u' ? 4 : 8,
                                          &codepoint))
                    return 0;
                if (!append_utf8(out, cap, &n, codepoint))
                    return 0;
                continue;
            }
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
           strcmp(language, "it") == 0 || strcmp(language, "pt") == 0 ||
           strcmp(language, "zh") == 0 || strcmp(language, "ko") == 0;
}

static void reset_title_glyphs(void)
{
    int i;
    memset(g_title_glyphs, 0, sizeof(g_title_glyphs));
    memset(g_extra_title_glyphs, 0, sizeof(g_extra_title_glyphs));
    g_extra_title_glyph_count = 0;
    g_title_glyphs[(unsigned char)' '] = kSpaceGlyph;
    g_title_glyphs[(unsigned char)'.'] = kDotGlyph;
    for (i = 0; i < 26; i++) {
        g_title_glyphs[(unsigned char)('A' + i)] = kNativeTitleGlyphs[i];
        g_title_glyphs[(unsigned char)('a' + i)] = kNativeTitleGlyphs[i];
    }
}

static int utf8_next(const char **text, uint32_t *codepoint)
{
    const unsigned char *s = (const unsigned char *)*text;
    if (!s || !*s)
        return 0;
    if (s[0] < 0x80u) {
        *codepoint = s[0];
        *text += 1;
        return 1;
    }
    if ((s[0] & 0xe0u) == 0xc0u && (s[1] & 0xc0u) == 0x80u) {
        *codepoint = ((uint32_t)(s[0] & 0x1fu) << 6) |
                     (uint32_t)(s[1] & 0x3fu);
        *text += 2;
        return 1;
    }
    if ((s[0] & 0xf0u) == 0xe0u && (s[1] & 0xc0u) == 0x80u &&
        (s[2] & 0xc0u) == 0x80u) {
        *codepoint = ((uint32_t)(s[0] & 0x0fu) << 12) |
                     ((uint32_t)(s[1] & 0x3fu) << 6) |
                     (uint32_t)(s[2] & 0x3fu);
        *text += 3;
        return 1;
    }
    if ((s[0] & 0xf8u) == 0xf0u && (s[1] & 0xc0u) == 0x80u &&
        (s[2] & 0xc0u) == 0x80u && (s[3] & 0xc0u) == 0x80u) {
        *codepoint = ((uint32_t)(s[0] & 0x07u) << 18) |
                     ((uint32_t)(s[1] & 0x3fu) << 12) |
                     ((uint32_t)(s[2] & 0x3fu) << 6) |
                     (uint32_t)(s[3] & 0x3fu);
        *text += 4;
        return 1;
    }
    *codepoint = s[0];
    *text += 1;
    return 1;
}

static const TitleGlyph *title_glyph(uint32_t codepoint)
{
    int i;
    const TitleGlyph *glyph;
    if (codepoint < 128u) {
        glyph = &g_title_glyphs[codepoint];
        if (glyph->width)
            return glyph;
        return &kEmptyGlyph;
    }
    for (i = 0; i < g_extra_title_glyph_count; i++) {
        if (g_extra_title_glyphs[i].codepoint == codepoint)
            return &g_extra_title_glyphs[i].glyph;
    }
    return &kEmptyGlyph;
}

static const TitleGlyph *next_title_glyph(const char **text)
{
    uint32_t codepoint;
    if (!utf8_next(text, &codepoint))
        return NULL;
    return title_glyph(codepoint);
}

static int text_width(const char *text, int scale)
{
    int width = 0;
    int chars = 0;
    while (text && *text) {
        const TitleGlyph *glyph = next_title_glyph(&text);
        if (!glyph)
            break;
        if (chars)
            width += scale;
        width += glyph->width * scale;
        chars++;
    }
    return width;
}

static int text_height(const char *text, int scale)
{
    int height = 8;
    while (text && *text) {
        const TitleGlyph *glyph = next_title_glyph(&text);
        if (!glyph)
            break;
        if (glyph->height > height)
            height = glyph->height;
    }
    return height * scale;
}

static int text_has_non_ascii(const char *text)
{
    while (text && *text) {
        if ((unsigned char)*text >= 128)
            return 1;
        text++;
    }
    return 0;
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
        const TitleGlyph *glyph = next_title_glyph(&source);
        int gy, gx;

        if (!glyph)
            break;
        for (gy = 0; gy < glyph->height; gy++) {
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
            if (is_menu_text_pixel(row[cx]))
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
        const TitleGlyph *glyph = next_title_glyph(&text);
        int gy, gx, sy, sx;
        if (!glyph)
            break;
        for (gy = 0; gy < glyph->height; gy++) {
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

static void fill_rect(uint32_t *pixels, int width, int height, int pitch_pixels,
                      int x, int y, int w, int h, uint32_t color)
{
    int yy, xx;
    int x0 = x;
    int y0 = y;
    int x1 = x + w;
    int y1 = y + h;

    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > width) x1 = width;
    if (y1 > height) y1 = height;
    for (yy = y0; yy < y1; yy++) {
        uint32_t *row = pixels + yy * pitch_pixels;
        for (xx = x0; xx < x1; xx++)
            row[xx] = color;
    }
}

static void draw_nonlatin_panel(uint32_t *pixels, int width, int height,
                                int pitch_pixels)
{
    fill_rect(pixels, width, height, pitch_pixels, 84, 136, 88, 49,
              rgb(8, 16, 41));
}

static void draw_label(uint32_t *pixels, int width, int height, int pitch_pixels,
                       const TitleMenuLabel *label, int selected,
                       int nonlatin_panel)
{
    uint32_t text = selected ? rgb(252, 244, 156) : rgb(64, 120, 88);
    uint32_t shadow = selected ? rgb(56, 120, 96) : rgb(24, 56, 48);
    int scale = 1;
    int tw = text_width(label->text, scale);
    int th = text_height(label->text, scale);
    int tx, ty;

    if (!nonlatin_panel)
        erase_source_label(pixels, width, height, pitch_pixels, label);
    tx = label->x + (label->w - tw) / 2;
    ty = label->text_y ? label->text_y : label->y + (label->h - th) / 2;
    if (th > 8 * scale)
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

static int parse_codepoint_string(const char *value, uint32_t *codepoint)
{
    char parsed[32];
    char *s;
    char *end;
    unsigned long cp;

    if (!parse_toml_string(value, parsed, sizeof(parsed)))
        return 0;
    s = parsed;
    if (s[0] == 'U' && s[1] == '+')
        s += 2;
    cp = strtoul(s, &end, 16);
    if (*end || cp > 0x10fffful)
        return 0;
    *codepoint = (uint32_t)cp;
    return 1;
}

static int parse_single_codepoint_string(const char *value, uint32_t *codepoint)
{
    char parsed[16];
    const char *s = parsed;
    uint32_t cp;

    if (!parse_toml_string(value, parsed, sizeof(parsed)))
        return 0;
    if (!utf8_next(&s, &cp) || *s)
        return 0;
    *codepoint = cp;
    return 1;
}

static int glyph_complete(const TitleGlyph *glyph, int row_count)
{
    int height = glyph->height ? glyph->height : 8;
    return glyph->width && height >= 1 && height <= 16 && row_count >= height;
}

static void store_loaded_glyph(uint32_t codepoint, const TitleGlyph *glyph)
{
    if (glyph->width < 1 || glyph->width > 16 || glyph->height < 1 ||
        glyph->height > 16 || !codepoint)
        return;
    if (codepoint < 128u) {
        g_title_glyphs[codepoint] = *glyph;
        if (codepoint >= 'A' && codepoint <= 'Z')
            g_title_glyphs['a' + (codepoint - 'A')] = *glyph;
        else if (codepoint >= 'a' && codepoint <= 'z')
            g_title_glyphs['A' + (codepoint - 'a')] = *glyph;
        return;
    }
    if (g_extra_title_glyph_count >=
        (int)(sizeof(g_extra_title_glyphs) / sizeof(g_extra_title_glyphs[0])))
        return;
    g_extra_title_glyphs[g_extra_title_glyph_count].codepoint = codepoint;
    g_extra_title_glyphs[g_extra_title_glyph_count].glyph = *glyph;
    g_extra_title_glyph_count++;
}

static void load_title_glyphs(const char *path)
{
    FILE *f;
    char line[512];
    TitleGlyph glyph;
    uint32_t codepoint = 0;
    int in_glyph = 0;
    int row_count = 0;

    if (!path)
        return;
    f = fopen(path, "rb");
    if (!f)
        return;

    memset(&glyph, 0, sizeof(glyph));
    glyph.height = 8;
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
            if (in_glyph && codepoint && glyph_complete(&glyph, row_count))
                store_loaded_glyph(codepoint, &glyph);
            memset(&glyph, 0, sizeof(glyph));
            glyph.height = 8;
            codepoint = 0;
            row_count = 0;
            in_glyph = 1;
            continue;
        }
        if (!in_glyph || !parse_key_value(s, &key, &value))
            continue;
        if (strcmp(key, "char") == 0) {
            (void)parsed;
            parse_single_codepoint_string(value, &codepoint);
        } else if (strcmp(key, "codepoint") == 0) {
            parse_codepoint_string(value, &codepoint);
        } else if (strcmp(key, "width") == 0) {
            glyph.width = (unsigned char)atoi(value);
        } else if (strcmp(key, "height") == 0) {
            glyph.height = (unsigned char)atoi(value);
        } else if (strncmp(key, "row", 3) == 0) {
            char *end;
            long row = strtol(key + 3, &end, 10);
            if (*end || row < 0 || row >= 16)
                continue;
            if (parse_glyph_row(value, &glyph.rows[row]))
                row_count++;
        }
    }
    if (in_glyph && codepoint && glyph_complete(&glyph, row_count))
        store_loaded_glyph(codepoint, &glyph);
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
    int nonlatin_panel;

    if (!g_title_menu_active || !pixels || pitch_pixels <= 0)
        return;
    if (!is_title_menu(pixels, width, height, pitch_pixels))
        return;

    selected = (int)(g_ram[0x0504] / 2);
    if (selected < 0 || selected > 3)
        selected = 0;

    nonlatin_panel = text_has_non_ascii(g_title_labels[0].text) ||
                     text_has_non_ascii(g_title_labels[1].text) ||
                     text_has_non_ascii(g_title_labels[2].text) ||
                     text_has_non_ascii(g_title_labels[3].text);
    if (nonlatin_panel)
        draw_nonlatin_panel(pixels, width, height, pitch_pixels);

    draw_label(pixels, width, height, pitch_pixels, &g_title_labels[0],
               selected == 0, nonlatin_panel);
    draw_label(pixels, width, height, pitch_pixels, &g_title_labels[1],
               selected == 1, nonlatin_panel);
    draw_label(pixels, width, height, pitch_pixels, &g_title_labels[2],
               selected == 2, nonlatin_panel);
    draw_label(pixels, width, height, pitch_pixels, &g_title_labels[3],
               selected == 3, nonlatin_panel);
}
