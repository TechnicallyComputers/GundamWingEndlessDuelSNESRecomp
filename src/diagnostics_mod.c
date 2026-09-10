/*
 * Performance diagnostic log — the opt-in mod that turns "it stutters
 * sometimes" into a file somebody can read.
 *
 * WHY THIS EXISTS. Lag spikes were reported on hardware comparable to the
 * machine the game is built on, which is exactly the case that cannot be
 * chased by description: "occasional hitching" is equally consistent with a
 * slow emulated frame, a blocked present, an audio underrun forcing a catch-up
 * burst, a netplay rollback resim, and a window manager stealing 40 ms. This
 * writes down which one it was, on the player's machine, with numbers.
 *
 * WHAT IT MEASURES, and the one thing it cannot. The mod runtime's frame hook
 * fires from RtlRunFrame (common_rtl.c) once per emulated frame, so the
 * interval between two calls is one whole trip round the host loop: emulate,
 * present, frame-limit or vsync wait, event pump. That is the number the
 * player feels. It is NOT a breakdown — this cannot say "the present blocked"
 * versus "the CPU was slow", because a mod is handed one callback and no
 * instrumentation points inside the loop. It can say WHEN, HOW BIG, HOW OFTEN
 * and WHAT ELSE WAS TRUE, which is enough to decide where to instrument next,
 * and it is honest about the boundary rather than implying a breakdown it
 * never had.
 *
 * WHY IT CANNOT DESYNC A MATCH. It reads a clock and writes text. No guest
 * memory, no PPU or APU register, no save state, no decision that feeds back
 * into the simulation — and, unlike the widescreen and localization packages,
 * nothing here is even part of the digest surface, so one peer may run it and
 * the other not. The clock is read at a fixed point in a frame that has
 * already been simulated, so its own cost cannot shift the guest either.
 *
 * DIAGNOSTIC TOOL CONTRACT (the studio rule, and it is load-bearing here):
 * line-buffer and flush every line. A crash, a hang or a hard power-off is
 * precisely the session whose log matters most, and a buffered file loses the
 * last and most interesting seconds of it. A dead probe must never read as a
 * clean run either, which is why the header records what could not be
 * measured and the footer says how the session ended.
 */

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "common_rtl.h"      /* snes_frame_counter */
#include "gwed_display.h"
#include "host_paths.h"
#include "mod_runtime.h"
#include "desktop/sdl_compat.h"

#if defined(SNES_HAS_LOBBY_CLIENT)
#include "netplay/snes_netplay.h"
#endif

#ifndef GWED_BUILD_ID
#define GWED_BUILD_ID "unknown"
#endif

#define GWED_DIAG_PACKAGE "gwed.diagnostics"
#define GWED_DIAG_FEATURE "perf_log"
#define GWED_DIAG_PLUGIN  "gwed.diagnostics.perflog"

/* The authentic SNES frame, and the budget every measurement is expressed
 * against. 60.0988 Hz, not 60: pacing a 60.0988 Hz guest off a 60.00 Hz
 * display duplicates a frame every ten seconds, and a diagnostic that called
 * the budget 16.67 ms would report that as a spike every ten seconds. */
#define GWED_DIAG_HZ        60.0988
#define GWED_DIAG_BUDGET_MS (1000.0 / GWED_DIAG_HZ)

/* Buckets are multiples of the budget. The last one is unbounded: a 4x frame
 * and a 40x frame are different problems and must not share a column. */
enum { kBucketCount = 6 };
static const double kBucketMax[kBucketCount] = {
    1.25, 1.5, 2.0, 4.0, 8.0, 0.0 /* 0 = no upper bound */
};
static const char *const kBucketLabel[kBucketCount] = {
    "<=1.25x", "<=1.5x", "<=2x", "<=4x", "<=8x", ">8x"
};

static FILE *s_log;
static int   s_active;
static int   s_every_frame;

/* Present cost, published by the host (diagnostics_mod.h). Consumed by the
 * next frame line, so a value is reported once and never twice. */
static double s_present_ms = -1.0;
/* Per-iteration present breakdown; -1 means "did not run this iteration".
 * Cleared with s_present_ms so a fast-forward burst cannot report another
 * iteration's phases as its own. */
