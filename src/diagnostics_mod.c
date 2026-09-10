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

/* Previous sessions kept beside the live log. */
#define GWED_DIAG_KEEP_LOGS 5

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
/* Worst upload and worst texture-unlock in the current window.
 *
 * Spike COUNT is a bad metric for comparing configurations here: it only
 * counts frames crossing a fixed threshold, and on a quiet machine the
 * baseline produces one or two per run, which discriminates nothing. These
 * are continuous -- every frame updates them whether or not it spikes -- so a
 * change in the tail shows up even when nothing crosses 25 ms. */
static double s_upload_max_ms, s_unlock_max_ms;
static double s_lp_emulate = -1.0, s_lp_pump = -1.0, s_lp_limit = -1.0;
static double s_lp_emulate_cpu = -1.0;
static double s_ph_upload_cpu = -1.0;
static double s_ph_lock = -1.0;
static double s_ph_fill = -1.0, s_ph_unlock = -1.0;
static double s_iter_ms = -1.0, s_iter_cpu = -1.0;
static double s_pace_want = -1.0, s_pace_slept = -1.0;
static double s_head_ms = -1.0;
/* The PREVIOUS iteration, kept because the frame interval straddles two.
 *
 * The mod frame hook fires inside RtlRunFrame, i.e. in the middle of a loop
 * iteration, so frame_ms = (tail of iteration N-1 after its hook) + (head of
 * iteration N up to its hook). Reporting only iteration N's wall made a 25 ms
 * frame show `iter wall=16.66` -- a perfect iteration -- and sent the cost to
 * `other`, where nothing could name it. With both, a long previous iteration
 * is visible as exactly that instead of as an unexplained remainder. */
static double s_iter_prev_ms = -1.0, s_iter_prev_cpu = -1.0;
/* Shifted from these, NOT from s_iter_ms: the per-frame reset clears s_iter_ms
 * before the next iteration reports, so shifting from it copied -1 every time
 * and the line never printed. These are never reset. */
static double s_iter_last_ms = -1.0, s_iter_last_cpu = -1.0;

/* Why a frame was off-CPU, straight from the kernel's own accounting.
 *
 * wall >> cpu says the thread was NOT computing; it does not say why. These
 * two separate the remaining candidates, and they need different fixes:
 *   run_delay  time this task sat RUNNABLE on a runqueue waiting for a CPU.
 *              Large means we were PREEMPTED -- host contention, not our bug.
 *   majflt     major page faults: a read from disk/swap to satisfy a memory
 *              access. Large means the stall is memory, and it is ours to fix.
 * If both are near zero the thread slept on something explicit (a lock, a
 * blocking write) and the next step is a stack, not a counter.
 *
 * Linux-only by construction -- /proc. Windows gets the same discrimination
 * from wall-vs-cpu above; this narrows it further where it is available. */
static unsigned long long s_prev_run_delay, s_prev_majflt;
static unsigned long long s_frame_run_delay, s_frame_majflt;

static void diag_sample_sched(void)
{
#if defined(__linux__)
    unsigned long long cpu_ns = 0, delay_ns = 0, slices = 0;
    FILE *f = fopen("/proc/self/schedstat", "r");
    if (f) {
        if (fscanf(f, "%llu %llu %llu", &cpu_ns, &delay_ns, &slices) == 3) {
            s_frame_run_delay = delay_ns - s_prev_run_delay;
            s_prev_run_delay = delay_ns;
        }
        fclose(f);
    }
    f = fopen("/proc/self/stat", "r");
    if (f) {
        char buf[1024];
        if (fgets(buf, sizeof buf, f)) {
            /* comm can contain spaces and parentheses; parse after the last
             * ')' so a renamed thread cannot shift the field indices. */
            char *p2 = strrchr(buf, ')');
            if (p2) {
                unsigned long long v[12]; int i, n = 0;
                char *tok = strtok(p2 + 1, " ");
                for (i = 0; tok && i < 12; i++) {
                    v[n++] = strtoull(tok, NULL, 10);
                    tok = strtok(NULL, " ");
                }
                /* after ')': state(0) ppid(1) pgrp(2) session(3) tty(4)
                 * tpgid(5) flags(6) minflt(7) cminflt(8) majflt(9) */
                if (n > 9) {
                    s_frame_majflt = v[9] - s_prev_majflt;
                    s_prev_majflt = v[9];
                }
            }
        }
        fclose(f);
    }
#endif
}
/* Guest work done in this frame, so a slow frame can be told apart from a
 * frame that merely DID more. Same instruments the debug server's
 * interp_stats reads, sampled per frame instead of per session. */