static double s_ph_upload = -1.0, s_ph_clear = -1.0, s_ph_blit = -1.0;
static double s_ph_overlay = -1.0, s_ph_osd = -1.0, s_ph_swap = -1.0;
/* Worst swap seen in the current window, and the last host-loop event. The
 * event is echoed on the next spike so cause and effect sit on adjacent
 * lines instead of hundreds of frame lines apart. */
static double s_swap_max_ms;
static double s_lp_emulate = -1.0, s_lp_pump = -1.0, s_lp_limit = -1.0;
static char s_last_event[160];
static double s_last_event_at_s = -1.0;
static double s_present_max_ms;
static double s_present_sum_ms;
static int    s_present_count;

/* Video facts, stashed until the log is open. Printed once. */
static int  s_video_known;
static char s_video_line[320];
static double s_spike_ms = 33.0;

static Uint64 s_perf_freq;
static Uint64 s_t_start;
static Uint64 s_t_prev;          /* previous frame hook, 0 = none yet */
static Uint64 s_t_window;        /* start of the current one-second window */

/* Whole-session and current-window accumulators. Kept apart so the footer
 * reports the session rather than whatever the last partial second held. */
typedef struct {
    unsigned long frames;
    double sum_ms;
    double max_ms;
    double min_ms;
    unsigned long spikes;
    unsigned long buckets[kBucketCount];
} DiagStats;

static DiagStats s_window;
static DiagStats s_session;
static double s_worst_at_s;      /* when the session's worst frame happened */
static int s_worst_frame;
/* Frames the hook never saw. RtlRunFrame drives it, so a gap in
 * snes_frame_counter means frames were simulated with this mod inactive --
 * a session reset, or the feature toggled mid-session. Reported rather than
 * quietly averaged in, because a gap looks exactly like a stall otherwise. */
static int s_prev_frame_counter;
static unsigned long s_counter_gaps;

static void stats_reset(DiagStats *s)
{
    memset(s, 0, sizeof(*s));
    s->min_ms = 1.0e18;
}

static void stats_add(DiagStats *s, double ms)
{
    int i;
    double ratio = ms / GWED_DIAG_BUDGET_MS;
    s->frames++;
    s->sum_ms += ms;
    if (ms > s->max_ms) s->max_ms = ms;
    if (ms < s->min_ms) s->min_ms = ms;
    for (i = 0; i < kBucketCount; ++i) {
        if (kBucketMax[i] == 0.0 || ratio <= kBucketMax[i]) {
            s->buckets[i]++;
            break;
        }
    }
}

/* Every write goes through here: one line, one flush, no exceptions. */
static void diag_line(const char *fmt, ...)
{
    va_list ap;
    if (!s_log)
        return;
    va_start(ap, fmt);
    vfprintf(s_log, fmt, ap);
    va_end(ap);
    fputc('\n', s_log);
    fflush(s_log);
}

static double seconds_since_start(Uint64 now)
{
    if (!s_perf_freq)
        return 0.0;
    return (double)(now - s_t_start) / (double)s_perf_freq;
}

/* Beside the executable, which is where this game keeps everything the player
 * owns (config.ini, keybinds.ini, saves/) -- so a rebuilt game logs next to
 * the rebuilt binary rather than into whatever directory it happened to be
 * started from. Falls back to the working directory when the exe path is not
 * knowable, and says so, because a log the player cannot find is a log that
 * was never written. */
static int diag_open_log(char *path, size_t cap)
{
    char prev[1200];

    if (!snesrecomp_exe_dir_path("gwed_diagnostics.log", path, cap))
        snprintf(path, cap, "gwed_diagnostics.log");

    /* Keep exactly one previous session. A player asked for a log after the
     * stutter usually restarts the game first, and without this the run that
     * showed the problem is the run that gets overwritten. */
    if (snprintf(prev, sizeof(prev), "%s.prev", path) < (int)sizeof(prev)) {
        remove(prev);
        rename(path, prev);
    }
    s_log = fopen(path, "w");
    return s_log != NULL;
}

static const char *diag_yes_no(int v) { return v ? "yes" : "no"; }

void GwedDiag_NoteVideo(struct SDL_Window *window_in,
                        struct SDL_Renderer *renderer_in, int vsync_on)
{
    SDL_Window *window = (SDL_Window *)window_in;
    SDL_Renderer *renderer = (SDL_Renderer *)renderer_in;
    int win_w = 0, win_h = 0, draw_w = 0, draw_h = 0;
    const char *backend = "unknown";
    double scale = 0.0;
    int fullscreen = 0;
    double hz = 0.0;

    if (!window)
        return;

    SDL_GetWindowSize(window, &win_w, &win_h);
#if SNESRECOMP_SDL3
    SDL_GetWindowSizeInPixels(window, &draw_w, &draw_h);
    fullscreen = (SDL_GetWindowFlags(window) & SDL_WINDOW_FULLSCREEN) != 0;
    {
        const SDL_DisplayMode *m =
            SDL_GetCurrentDisplayMode(SDL_GetDisplayForWindow(window));
        if (m) hz = m->refresh_rate;
    }
#else
    SDL_GL_GetDrawableSize(window, &draw_w, &draw_h);
    if (draw_w <= 0) { draw_w = win_w; draw_h = win_h; }
    fullscreen = (SDL_GetWindowFlags(window) &
                  (SDL_WINDOW_FULLSCREEN | SDL_WINDOW_FULLSCREEN_DESKTOP)) != 0;
    {
        SDL_DisplayMode m;
        int idx = SDL_GetWindowDisplayIndex(window);
        if (idx >= 0 && SDL_GetCurrentDisplayMode(idx, &m) == 0)
            hz = (double)m.refresh_rate;
    }
#endif
    if (renderer && snesrecomp_sdl_renderer_name(renderer))
        backend = snesrecomp_sdl_renderer_name(renderer);
    if (win_w > 0) scale = (double)draw_w / (double)win_w;

    /* The ratio is the finding, not the sizes. A process that is DPI-aware
     * fills physical pixels itself; one that is not gets bitmap-scaled by the
     * compositor. Either way the cost lands inside the measured interval, and
     * only the measurement can say which happened on this machine. */
    snprintf(s_video_line, sizeof(s_video_line),
             "# video   renderer=%s window=%dx%d drawable=%dx%d scale=%.2fx "
             "fullscreen=%s vsync=%s display=%.3fHz  (drawable/window is the "
             "DPI scale this process actually got)",
             backend, win_w, win_h, draw_w, draw_h, scale,
             diag_yes_no(fullscreen), diag_yes_no(vsync_on), hz);
    s_video_known = 1;
    /* Already logging: the header has been and gone, so print it now rather
     * than lose it. */
    if (s_log)
        diag_line("%s", s_video_line);
}

void GwedDiag_NotePresentMs(double ms)
{
    if (!s_active)
        return;
    s_present_ms = ms;
    if (ms > s_present_max_ms) s_present_max_ms = ms;
    s_present_sum_ms += ms;
    s_present_count++;
}

void GwedDiag_NotePresentPhases(double upload_ms, double clear_ms,
                                double blit_ms, double overlay_ms,
                                double osd_ms, double swap_ms)
{
    if (!s_active)
        return;
    s_ph_upload = upload_ms; s_ph_clear = clear_ms; s_ph_blit = blit_ms;
    s_ph_overlay = overlay_ms; s_ph_osd = osd_ms; s_ph_swap = swap_ms;
    if (swap_ms > s_swap_max_ms) s_swap_max_ms = swap_ms;
}

void GwedDiag_NoteLoopPhases(double emulate_ms, double pump_ms,
                             double limit_ms)
{
    if (!s_active)
        return;
    s_lp_emulate = emulate_ms;
    s_lp_pump = pump_ms;
    s_lp_limit = limit_ms;
}

void GwedDiag_NoteEvent(const char *what)
{
    if (!s_active || !what || !*what)
        return;
    snprintf(s_last_event, sizeof(s_last_event), "%s", what);
    s_last_event_at_s = seconds_since_start(SDL_GetPerformanceCounter());
    diag_line("[%8.2fs] EVENT %s", s_last_event_at_s, s_last_event);
}