extern uint64_t interp816_insns_total(void);
extern uint64_t interp816_cycles_total(void);
static uint64_t s_prev_insns, s_prev_gcycles;
static uint64_t s_win_insns_sum;      /* window totals, for the comparison */
static int      s_win_insns_frames;
static uint64_t s_frame_insns, s_frame_gcycles;   /* this frame's delta */
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
/* ---- the log must not stall the thing it measures ---------------------- */
/*
 * diag_line() used to vfprintf + fflush straight onto the frame thread. With
 * every_frame=on that is one write(2) per frame, and MEASURED on 2026-09-10 it
 * periodically blocked for 62-71 ms when the kernel throttled dirty pages:
 *
 *   cpu     emulate wall=72.77 cpu=1.92  -> BLOCKED off-CPU
 *   kernel  run_delay=0.01ms majflt=0    -> slept on something explicit
 *   BLOCKED 70.74 ms in syscall 1 (write)  fd=8 -> gwed_diagnostics.log
 *
 * The per-frame hook runs inside RtlRunFrame, so that block was charged to
 * emulation and read as a 4x-budget spike in the guest. The instrument was
 * manufacturing the stalls it reported, and every log this feature has ever
 * produced overstates them.
 *
 * So the frame thread now only ever appends to memory. A writer thread does
 * the blocking part. Crash-safety is preserved differently but genuinely: the
 * writer flushes after every drain, so the tail on disk trails the frame
 * thread by at most one drain rather than by nothing.
 *
 * Overflow is counted and reported rather than silently dropped -- a log that
 * quietly loses lines is worse than one that admits it.
 */
#define DIAG_Q_BYTES (1u << 20)
static char       *s_q;
static size_t      s_q_head, s_q_tail;     /* byte ring: head=write, tail=read */
static SDL_mutex  *s_q_lock;
static SDL_cond   *s_q_wake;
static SDL_Thread *s_q_thread;
static int         s_q_stop;
static unsigned long long s_q_dropped;

static size_t diag_q_used(void)
{
    return (s_q_head >= s_q_tail) ? (s_q_head - s_q_tail)
                                  : (DIAG_Q_BYTES - s_q_tail + s_q_head);
}

static int SDLCALL diag_writer(void *unused)
{
    (void)unused;
    for (;;) {
        char chunk[8192];
        size_t n = 0;
        SDL_LockMutex(s_q_lock);
        while (!s_q_stop && s_q_head == s_q_tail)
            SDL_CondWait(s_q_wake, s_q_lock);
        while (n < sizeof chunk && s_q_tail != s_q_head) {
            chunk[n++] = s_q[s_q_tail];
            s_q_tail = (s_q_tail + 1) % DIAG_Q_BYTES;
        }
        SDL_UnlockMutex(s_q_lock);
        if (n && s_log) {
            fwrite(chunk, 1, n, s_log);   /* the blocking part, off the frame */
            fflush(s_log);
        }
        if (!n && s_q_stop)
            break;
    }
    return 0;
}