static void diag_write_header(const char *path)
{
    time_t now = time(NULL);
    char stamp[64];
    struct tm *tm_now = localtime(&now);
    const char *video = "unknown";
    const char *audio = "unknown";

    stamp[0] = '\0';
    if (tm_now)
        strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", tm_now);

    /* SDL is already up by the time a frame has run, so these are the drivers
     * actually in use rather than whatever the config asked for. */
    if (SDL_GetCurrentVideoDriver()) video = SDL_GetCurrentVideoDriver();
    if (SDL_GetCurrentAudioDriver()) audio = SDL_GetCurrentAudioDriver();

    diag_line("# Gundam Wing Endless Duel — performance log");
    diag_line("# file    %s", path);
    diag_line("# started %s", stamp[0] ? stamp : "(unknown time)");
    diag_line("# build   %s", GWED_BUILD_ID);
    /* SDL3 renamed SDL_GetCPUCount; the compat header's old-name aliases do
     * not cover it, so spell both rather than let one backend fail to
     * build. */
    diag_line("# host    %s %d logical cpu(s), %d MB ram, video=%s audio=%s",
              SDL_GetPlatform(),
#if SNESRECOMP_SDL3
              SDL_GetNumLogicalCPUCores(),
#else
              SDL_GetCPUCount(),
#endif
              SDL_GetSystemRAM(), video, audio);
    diag_line("# display widescreen=%s frame_width=%d",
              diag_yes_no(GwedDisplay_IsWidescreenActive()),
              GwedDisplay_GetCurrentFrameWidth());
    if (s_video_known)
        diag_line("%s", s_video_line);
    else
        diag_line("# video   not published yet — the window did not exist when "
                  "logging began");
#if defined(SNES_HAS_LOBBY_CLIENT)
    diag_line("# netplay %s at start", diag_yes_no(snes_netplay_active()));
#else
    diag_line("# netplay not built into this binary");
#endif
    diag_line("# budget  %.3f ms per frame (%.4f Hz); spike threshold %.0f ms "
              "(%.2fx budget)",
              GWED_DIAG_BUDGET_MS, GWED_DIAG_HZ, s_spike_ms,
              s_spike_ms / GWED_DIAG_BUDGET_MS);
    diag_line("#");
    diag_line("# What one line means: the wall time between the end of one "
              "emulated frame and");
    diag_line("# the end of the next -- emulate, present, vsync or limiter "
              "wait, event pump,");
    diag_line("# all of it. It is what the player feels. It is not a "
              "breakdown of where the");
    diag_line("# time went inside that trip. The host now also reports its "
              "PRESENT cost");
    diag_line("# (first draw call to SDL_RenderPresent returning) as "
              "'present=' on a frame");
    diag_line("# line, which splits that trip into roughly emulate-and-wait "
              "versus put-it");
    diag_line("# on the screen. A present belongs to a host ITERATION: under "
              "fast-forward one");
    diag_line("# present covers several emulated frames, so it is reported on "
              "the frame it");
    diag_line("# landed on rather than divided between them.");
    diag_line("#");
    diag_line("# READ present= WITH THE VSYNC FIELD ABOVE -- but do NOT assume "
              "the vsync wait");
    diag_line("# is inside it. WHERE the wait lands is a property of the "
              "backend, not of the");
    diag_line("# setting. On SDL's Vulkan renderer the FIFO block is taken in "
              "vkAcquireNextImage");
    diag_line("# at the START of the next frame -- before the first draw call "
              "that starts this");
    diag_line("# timer -- so a healthy vsync-ON run here reads present ~= 1.5 "
              "ms, not ~= budget.");
    diag_line("# Measured: vsync on, vulkan, 60.000 Hz panel, present avg 1.46 "
              "ms over 6134");
    diag_line("# frames. A backend that blocks inside SDL_RenderPresent "
              "instead will read");
    diag_line("# present ~= budget and be equally healthy. Establish which "
              "shape THIS build");
    diag_line("# shows when it is behaving, and read departures from that.");
    diag_line("#");
    diag_line("# What always means work, on any backend: a SPIKE whose time is "
              "mostly present.");
    diag_line("# On a backend that waits elsewhere (the vulkan case above) "
              "that reading is");
    diag_line("# unambiguous, because no display wait can be hiding in the "
              "number at all.");
    diag_line("#");
    if (s_every_frame)
        diag_line("# every_frame=on: one 'frame' line per frame, plus the "
                  "per-second summaries.");
    diag_line("");
}

static void diag_write_stats(const char *prefix, const DiagStats *s,
                             double elapsed_s)
{
    int i;
    char hist[256];
    size_t used = 0;

    if (!s->frames) {
        diag_line("%s no frames", prefix);
        return;
    }
    hist[0] = '\0';
    for (i = 0; i < kBucketCount; ++i) {
        int n;
        if (!s->buckets[i])
            continue;
        n = snprintf(hist + used, sizeof(hist) - used, "%s%s=%lu",
                     used ? " " : "", kBucketLabel[i], s->buckets[i]);
        if (n < 0 || (size_t)n >= sizeof(hist) - used)
            break;
        used += (size_t)n;
    }
    diag_line("%s frames=%lu fps=%.2f avg=%.2fms min=%.2fms max=%.2fms "
              "spikes=%lu | %s",
              prefix, s->frames,
              elapsed_s > 0.0 ? (double)s->frames / elapsed_s : 0.0,
              s->sum_ms / (double)s->frames, s->min_ms, s->max_ms, s->spikes,
              hist);
    /* The one number that decides where to look next. If avg present is most
     * of avg frame, the cost is putting pixels on the screen -- output
     * surface, compositor, DPI scaling -- and not the emulation. */
    if (s_present_count > 0)
        diag_line("%s present avg=%.2fms max=%.2fms swapmax=%.2fms "
                  "over %d present(s)",
                  prefix, s_present_sum_ms / (double)s_present_count,
                  s_present_max_ms, s_swap_max_ms, s_present_count);
    s_present_sum_ms = 0.0;
    s_present_max_ms = 0.0;
    s_present_count = 0;
    s_swap_max_ms = 0.0;
}