static void diag_line(const char *fmt, ...)
{
    char line[1024];
    va_list ap;
    int len;
    if (!s_log)
        return;
    va_start(ap, fmt);
    len = vsnprintf(line, sizeof line - 1, fmt, ap);
    va_end(ap);
    if (len < 0)
        return;
    if (len > (int)sizeof line - 2)
        len = (int)sizeof line - 2;
    line[len++] = '\n';

    if (!s_q_thread) {                 /* before the writer exists: direct */
        fwrite(line, 1, (size_t)len, s_log);
        fflush(s_log);
        return;
    }
    SDL_LockMutex(s_q_lock);
    if (DIAG_Q_BYTES - diag_q_used() > (size_t)len + 1) {
        int i;
        for (i = 0; i < len; i++) {
            s_q[s_q_head] = line[i];
            s_q_head = (s_q_head + 1) % DIAG_Q_BYTES;
        }
    } else {
        s_q_dropped++;                 /* admitted in the footer */
    }
    SDL_CondSignal(s_q_wake);
    SDL_UnlockMutex(s_q_lock);
}

static void diag_writer_start(void)
{
    if (s_q_thread || !s_log)
        return;
    s_q = (char *)malloc(DIAG_Q_BYTES);
    if (!s_q)
        return;                        /* stays on the direct path */
    s_q_lock = SDL_CreateMutex();
    s_q_wake = SDL_CreateCond();
    if (!s_q_lock || !s_q_wake)
        return;
    s_q_thread = SDL_CreateThread(diag_writer, "gwed-diag-log", NULL);
}

static void diag_writer_stop(void)
{
    if (!s_q_thread)
        return;
    SDL_LockMutex(s_q_lock);
    s_q_stop = 1;
    SDL_CondSignal(s_q_wake);
    SDL_UnlockMutex(s_q_lock);
    SDL_WaitThread(s_q_thread, NULL);
    s_q_thread = NULL;
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

    /* Keep several previous sessions, not one.
     *
     * One was not enough. While a spike was being diagnosed against these
     * logs, two verification runs of the same executable rotated the session
     * that actually showed the problem out of existence -- the interesting run
     * became .prev, then became nothing, and it was unrecoverable. A player
     * asked for a log after a stutter usually restarts the game at least once
     * before finding the file, so the same thing happens to them.
     *
     * Generations: <log>.1 is the previous session, .2 the one before, and so
     * on. Cheap: these are text and a long session is well under a megabyte. */
    {
        int gen;
        char older[1024], newer[1024];
        for (gen = GWED_DIAG_KEEP_LOGS - 1; gen >= 1; gen--) {
            if (snprintf(older, sizeof(older), "%s.%d", path, gen + 1)
                    >= (int)sizeof(older) ||
                snprintf(newer, sizeof(newer), "%s.%d", path, gen)
                    >= (int)sizeof(newer))
                continue;
            remove(older);
            rename(newer, older);
        }
        if (snprintf(prev, sizeof(prev), "%s.1", path) < (int)sizeof(prev)) {
            remove(prev);
            rename(path, prev);
        }
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
    if (upload_ms > s_upload_max_ms) s_upload_max_ms = upload_ms;
    s_ph_overlay = overlay_ms; s_ph_osd = osd_ms; s_ph_swap = swap_ms;
    if (swap_ms > s_swap_max_ms) s_swap_max_ms = swap_ms;
}

/* Name the single largest bucket, across ALL of them.
 *
 * The first version of this compared `swap` against the drawing phases only,
 * and so reported "swap-dominated: driver/display" for a frame whose swap was
 * 0.14 ms and whose emulation was 66 ms. A verdict that can point at the
 * display while the emulator is the whole cost is worse than no verdict --
 * it is exactly the wrong-platform-guess this instrumentation exists to stop.
 * Compare everything, and say which bucket actually holds the time. */
static const char *diag_spike_verdict(double drawn, double other_ms)
{
    struct { const char *name; double ms; } b[] = {
        { "EMULATION: guest frame, ours",        s_lp_emulate },
        { "UPLOAD: guest->texture copy, ours",   s_ph_upload  },
        { "DRAW: our render calls",              drawn        },
        { "SWAP: driver/display/swapchain",      s_ph_swap    },
        /* Unattributed time has to be able to win. Without this the verdict
         * names the largest MEASURED bucket even when every measured bucket is
         * small and the real cost is outside all of them -- which is how four
         * 25 ms frames whose emulation was 1.5-2.5 ms came out labelled
         * EMULATION. */
        { "INPUT/GESTURE: reads before emulation", s_head_ms },
        { "UNATTRIBUTED: outside every measured phase", other_ms },
        { "EVENT PUMP",                          s_lp_pump    },
        /* Only a cause when it overshot. With pacing on, the deliberate wait
         * is the largest bucket in nearly every frame, so treating it as a
         * candidate would label every spike LIMITER whatever really happened.
         * An overshoot is real and gets named; an honest wait scores zero. */
        { "PACER OVERSHOOT: slept longer than asked",
          (s_pace_slept >= 0.0 && s_pace_want >= 0.0)
            ? (s_pace_slept - s_pace_want) : -1.0 },
    };
    int i, best = 0;
    for (i = 1; i < (int)(sizeof b / sizeof b[0]); i++)
        if (b[i].ms > b[best].ms) best = i;
    return b[best].ms > 0.0 ? b[best].name : "unattributed";
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

void GwedDiag_NoteEmulateCpuMs(double cpu_ms)
{
    if (!s_active)
        return;
    s_lp_emulate_cpu = cpu_ms;
}

void GwedDiag_NoteUploadCpuMs(double cpu_ms)
{
    if (!s_active)
        return;
    s_ph_upload_cpu = cpu_ms;
}

void GwedDiag_NoteTextureLockMs(double ms)
{
    if (!s_active)
        return;
    s_ph_lock = ms;
}

void GwedDiag_NoteTextureFillUnlockMs(double fill_ms, double unlock_ms)
{
    if (!s_active)
        return;
    s_ph_fill = fill_ms;
    s_ph_unlock = unlock_ms;
    if (unlock_ms > s_unlock_max_ms) s_unlock_max_ms = unlock_ms;
}

void GwedDiag_NoteIterationMs(double iter_ms, double iter_cpu_ms)
{
    if (!s_active)
        return;
    s_iter_prev_ms = s_iter_last_ms;
    s_iter_prev_cpu = s_iter_last_cpu;
    s_iter_last_ms = iter_ms;
    s_iter_last_cpu = iter_cpu_ms;
    s_iter_ms = iter_ms;
    s_iter_cpu = iter_cpu_ms;
}

void GwedDiag_NotePaceMs(double want_ms, double slept_ms)
{
    if (!s_active)
        return;
    s_pace_want = want_ms;
    s_pace_slept = slept_ms;
}

void GwedDiag_NoteInputHeadMs(double head_ms)
{
    if (!s_active)
        return;
    s_head_ms = head_ms;
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
    /* A trace-enabled build is NOT what a player runs. It compiles in the TCP
     * debug server and the observability rings, and those rings are ~2 GB:
     * measured 2026-09-10, VmSize 3907 MB / RSS 462 MB / 22093 major page
     * faults in 25 s, against 57 MB / 11.8 MB / 9 faults for the same code
     * with -DSNESRECOMP_ENABLE_TRACE=OFF. On a machine with any memory
     * pressure that difference IS the lag spike, and a perf log from such a
     * build says nothing about the shipped game. Say so at the top, where it
     * cannot be missed, rather than leaving it to be rediscovered. */
#if defined(SNESRECOMP_TRACE) && SNESRECOMP_TRACE
    diag_line("#");
    diag_line("# *** TRACE BUILD -- NOT PLAYER-REPRESENTATIVE ***");
    diag_line("#   Built with SNESRECOMP_ENABLE_TRACE=ON: the TCP debug server");
    diag_line("#   and the observability rings are compiled in, reserving on the");
    diag_line("#   order of 2 GB up front. Under memory pressure that pages out");
    diag_line("#   and the resulting major faults show up here as spikes that a");
    diag_line("#   shipped build does not have. Rebuild with");
    diag_line("#   -DSNESRECOMP_ENABLE_TRACE=OFF before drawing any conclusion");
    diag_line("#   about what a player feels.");
    diag_line("#");
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
                  "uploadmax=%.2fms unlockmax=%.2fms over %d present(s)",
                  prefix, s_present_sum_ms / (double)s_present_count,
                  s_present_max_ms, s_swap_max_ms,
                  s_upload_max_ms, s_unlock_max_ms, s_present_count);
    s_present_sum_ms = 0.0;
    s_present_max_ms = 0.0;
    s_present_count = 0;
    s_swap_max_ms = 0.0;
    s_upload_max_ms = 0.0;
    s_unlock_max_ms = 0.0;
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

    diag_sample_sched();
    {
        uint64_t ins = interp816_insns_total();
        uint64_t gcy = interp816_cycles_total();
        s_frame_insns   = ins - s_prev_insns;
        s_frame_gcycles = gcy - s_prev_gcycles;
        s_prev_insns = ins;
        s_prev_gcycles = gcy;
    }
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
                      diag_spike_verdict(drawn, ms - s_present_ms
                                         - (s_ph_upload >= 0.0
                                            ? s_ph_upload : 0.0)
                                         - (s_lp_emulate >= 0.0
                                            ? s_lp_emulate : 0.0)
                                         - (s_lp_limit >= 0.0
                                            ? s_lp_limit : 0.0)));
            {
                /* THE discriminator for an emulation stall. If the guest
                 * executed a normal number of instructions and the frame still
                 * took 40x as long, the cost is on the HOST -- an allocation,
                 * a page fault, a blocking write, a lock. If instruction count
                 * scales with the time, the guest genuinely did more work and
                 * the question moves to what it was doing. Without this the
                 * two are indistinguishable and the hunt has no direction. */
                double avg = s_win_insns_frames
                    ? (double)s_win_insns_sum / (double)s_win_insns_frames
                    : 0.0;
                diag_line("           guest   insns=%llu cycles=%llu | "
                          "window avg insns=%.0f -> %.1fx (%s)",
                          (unsigned long long)s_frame_insns,
                          (unsigned long long)s_frame_gcycles,
                          avg,
                          avg > 0.0 ? (double)s_frame_insns / avg : 0.0,
                          (avg > 0.0 && (double)s_frame_insns < avg * 2.0)
                            ? "guest did NORMAL work: the cost is on the HOST"
                            : "guest genuinely executed more");
            }
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
                if (s_head_ms >= 0.0)
                    diag_line("           input   head=%.2f (keyboard+gamepad "
                              "reads and gesture checks, before emulation)",
                              s_head_ms);
                if (s_pace_want >= 0.0)
                    diag_line("           pacer   asked=%.2f slept=%.2f "
                              "overshoot=%.2f",
                              s_pace_want, s_pace_slept,
                              s_pace_slept - s_pace_want);
                if (s_iter_prev_ms >= 0.0)
                    diag_line("           iter-1  wall=%.2f cpu=%.2f "
                              "(the PREVIOUS iteration; the frame interval "
                              "straddles it and this one)",
                              s_iter_prev_ms,
                              s_iter_prev_cpu >= 0.0 ? s_iter_prev_cpu : 0.0);
                if (s_iter_ms >= 0.0)
                    diag_line("           iter    wall=%.2f cpu=%.2f -> the "
                              "missing time is %s",
                              s_iter_ms,
                              s_iter_cpu >= 0.0 ? s_iter_cpu : 0.0,
                              (s_iter_ms > ms * 0.6)
                                ? ((s_iter_cpu >= 0.0 &&
                                    s_iter_cpu < s_iter_ms * 0.5)
                                   ? "INSIDE the loop, WAITING (unmeasured "
                                     "blocking call in the body)"
                                   : "INSIDE the loop, ON-CPU (unmeasured work "
                                     "in the body)")
                                : "BETWEEN iterations (outside the loop body "
                                  "entirely)");
                if (s_ph_upload > 5.0)
                    diag_line("           upload  wall=%.2f cpu=%.2f "
                              "lock=%.2f copy=%.2f -> %s",
                              s_ph_upload,
                              s_ph_upload_cpu >= 0.0 ? s_ph_upload_cpu : 0.0,
                              s_ph_lock >= 0.0 ? s_ph_lock : 0.0,
                              s_ph_lock >= 0.0 ? s_ph_upload - s_ph_lock
                                               : s_ph_upload,
                              (s_ph_lock >= 0.0 && s_ph_lock > s_ph_upload * 0.5)
                                ? "LOCK: waiting on the GPU to release the "
                                  "streaming texture"
                                : (s_ph_unlock >= 0.0 &&
                                   s_ph_unlock > s_ph_upload * 0.5
                                   ? "UNLOCK: the GPU transfer blocks"
                                   : (s_ph_fill >= 0.0 &&
                                      s_ph_fill > s_ph_upload * 0.5
                                      ? "FILL: writing the pixels is slow"
                                      : "spread across phases")));
                if (s_ph_fill >= 0.0 || s_ph_unlock >= 0.0)
                    diag_line("           texture fill=%.2f unlock=%.2f",
                              s_ph_fill >= 0.0 ? s_ph_fill : 0.0,
                              s_ph_unlock >= 0.0 ? s_ph_unlock : 0.0);
                if (s_lp_emulate_cpu >= 0.0)
                    diag_line("           cpu     emulate wall=%.2f cpu=%.2f "
                              "-> %s",
                              s_lp_emulate, s_lp_emulate_cpu,
                              (s_lp_emulate > 5.0 &&
                               s_lp_emulate_cpu < s_lp_emulate * 0.5)
                                ? "BLOCKED off-CPU"
                                : "on-CPU: host code doing the work");
#if defined(__linux__)
                diag_line("           kernel  run_delay=%.2fms majflt=%llu "
                          "-> %s",
                          (double)s_frame_run_delay / 1e6,
                          (unsigned long long)s_frame_majflt,
                          ((double)s_frame_run_delay / 1e6) > s_lp_emulate * 0.5
                            ? "PREEMPTED: waited for a CPU (host contention)"
                            : (s_frame_majflt > 0
                               ? "MAJOR PAGE FAULTS: memory, and ours"
                               : "slept on something explicit: needs a stack"));
#endif
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
    if (ms < s_spike_ms) {           /* healthy frames define the baseline */
        s_win_insns_sum += s_frame_insns;
        s_win_insns_frames++;
    }
    s_present_ms = -1.0;
    s_ph_upload = s_ph_clear = s_ph_blit = -1.0;
    s_ph_overlay = s_ph_osd = s_ph_swap = -1.0;
    s_lp_emulate = s_lp_pump = s_lp_limit = -1.0;
    s_lp_emulate_cpu = -1.0;
    s_ph_upload_cpu = -1.0;
    s_ph_lock = -1.0;
    s_ph_fill = s_ph_unlock = -1.0;
    s_iter_ms = s_iter_cpu = -1.0;
    s_pace_want = s_pace_slept = -1.0;
    s_head_ms = -1.0;

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
        /* The header goes down synchronously (the writer is not up yet), then
         * everything the frame loop produces goes through the queue. */
        diag_writer_start();
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
    if (s_q_dropped)
        diag_line("# dropped %llu line(s): the log queue filled, which means "
                  "the writer could not keep up. Lines are missing.",
                  (unsigned long long)s_q_dropped);
    diag_line("# ended   cleanly");
    /* Drain and join before closing: the writer holds the only path to disk
     * for everything above, footer included. */
    diag_writer_stop();
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