/* One emulated frame has just finished. */
static void gwed_diag_frame(void)
{
    Uint64 now;
    double ms;

    if (!s_active || !s_log)
        return;
    now = SDL_GetPerformanceCounter();

    if (!s_t_prev) {
        /* First frame: nothing to measure against, and its interval would be
         * the whole of startup. Start the clock instead of reporting a
         * multi-second "spike" that is really the game loading. */
        s_t_prev = now;
        s_t_window = now;
        s_prev_frame_counter = snes_frame_counter;
        return;
    }

    ms = (double)(now - s_t_prev) * 1000.0 / (double)s_perf_freq;
    s_t_prev = now;

    /* A jump in the guest frame counter means frames ran without this hook.
     * Their time landed in this interval, so the interval is not one frame and
     * must not be recorded as one. */
    if (snes_frame_counter - s_prev_frame_counter > 1) {
        s_counter_gaps++;
        diag_line("[%8.2fs] GAP  %d frame(s) ran without this hook "
                  "(session reset, or the mod was toggled) — %.2fms not "
                  "counted",
                  seconds_since_start(now),
                  snes_frame_counter - s_prev_frame_counter, ms);
        s_prev_frame_counter = snes_frame_counter;
        return;
    }
    s_prev_frame_counter = snes_frame_counter;

    stats_add(&s_window, ms);
    stats_add(&s_session, ms);
    if (ms > s_session.max_ms - 1e-9) {
        s_worst_at_s = seconds_since_start(now);
        s_worst_frame = snes_frame_counter;
    }

    if (s_every_frame) {
        if (s_present_ms >= 0.0)
            diag_line("[%8.2fs] frame %d %.2fms present=%.2fms",
                      seconds_since_start(now), snes_frame_counter, ms,
                      s_present_ms);
        else
            diag_line("[%8.2fs] frame %d %.2fms", seconds_since_start(now),
                      snes_frame_counter, ms);
    }

    if (ms >= s_spike_ms) {
        s_window.spikes++;
        s_session.spikes++;
        diag_line("[%8.2fs] SPIKE frame %d %.2fms (%.2fx budget) "
                  "present=%.2fms%s",
                  seconds_since_start(now), snes_frame_counter, ms,
                  ms / GWED_DIAG_BUDGET_MS,
                  s_present_ms >= 0.0 ? s_present_ms : 0.0,
#if defined(SNES_HAS_LOBBY_CLIENT)
                  snes_netplay_active() ? " netplay" : "");
#else
                  "");
#endif
        /* The autopsy. A spike is the only place the breakdown is worth its
         * line count, and it is the only place it is ever needed: `swap` is
         * SDL_RenderPresent alone, so swap-heavy means the driver, the display
         * pipeline or a swapchain rebuild, and everything else is our drawing.
         * `upload` is the guest->texture copy, which happens before the
         * present timer starts and used to be charged to emulation, hiding a
         * whole class of stall. `rest` is the frame time this present does not
         * account for: emulation, the event pump and any limiter wait. */
        if (s_present_ms >= 0.0 && s_ph_swap >= 0.0) {
            double drawn = (s_ph_clear >= 0.0 ? s_ph_clear : 0.0)
                         + (s_ph_blit >= 0.0 ? s_ph_blit : 0.0)
                         + (s_ph_overlay >= 0.0 ? s_ph_overlay : 0.0)
                         + (s_ph_osd >= 0.0 ? s_ph_osd : 0.0);
            diag_line("           autopsy upload=%.2f clear=%.2f blit=%.2f "
                      "overlay=%.2f osd=%.2f swap=%.2f | draw=%.2f "
                      "rest=%.2f (%s)",
                      s_ph_upload >= 0.0 ? s_ph_upload : 0.0,
                      s_ph_clear >= 0.0 ? s_ph_clear : 0.0,
                      s_ph_blit >= 0.0 ? s_ph_blit : 0.0,
                      s_ph_overlay >= 0.0 ? s_ph_overlay : 0.0,
                      s_ph_osd >= 0.0 ? s_ph_osd : 0.0,
                      s_ph_swap,
                      drawn,
                      ms - s_present_ms
                        - (s_ph_upload >= 0.0 ? s_ph_upload : 0.0),
                      s_ph_swap > drawn ? "swap-dominated: driver/display"
                                        : "draw-dominated: ours");
            if (s_lp_emulate >= 0.0) {
                double acct = s_present_ms
                            + (s_ph_upload >= 0.0 ? s_ph_upload : 0.0)
                            + s_lp_emulate + s_lp_pump
                            + (s_lp_limit >= 0.0 ? s_lp_limit : 0.0);
                diag_line("           loop    emulate=%.2f pump=%.2f "
                          "limiter=%.2f | accounted=%.2f of %.2f "
                          "(other=%.2f)",
                          s_lp_emulate, s_lp_pump,
                          s_lp_limit >= 0.0 ? s_lp_limit : 0.0,
                          acct, ms, ms - acct);
            }
        }
        if (s_last_event_at_s >= 0.0)
            diag_line("           last event %.2fs ago: %s",
                      seconds_since_start(now) - s_last_event_at_s,
                      s_last_event);
    }
    /* One sample, one report. Clearing here means a frame that ran without a
     * present of its own (a fast-forward burst) shows no present= rather than
     * repeating the previous iteration's number as if it were its own. */
    s_present_ms = -1.0;
    s_ph_upload = s_ph_clear = s_ph_blit = -1.0;
    s_ph_overlay = s_ph_osd = s_ph_swap = -1.0;
    s_lp_emulate = s_lp_pump = s_lp_limit = -1.0;

    /* One summary per second of wall clock, so a quiet session stays short
     * and a bad one is dense where it went bad. */
    if ((double)(now - s_t_window) / (double)s_perf_freq >= 1.0) {
        double elapsed = (double)(now - s_t_window) / (double)s_perf_freq;
        char prefix[64];
        snprintf(prefix, sizeof(prefix), "[%8.2fs]", seconds_since_start(now));
        diag_write_stats(prefix, &s_window, elapsed);
        stats_reset(&s_window);
        s_t_window = now;
    }
}

/* Runs at the top of every session's activation pass, before the plugins. */
static void gwed_diag_reset(void)
{
    /* A session boundary is not the end of the log: a rematch is part of the
     * same sitting and the interesting spike may be in either half. Keep the
     * file open and the session totals running; only the frame clock restarts,
     * because the gap across a reboot is load time, not a stall. */
    if (s_log && s_active)
        diag_line("[%8.2fs] --- session boundary (rematch or reboot); frame "
                  "clock restarts, totals continue ---",
                  s_perf_freq ? seconds_since_start(SDL_GetPerformanceCounter())
                              : 0.0);
    s_t_prev = 0;
    s_active = 0;
}

static void gwed_diag_activate(void)
{
    char path[1200];
    char value[32];

    if (!snes_mod_runtime_feature_enabled_c(GWED_DIAG_PACKAGE,
                                            GWED_DIAG_FEATURE))
        return;

    /* Options, then env overrides -- the env ones exist so a player can be
     * asked for a sharper log over chat without walking them through menus. */
    if (snes_mod_runtime_feature_option_value_c(GWED_DIAG_PACKAGE,
                                                GWED_DIAG_FEATURE, "spike_ms",
                                                value, sizeof(value))) {
        double v = atof(value);
        if (v > 0.0)
            s_spike_ms = v;
    }
    if (snes_mod_runtime_feature_option_value_c(GWED_DIAG_PACKAGE,
                                                GWED_DIAG_FEATURE,
                                                "every_frame", value,
                                                sizeof(value))) {
        s_every_frame = strcmp(value, "on") == 0;
    }
    {
        const char *env = getenv("GWED_DIAG_SPIKE_MS");
        if (env && env[0]) {
            double v = atof(env);
            if (v > 0.0)
                s_spike_ms = v;
        }
        env = getenv("GWED_DIAG_EVERY_FRAME");
        if (env && env[0])
            s_every_frame = env[0] != '0';
    }

    s_perf_freq = SDL_GetPerformanceFrequency();
    if (!s_perf_freq) {
        /* Refuse rather than divide by zero and write plausible nonsense. */
        fprintf(stderr, "[diag] no performance counter — performance log "
                        "disabled for this session\n");
        return;
    }

    if (!s_log) {
        if (!diag_open_log(path, sizeof(path))) {
            fprintf(stderr, "[diag] could not open %s for writing — "
                            "performance log disabled (is the game folder "
                            "read-only?)\n", path);
            return;
        }
        stats_reset(&s_window);
        stats_reset(&s_session);
        s_t_start = SDL_GetPerformanceCounter();
        s_t_window = s_t_start;
        s_worst_at_s = 0.0;
        s_worst_frame = 0;
        s_counter_gaps = 0;
        diag_write_header(path);
        /* Say it on stderr too: the whole point is that the player can find
         * the file and send it. */
        fprintf(stderr, "[diag] performance log: %s\n", path);
    }
    snes_mod_register_frame_callback(gwed_diag_frame);
    s_t_prev = 0;
    s_prev_frame_counter = snes_frame_counter;
    s_active = 1;
}

/* Normal exit. A log that stops mid-line is a crash, and the footer is what
 * tells the two apart -- so its absence is itself the finding. */
static void gwed_diag_atexit(void)
{
    double elapsed;
    if (!s_log)
        return;
    elapsed = s_perf_freq
                  ? seconds_since_start(SDL_GetPerformanceCounter())
                  : 0.0;
    diag_line("");
    diag_write_stats("# session", &s_session, elapsed);
    if (s_session.frames) {
        diag_line("# worst   %.2fms at %.2fs (frame %d)", s_session.max_ms,
                  s_worst_at_s, s_worst_frame);
        diag_line("# spikes  %lu over %.0f ms in %.1f s (%.2f per minute)",
                  s_session.spikes, s_spike_ms, elapsed,
                  elapsed > 0.0 ? (double)s_session.spikes * 60.0 / elapsed
                                : 0.0);
    }
    if (s_counter_gaps)
        diag_line("# gaps    %lu interval(s) spanned frames this hook did not "
                  "see and were left out", s_counter_gaps);
    diag_line("# ended   cleanly");
    fclose(s_log);
    s_log = NULL;
}

SNES_MOD_CONSTRUCTOR(gwed_diagnostics_register)
{
    snes_mod_register_reset_callback(gwed_diag_reset);
    snes_mod_register_activation_plugin(GWED_DIAG_PLUGIN, gwed_diag_activate);
    /* The frame hook is registered on activation, not here: an unselected
     * package must not cost the frame loop a call. atexit is armed now
     * because a footer is only written when a log exists, and the process may
     * end without another pass through activation. */
    atexit(gwed_diag_atexit);
}
