/*
 * Gundam Wing Endless Duel — desktop host.
 *
 * Deliberately small: ROM resolution, a window, a texture, a keyboard map,
 * and the frame loop the framework expects (capture input -> RtlRunFrame ->
 * present). snesrecomp/runner/src/desktop/mmx23_host_main.inc is the
 * fully-featured reference host — netplay barrier, shaders, save states,
 * gamepad config — and is worth reading before growing this one.
 *
 * Nothing here ever ships a ROM. The player supplies one, and the host's job
 * is to make that easy rather than to exit with a usage line:
 *
 *   1. The recomp-ui GUI launcher (built when the project has the recomp-ui
 *      submodule) picks and verifies one, alongside display/audio/input.
 *   2. Otherwise snesrecomp_launcher_resolve_rom_sha256() takes the
 *      positional argument, then a copy beside the executable, then the
 *      <exe_dir>/rom.cfg cache, then a native file picker.
 *
 * Both check what they are handed against the digests in codegen_setup.c —
 * the same ones tools/regen.sh generated this C from — so a ROM that cannot
 * have produced this executable is caught at the door rather than as
 * mis-execution ten frames in.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "common_rtl.h"
#include "common_cpu_infra.h"
#include "widescreen.h"
#include "game_rtl.h"
#include "snes/ppu.h"
#include "debug_server.h"
#include "snes_savestate_menu.h" /* Select+R save-state overlay */
#include "cpu_trace.h"
#include "desktop/sdl_compat.h"
#include "host_paths.h"      /* snesrecomp_exe_dir_path */
#include "launcher.h"        /* snesrecomp_launcher_resolve_rom_sha256 */
#include "launcher_cache.h"  /* snesrecomp_rom_cache_read */
#include "codegen_setup.h"   /* kGameCodegenIdentity - the ROM digests */

#if defined(RECOMP_LAUNCHER)
/* Defined by recomp_target_launcher_ui() in CMakeLists.txt. Absent when the
 * project was scaffolded with --no-recomp-ui, and every launcher block in
 * this file compiles out with it. */
#include "recomp_launcher.h"
#include "launcher_profile.h"
#endif

#if defined(SNES_HAS_LOBBY_CLIENT)
/* Delay-sync netplay + MotK lobby. Defined by snesrecomp_enable_recomp_net()
 * in CMakeLists.txt, which also links recomp-net and puts these headers on
 * the include path. Without that call every netplay block compiles out and
 * the launcher's netplay button stays capability-gated off.
 *
 * This is the MINIMAL host wiring: lobby button in the launcher, delay-sync
 * session from the lobby's launch result, exit on quit. The fully-featured
 * reference (rematch, soft-return to the waiting room, dual viewport) is
 * MetalWarriorsSNESRecomp's src/main.c. */
#include "snes_netplay.h"
#include "snes_host_lobby.h"
#include "snes_host_app.h"

static SnesNetplayConfig g_netplay_cfg;
static int g_netplay_pending;    /* launcher armed a session; start after SnesInit */
static int g_netplay_from_lobby; /* admit pump waits for the lobby peer */

static void host_lobby_ensure_init(void)
{
    static int once;
    SnesHostLobbyIdentity id;
    SnesHostLobbyOpts opts;
    if (once)
        return;
    once = 1;
    memset(&id, 0, sizeof(id));
    id.game_name = SNES_GAME_TITLE;
    id.game_version = SNES_GAME_VERSION;
    id.lan_registry_path = "netplay_lan_lobby.txt";
    id.default_lobby_name = "Netplay Lobby";
    memset(&opts, 0, sizeof(opts));
    opts.rematch_set_ready = 1;
    /* fill_match_caps NULL → input delay 2, no widescreen caps exchange. */
    if (snes_host_lobby_init(&id, &opts) != 0)
        fprintf(stderr, "netplay: snes_host_lobby_init failed\n");
}
#endif /* SNES_HAS_LOBBY_CLIENT */

#define GAME_WIDTH  256
#define GAME_HEIGHT 224

extern Ppu *g_ppu;

/* Developer TCP debug server (opt in with -DSNESRECOMP_ENABLE_TRACE=ON).
 * debug_server.h replaces every entry point with a no-op static inline when
 * the build did not opt in, so these calls need no #if of their own.
 *
 * The server owns its own thread. The game free-runs and a client queries the
 * always-on rings; nothing here pauses the runtime to observe it, because
 * stopping one of two observers to look at it is how they stop agreeing. */
#ifndef SNES_DEBUG_PORT
#define SNES_DEBUG_PORT 4370
#endif

static void start_debug_server(void)
{
    int port = SNES_DEBUG_PORT;
    const char *env = getenv("SNESRECOMP_DEBUG_PORT");

    if (env && env[0]) {
        int v = atoi(env);
        if (v > 0 && v < 65536)
            port = v;
    }
    /* Allocate the cpu_trace ring before the first frame. The server's query
     * commands read it, and an unallocated ring means every one of them
     * answers "nothing recorded" for a run that did in fact execute. */
    cpu_trace_init();
    debug_server_set_ram(g_ram, 0x20000);
    if (debug_server_init(port) == 0) {
#if SNESRECOMP_TRACE
        fprintf(stderr, "[main] debug server listening on 127.0.0.1:%d\n", port);
#endif
    }
#if SNESRECOMP_TRACE
    else {
        fprintf(stderr, "[main] debug server FAILED to bind port %d\n", port);
    }
#endif
}

/* The PPU composites into a host-owned buffer, not straight into the SDL
 * texture: ppu_runLine() writes one scanline at a time across the whole frame,
 * while the texture is only mapped for the moment of the present. PpuBeginDrawing
 * is what points ppu->renderBuffer at this array — without that call it stays
 * NULL and nothing is ever drawn. */
static uint32_t g_render_pixels[GAME_WIDTH * GAME_HEIGHT];

/* The framework calls this to hand the host a frame. renderBuffer is the
 * PPU's composited output in the runner's native layout. */
void RtlDrawPpuFrame(uint8 *pixel_buffer, size_t pitch, uint32 render_flags)
{
    (void)render_flags;
    if (!g_ppu || !g_ppu->renderBuffer)
        return;
    /* Render the frame before presenting it. Nothing else in a production
     * build calls draw_ppu_frame — common_rtl.c's call is inside SNES_COSIM —
     * so a host that omits it presents an untouched texture forever. */
    g_rtl_game_info->draw_ppu_frame();
    RtlWidescreenPresent(pixel_buffer, pitch, g_ppu->renderBuffer,
                         GAME_WIDTH, GAME_HEIGHT);
}


/* ── Audio ────────────────────────────────────────────────────────────────
 *
 * The scaffold shipped silent: it called snesrecomp_sdl_init(SDL_INIT_VIDEO)
 * and never opened an audio device, so RtlRenderAudio — the runner's mixer,
 * which pulls the emulated S-DSP — had no consumer at all. host_contract.c's
 * RtlApuLock/Unlock existed but were never load-bearing, exactly as its own
 * comment predicted.
 *
 * The S-DSP produces one block of 534 samples at its true rate of 32040 Hz
 * (1.024 MHz / 32). RtlSetAudioOutputRate() tells the consumer what rate to
 * resample onto — it cannot infer it, and getting it wrong detunes everything
 * (the runner's own notes record 32000 playing a constant -2.2 cents flat). */

static SDL_AudioDeviceID g_audio_device;
#if SNESRECOMP_SDL3
static SDL_AudioStream *g_audio_stream;
static uint8 *g_audio_scratch;
static size_t g_audio_scratch_size;
#endif
static uint8 *g_audio_block;          /* one DSP block, host rate */
static uint8 *g_audio_block_cur;
static uint8 *g_audio_block_end;
static int g_frames_per_block;
static int g_audio_channels = 2;
static unsigned long long g_audio_calls;   /* diagnostics only */
static unsigned long long g_audio_nonzero;

/* Runs on the audio thread. RtlApuLock serialises it against the guest thread,
 * which is the whole reason that hook exists. */
static void fill_audio(uint8 *stream, int len)
{
    RtlApuLock();
    g_audio_calls++;
    while (len > 0) {
        int n;
        if (g_audio_block_end == g_audio_block_cur) {
            RtlRenderAudio((int16 *)g_audio_block, g_frames_per_block,
                           g_audio_channels);
            g_audio_block_cur = g_audio_block;
            g_audio_block_end = g_audio_block +
                (size_t)g_frames_per_block * g_audio_channels * sizeof(int16);
            {   /* Did the DSP actually produce anything but silence? */
                const int16 *q = (const int16 *)g_audio_block;
                int i, total = g_frames_per_block * g_audio_channels;
                for (i = 0; i < total; i++)
                    if (q[i]) { g_audio_nonzero++; break; }
            }
        }
        n = (int)(g_audio_block_end - g_audio_block_cur);
        if (n > len) n = len;
        memcpy(stream, g_audio_block_cur, (size_t)n);
        g_audio_block_cur += n;
        stream += n;
        len -= n;
    }
    RtlApuUnlock();
}

#if SNESRECOMP_SDL3
static void SDLCALL audio_stream_cb(void *user, SDL_AudioStream *stream,
                                    int additional, int total)
{
    (void)user; (void)total;
    if (additional <= 0) return;
    if ((size_t)additional > g_audio_scratch_size) {
        uint8 *grown = (uint8 *)realloc(g_audio_scratch, (size_t)additional);
        if (!grown) return;
        g_audio_scratch = grown;
        g_audio_scratch_size = (size_t)additional;
    }
    fill_audio(g_audio_scratch, additional);
    SDL_PutAudioStreamData(stream, g_audio_scratch, additional);
}
#else
static void SDLCALL audio_cb(void *user, Uint8 *stream, int len)
{
    (void)user;
    fill_audio((uint8 *)stream, len);
}
#endif

/* Returns 0 on success. A failure here is reported and tolerated: a silent
 * game is worth more than no game. */
static int open_audio(void)
{
    SDL_AudioSpec want, have;

    memset(&want, 0, sizeof(want));
    memset(&have, 0, sizeof(have));
    want.freq = 32040;          /* the S-DSP's true rate */
    want.channels = 2;

    /* Force the APU mutex into existence on this thread: RtlApuLock creates it
     * lazily, and the audio thread must never be the one to race that. */
    RtlApuLock();
    RtlApuUnlock();

#if SNESRECOMP_SDL3
    want.format = SDL_AUDIO_S16;
    have = want;
    g_audio_stream = SDL_OpenAudioDeviceStream(
        SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &want, audio_stream_cb, NULL);
    if (g_audio_stream) {
        g_audio_device = SDL_GetAudioStreamDevice(g_audio_stream);
        SDL_GetAudioStreamFormat(g_audio_stream, &have, NULL);
    }
#else
    want.format = AUDIO_S16;
    want.samples = 512;
    want.callback = &audio_cb;
    g_audio_device = SDL_OpenAudioDevice(NULL, 0, &want, &have, 0);
#endif
    if (g_audio_device == 0) {
        fprintf(stderr, "audio: device open failed: %s\n", SDL_GetError());
        return 1;
    }

    g_audio_channels = have.channels ? have.channels : 2;
    RtlSetAudioOutputRate(have.freq);
    /* Round to nearest so a non-multiple host rate does not undersize the
     * block: 32040->534, 48000->800, 44100->735. */
    g_frames_per_block = (534 * have.freq + 32040 / 2) / 32040;
    g_audio_block = (uint8 *)calloc(
        (size_t)g_frames_per_block * g_audio_channels * sizeof(int16), 1);
    if (!g_audio_block) {
        fprintf(stderr, "audio: out of memory for mix block\n");
        return 1;
    }
    g_audio_block_cur = g_audio_block_end = g_audio_block;

    snesrecomp_sdl_pause_audio_device(g_audio_device, false);
    fprintf(stderr, "audio: %d Hz, %d ch, %d frames/block\n",
            have.freq, g_audio_channels, g_frames_per_block);
    return 0;
}

/* ── Gamepads ───────────────────────────────────────────────────────────────
 *
 * The scaffold shipped keyboard-only: read_keyboard() below was the whole
 * input path, and snesrecomp_sdl_init() never asked for the gamepad subsystem.
 * A pad mapped in the launcher therefore did nothing once the game started —
 * the launcher has its own SDL context and its own input handling, and none of
 * that survives into the frame loop.
 *
 * Mapping comes from config.ini's [GamepadMap] Controls line, which is what the
 * launcher writes, in the order the runner's own config uses:
 *
 *   Up, Down, Left, Right, Select, Start, A, B, X, Y, L, R
 *
 * Note the default maps SNES A onto the pad's B and SNES B onto the pad's A —
 * the usual Nintendo/Xbox face swap — so on a DualSense, cross is B and circle
 * is A. Reading the file rather than hardcoding that keeps the launcher's
 * screen honest: whatever it shows is what the game uses.
 *
 * Polled once per frame rather than event-driven. Buttons are level state, not
 * edges, so polling cannot drop or double a press the way an event queue can if
 * a frame is slow, and it needs no per-pad bookkeeping. */

#define GAME_PAD_BUTTONS 12

/* SNES bit order of the runner's input word, indexed by [GamepadMap] position. */
static const uint8_t kGameControlBit[GAME_PAD_BUTTONS] = {
    4,  /* Up     */ 5,  /* Down   */ 6,  /* Left  */ 7,  /* Right */
    2,  /* Select */ 3,  /* Start  */ 8,  /* A     */ 0,  /* B     */
    9,  /* X      */ 1,  /* Y      */ 10, /* L     */ 11, /* R     */
};

/* Launcher button names -> SDL. Negative values are axis pseudo-buttons: the
 * triggers report as axes, not buttons, on every modern pad. */
#define GAME_AXIS_L2 (-1)
#define GAME_AXIS_R2 (-2)

struct GamePadName { const char *name; int button; };

static const struct GamePadName kGamePadNames[] = {
    {"DpadUp", SDL_CONTROLLER_BUTTON_DPAD_UP},
    {"DpadDown", SDL_CONTROLLER_BUTTON_DPAD_DOWN},
    {"DpadLeft", SDL_CONTROLLER_BUTTON_DPAD_LEFT},
    {"DpadRight", SDL_CONTROLLER_BUTTON_DPAD_RIGHT},
    {"A", SDL_CONTROLLER_BUTTON_A},
    {"B", SDL_CONTROLLER_BUTTON_B},
    {"X", SDL_CONTROLLER_BUTTON_X},
    {"Y", SDL_CONTROLLER_BUTTON_Y},
    {"Back", SDL_CONTROLLER_BUTTON_BACK},
    {"Start", SDL_CONTROLLER_BUTTON_START},
    {"Guide", SDL_CONTROLLER_BUTTON_GUIDE},
    {"Lb", SDL_CONTROLLER_BUTTON_LEFTSHOULDER},
    {"Rb", SDL_CONTROLLER_BUTTON_RIGHTSHOULDER},
    {"L3", SDL_CONTROLLER_BUTTON_LEFTSTICK},
    {"R3", SDL_CONTROLLER_BUTTON_RIGHTSTICK},
    {"Lt", GAME_AXIS_L2},
    {"Rt", GAME_AXIS_R2},
    {"None", -100},
};

/* Defaults match config.ini's shipped [GamepadMap] Controls line. */
static int g_pad_map[GAME_PAD_BUTTONS] = {
    SDL_CONTROLLER_BUTTON_DPAD_UP,    SDL_CONTROLLER_BUTTON_DPAD_DOWN,
    SDL_CONTROLLER_BUTTON_DPAD_LEFT,  SDL_CONTROLLER_BUTTON_DPAD_RIGHT,
    SDL_CONTROLLER_BUTTON_BACK,       SDL_CONTROLLER_BUTTON_START,
    SDL_CONTROLLER_BUTTON_B,          SDL_CONTROLLER_BUTTON_A,
    SDL_CONTROLLER_BUTTON_Y,          SDL_CONTROLLER_BUTTON_X,
    SDL_CONTROLLER_BUTTON_LEFTSHOULDER, SDL_CONTROLLER_BUTTON_RIGHTSHOULDER,
};

#define GAME_MAX_PADS 2
#if SNESRECOMP_SDL3
static SDL_Gamepad *g_pads[GAME_MAX_PADS];
#else
static SDL_GameController *g_pads[GAME_MAX_PADS];
#endif

static int game_pad_name_to_button(const char *name)
{
    size_t i;
    for (i = 0; i < sizeof(kGamePadNames) / sizeof(kGamePadNames[0]); ++i) {
        if (SDL_strcasecmp(name, kGamePadNames[i].name) == 0)
            return kGamePadNames[i].button;
    }
    return -100;   /* unknown name: leave unbound rather than guess */
}

/* Parse [GamepadMap] Controls from config.ini beside the executable, then the
 * working directory. Absent or malformed leaves the defaults in place — a
 * missing config must not silently unbind the pad. */
/* ── Video pacing ─────────────────────────────────────────────────────────
 *
 * The SNES field rate is 60.0988 Hz, and a desktop display is almost never
 * exactly that. With vsync on, SDL_RenderPresent blocks on the monitor and
 * the guest advances one frame per refresh, so the two rates beat: measured
 * here at 59.9985 fps against a 60.00 Hz panel, which duplicates one frame
 * every 10.0 seconds. On a screen whose animation loops on a 128-frame cycle
 * that reads as an intermittent stutter -- and it is invisible to every
 * framebuffer capture, because the composited frame is correct and simply
 * gets shown twice.
 *
 * So vsync is a setting rather than a constant. This game defaults it OFF -- the
 * title is known to pace badly against vsync on hardware emulators too --
 * and config.ini can turn it back on.
 * With it off the loop must pace itself -- otherwise it free-runs. */
#define GAME_FPS 60.0988

static int g_vsync = 1;

static int game_config_int(const char *section, const char *key, int fallback)
{
    static const char *paths[] = {"config.ini", "../config.ini"};
    size_t p;
    for (p = 0; p < sizeof(paths) / sizeof(paths[0]); ++p) {
        FILE *f = fopen(paths[p], "rb");
        char line[512];
        int in_section = 0;
        if (!f)
            continue;
        while (fgets(line, sizeof(line), f)) {
            char *s = line, *eq;
            while (*s == ' ' || *s == '\t') s++;
            if (*s == '[') {
                in_section = (SDL_strncasecmp(s, section, strlen(section)) == 0);
                continue;
            }
            if (!in_section || *s == '#' || *s == ';')
                continue;
            eq = strchr(s, '=');
            if (!eq)
                continue;
            *eq = 0;
            {
                char *k = s, *v = eq + 1, *end = k + strlen(k);
                while (end > k && (end[-1] == ' ' || end[-1] == '\t')) *--end = 0;
                if (SDL_strcasecmp(k, key) == 0) {
                    int out = atoi(v);
                    fclose(f);
                    return out;
                }
            }
        }
        fclose(f);
    }
    return fallback;
}

/* Pace the loop on the SNES clock when the display is not doing it for us.
 * Accumulates in floating point so the 0.0988 does not get truncated away --
 * rounding to a whole 16 ms would reintroduce the same beat this exists to
 * avoid. Resynchronises rather than spiralling if a frame runs long. */
static void game_frame_limit(void)
{
    static double next_ms = 0.0;
    double now = (double)SDL_GetTicks();
    if (next_ms == 0.0)
        next_ms = now;
    next_ms += 1000.0 / GAME_FPS;
    if (next_ms > now) {
        double wait = next_ms - now;
        if (wait > 100.0) {          /* clock jumped; do not sleep for ever */
            next_ms = now;
            return;
        }
        SDL_Delay((Uint32)wait);
    } else if (now - next_ms > 100.0) {
        next_ms = now;               /* fell far behind; drop the debt */
    }
}

static void game_load_pad_map(void)
{
    static const char *paths[] = {"config.ini", "../config.ini"};
    size_t p;
    for (p = 0; p < sizeof(paths) / sizeof(paths[0]); ++p) {
        FILE *f = fopen(paths[p], "rb");
        char line[512];
        int in_section = 0;
        if (!f)
            continue;
        while (fgets(line, sizeof(line), f)) {
            char *s = line, *eq;
            while (*s == ' ' || *s == '\t') s++;
            if (*s == '[') {
                in_section = (SDL_strncasecmp(s, "[GamepadMap]", 12) == 0);
                continue;
            }
            if (!in_section || *s == '#' || *s == ';')
                continue;
            eq = strchr(s, '=');
            if (!eq)
                continue;
            *eq = 0;
            {
                char *key = s;
                char *val = eq + 1;
                char *end = key + strlen(key);
                int n = 0;
                while (end > key && (end[-1] == ' ' || end[-1] == '\t')) *--end = 0;
                if (SDL_strcasecmp(key, "Controls") != 0)
                    continue;
                for (;;) {
                    char *comma, *tok;
                    while (*val == ' ' || *val == '\t') val++;
                    comma = strchr(val, ',');
                    if (comma) *comma = 0;
                    tok = val;
                    end = tok + strlen(tok);
                    while (end > tok && (end[-1] == ' ' || end[-1] == '\t' ||
                                         end[-1] == '\r' || end[-1] == '\n'))
                        *--end = 0;
                    if (*tok && n < GAME_PAD_BUTTONS)
                        g_pad_map[n++] = game_pad_name_to_button(tok);
                    if (!comma)
                        break;
                    val = comma + 1;
                }
                fprintf(stderr, "[input] gamepad map: %d binding(s) from %s\n",
                        n, paths[p]);
                fclose(f);
                return;
            }
        }
        fclose(f);
    }
    fprintf(stderr, "[input] gamepad map: config.ini has no [GamepadMap]; "
                    "using built-in defaults\n");
}

static void game_open_pads(void)
{
    int slot = 0;
#if SNESRECOMP_SDL3
    int count = 0;
    SDL_JoystickID *ids = SDL_GetGamepads(&count);
    int i;
    for (i = 0; ids && i < count && slot < GAME_MAX_PADS; ++i) {
        if (g_pads[slot])
            continue;
        g_pads[slot] = SDL_OpenGamepad(ids[i]);
        if (g_pads[slot]) {
            const char *n = SDL_GetGamepadName(g_pads[slot]);
            fprintf(stderr, "[input] gamepad %d: %s\n", slot, n ? n : "(unnamed)");
            slot++;
        }
    }
    if (ids)
        SDL_free(ids);
    if (!count)
        fprintf(stderr, "[input] no gamepads detected\n");
#else
    int i;
    for (i = 0; i < SDL_NumJoysticks() && slot < GAME_MAX_PADS; ++i) {
        if (!SDL_IsGameController(i))
            continue;
        if (g_pads[slot])
            continue;
        g_pads[slot] = SDL_GameControllerOpen(i);
        if (g_pads[slot]) {
            const char *n = SDL_GameControllerName(g_pads[slot]);
            fprintf(stderr, "[input] gamepad %d: %s\n", slot, n ? n : "(unnamed)");
            slot++;
        }
    }
    if (!slot)
        fprintf(stderr, "[input] no gamepads detected\n");
#endif
}

static void game_close_pads(void)
{
    int i;
    for (i = 0; i < GAME_MAX_PADS; ++i) {
        if (!g_pads[i])
            continue;
#if SNESRECOMP_SDL3
        SDL_CloseGamepad(g_pads[i]);
#else
        SDL_GameControllerClose(g_pads[i]);
#endif
        g_pads[i] = NULL;
    }
}

/* Seat `slot`'s 12 SNES bits. The left stick doubles as the d-pad: a DualSense
 * player reaching for the stick should not find the game unresponsive. */
static uint16_t read_gamepad(int slot)
{
    uint16_t pad = 0;
    int i;
    Sint16 ax, ay;
    const Sint16 dead = 12000;
    if (slot < 0 || slot >= GAME_MAX_PADS || !g_pads[slot])
        return 0;
    for (i = 0; i < GAME_PAD_BUTTONS; ++i) {
        int b = g_pad_map[i];
        int down = 0;
        if (b == -100)
            continue;
#if SNESRECOMP_SDL3
        if (b == GAME_AXIS_L2)
            down = SDL_GetGamepadAxis(g_pads[slot], SDL_GAMEPAD_AXIS_LEFT_TRIGGER) > 8000;
        else if (b == GAME_AXIS_R2)
            down = SDL_GetGamepadAxis(g_pads[slot], SDL_GAMEPAD_AXIS_RIGHT_TRIGGER) > 8000;
        else
            down = SDL_GetGamepadButton(g_pads[slot], (SDL_GamepadButton)b);
#else
        if (b == GAME_AXIS_L2)
            down = SDL_GameControllerGetAxis(g_pads[slot], SDL_CONTROLLER_AXIS_TRIGGERLEFT) > 8000;
        else if (b == GAME_AXIS_R2)
            down = SDL_GameControllerGetAxis(g_pads[slot], SDL_CONTROLLER_AXIS_TRIGGERRIGHT) > 8000;
        else
            down = SDL_GameControllerGetButton(g_pads[slot], (SDL_GameControllerButton)b);
#endif
        if (down)
            pad |= (uint16_t)(1u << kGameControlBit[i]);
    }
#if SNESRECOMP_SDL3
    ax = SDL_GetGamepadAxis(g_pads[slot], SDL_GAMEPAD_AXIS_LEFTX);
    ay = SDL_GetGamepadAxis(g_pads[slot], SDL_GAMEPAD_AXIS_LEFTY);
#else
    ax = SDL_GameControllerGetAxis(g_pads[slot], SDL_CONTROLLER_AXIS_LEFTX);
    ay = SDL_GameControllerGetAxis(g_pads[slot], SDL_CONTROLLER_AXIS_LEFTY);
#endif
    if (ax < -dead) pad |= 1u << 6;   /* Left  */
    if (ax >  dead) pad |= 1u << 7;   /* Right */
    if (ay < -dead) pad |= 1u << 4;   /* Up    */
    if (ay >  dead) pad |= 1u << 5;   /* Down  */
    return pad;
}

/* Runner input word: 12 button bits per seat.
 * B, Y, Select, Start, Up, Down, Left, Right, A, X, L, R. */
static uint16_t read_keyboard(void)
{
    const uint8_t *keys = snesrecomp_sdl_get_keyboard_state();
    uint16_t pad = 0;
    if (!keys)
        return 0;
    if (keys[SDL_SCANCODE_Z])      pad |= 1u << 0;   /* B */
    if (keys[SDL_SCANCODE_A])      pad |= 1u << 1;   /* Y */
    if (keys[SDL_SCANCODE_RSHIFT]) pad |= 1u << 2;   /* Select */
    if (keys[SDL_SCANCODE_RETURN]) pad |= 1u << 3;   /* Start */
    if (keys[SDL_SCANCODE_UP])     pad |= 1u << 4;
    if (keys[SDL_SCANCODE_DOWN])   pad |= 1u << 5;
    if (keys[SDL_SCANCODE_LEFT])   pad |= 1u << 6;
    if (keys[SDL_SCANCODE_RIGHT])  pad |= 1u << 7;
    if (keys[SDL_SCANCODE_X])      pad |= 1u << 8;   /* A */
    if (keys[SDL_SCANCODE_S])      pad |= 1u << 9;   /* X */
    if (keys[SDL_SCANCODE_C])      pad |= 1u << 10;  /* L */
    if (keys[SDL_SCANCODE_V])      pad |= 1u << 11;  /* R */
    return pad;
}

#if defined(SNES_HAS_LOBBY_CLIENT)
/* Barrier hooks: the admit pump stalls the sim until the peer's inputs
 * arrive, and needs the host to keep sampling the pad and pumping SDL so a
 * stall never looks like a hang. */
static uint16_t netplay_capture_pad(void *ctx)
{
    (void)ctx;
    return (uint16_t)((read_keyboard() | read_gamepad(0)) & 0x0fffu);
}

static void netplay_poll_events(void *ctx, int *want_soft_exit)
{
    SDL_Event event;
    (void)ctx;
    while (SDL_PollEvent(&event)) {
        if (event.type == SDL_QUIT)
            *want_soft_exit = 2;
        if (event.type == SDL_KEYDOWN &&
            SNESRECOMP_SDL_EVENT_KEY(event) == SDLK_ESCAPE)
            *want_soft_exit = 1;
    }
}
#endif /* SNES_HAS_LOBBY_CLIENT */

static int hex_nibble(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static uint8 *read_rom(const char *path, int *size_out)
{
    FILE *file = fopen(path, "rb");
    long size;
    uint8 *data;

    if (!file) {
        fprintf(stderr, "GundamWingEndlessDuelSNESRecomp: cannot open ROM: %s\n", path);
        return NULL;
    }
    fseek(file, 0, SEEK_END);
    size = ftell(file);
    fseek(file, 0, SEEK_SET);
    if (size <= 0) {
        fclose(file);
        return NULL;
    }
    data = (uint8 *)malloc((size_t)size);
    if (!data || fread(data, 1, (size_t)size, file) != (size_t)size) {
        free(data);
        fclose(file);
        fprintf(stderr, "GundamWingEndlessDuelSNESRecomp: short read: %s\n", path);
        return NULL;
    }
    fclose(file);
    *size_out = (int)size;
    return data;
}

/* kGameCodegenIdentity carries the digests as text, because tools/regen.sh
 * and the CI workflow read them from the same struct. The ROM checks want
 * bytes. Returns NULL when the identity is unset or malformed, which the
 * callers read as "cannot verify" rather than "verified". */
static const uint8_t *expected_rom_sha256(uint8_t out[32])
{
    const char *hex = kGameCodegenIdentity.expected_sha256;
    int i;

    if (!hex || strlen(hex) != 64)
        return NULL;
    for (i = 0; i < 32; ++i) {
        int hi = hex_nibble(hex[i * 2]);
        int lo = hex_nibble(hex[i * 2 + 1]);
        if (hi < 0 || lo < 0)
            return NULL;
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    return out;
}

static int expected_rom_crc32(uint32_t *out)
{
    const char *hex = kGameCodegenIdentity.expected_crc32;
    uint32_t v = 0;
    int i;

    if (!hex || strlen(hex) != 8)
        return 0;
    for (i = 0; i < 8; ++i) {
        int n = hex_nibble(hex[i]);
        if (n < 0)
            return 0;
        v = (v << 4) | (uint32_t)n;
    }
    *out = v;
    return 1;
}

/* The dump this project was generated from, if the player parked a copy next
 * to the executable or in the working directory. Not a requirement — it is
 * the habit `tools/regen.sh` documents, and skipping the prompt for someone
 * who already followed it is the whole point. */
static int find_rom_beside_exe(char *out, size_t cap)
{
    const char *name = kGameCodegenIdentity.rom_file;
    FILE *f;

    if (!name || !name[0])
        return 0;
    if (snesrecomp_exe_dir_path(name, out, cap)) {
        f = fopen(out, "rb");
        if (f) {
            fclose(f);
            return 1;
        }
    }
    f = fopen(name, "rb");
    if (f) {
        fclose(f);
        snprintf(out, cap, "%s", name);
        return 1;
    }
    out[0] = '\0';
    return 0;
}

#if defined(RECOMP_LAUNCHER)
/* Pre-boot GUI: ROM picker, display/audio/input settings, verification status.
 * Returns 1 when `out` holds a ROM to boot, 0 to fall through to the
 * text-mode resolve below, and -1 when the player chose to quit. */
static int run_gui_launcher(const char *initial_rom, char *out, size_t cap)
{
    RecompLauncherCSettings ls;
    RecompLauncherCGameInfo gi;
    uint8_t sha[32];
    uint32_t crc;
    char assets_dir[1024];
    int lr;

    /* recomp-ui resolves its staged assets (assets/fonts, assets/img) relative
     * to the executable; the argument exists for hosts with other layouts. */
    if (!snesrecomp_exe_dir_path(".", assets_dir, sizeof(assets_dir)))
        snprintf(assets_dir, sizeof(assets_dir), ".");

    memset(&ls, 0, sizeof(ls));
    ls.output_method = 2;      /* OpenGL */
    ls.window_scale  = 3;      /* matches the game window opened below */
    ls.enable_audio  = 1;
    ls.audio_freq    = 32000;
    ls.volume        = 100;
    ls.player_src[0] = 1;      /* keyboard */

    memset(&gi, 0, sizeof(gi));
    /* One profile call sets the whole SNES identity — theme, pad art, platform
     * label, ROM noun, capability set — so those can never drift apart. */
    launcher_profile_apply("snes", &gi);
    gi.name        = "Gundam Wing Endless Duel";
    gi.region      = "(JPN)";
    gi.num_players = 2;
    /* `sha` outlives the run_window call below, which is the only consumer. */
    if (expected_rom_sha256(sha)) {
        gi.known_sha256     = (const uint8_t (*)[32])&sha;
        gi.num_known_sha256 = 1;
    }
    if (expected_rom_crc32(&crc)) {
        gi.expected_crc     = crc;
        gi.has_expected_crc = 1;
    }
#if defined(SNES_HAS_LOBBY_CLIENT)
    /* The netplay button is capability-gated: without these two fields the
     * launcher never shows it (which is exactly why a build without
     * snesrecomp_enable_recomp_net has no netplay UI). */
    gi.netplay_supported = 1;
    host_lobby_ensure_init();
    gi.netplay = snes_host_lobby_callbacks();
#endif

    out[0] = '\0';
    lr = recomp_launcher_run_window("Gundam Wing Endless Duel", &ls, &gi, assets_dir,
                                    (initial_rom && initial_rom[0]) ? initial_rom
                                                                    : NULL,
                                    out, cap);
#if defined(SNES_HAS_LOBBY_CLIENT)
    /* A lobby launch arms the session; snes_netplay_start() runs after
     * SnesInit, once the guest exists. */
    if (lr == RECOMP_LAUNCHER_RESULT_LAUNCH && ls.netplay_launch.enabled) {
        SnesHostLaunchResult res;
        snes_host_app_apply_launch(&ls.netplay_launch, &res);
        g_netplay_cfg = res.net_cfg;
        g_netplay_pending = 1;
        g_netplay_from_lobby = 1;
        fprintf(stderr,
                "netplay: lobby launch slot=%d bind=%s peer=%s delay=%d\n",
                ls.netplay_launch.local_slot, ls.netplay_launch.bind_hostport,
                ls.netplay_launch.peer_hostport, ls.netplay_launch.input_delay);
    }
#endif
    if (lr == RECOMP_LAUNCHER_RESULT_QUIT)
        return -1;
    if (lr == RECOMP_LAUNCHER_RESULT_LAUNCH && out[0])
        return 1;
    /* UNAVAILABLE (assets or GL missing) and RELAUNCH (only reachable once a
     * host wires gi.rebuild_with_progress, which this scaffold does not) both
     * mean "the GUI produced no ROM" — resolve one the text-mode way. */
    out[0] = '\0';
    return 0;
}
#endif /* RECOMP_LAUNCHER */

/* ── Save-state overlay ───────────────────────────────────────────────────
 *
 * Select + R opens the framework's slot browser. Everything about the menu —
 * which slot is selected, what the panel looks like, and the RtlSaveLoad call
 * itself — lives in snesrecomp/runner/src/snes_savestate_menu.c. The host
 * supplies only the two things a framework module cannot: SDL events, and
 * pixels on the screen.
 *
 * The guest is frozen while the menu is open (the loop below simply stops
 * calling RtlRunFrame), which is what psxrecomp does and the only way "save
 * right here" refers to a definite point in time. Audio goes quiet for the
 * duration, as it would for any paused game. */

static SDL_Texture *g_overlay_tex;

static void game_draw_overlay(SDL_Renderer *renderer)
{
    const uint32_t *px = NULL;
    int w = 0, h = 0;

    if (!snes_savestate_menu_overlay_image(&px, &w, &h))
        return;
    if (!g_overlay_tex) {
        g_overlay_tex = SDL_CreateTexture(renderer, SDL_PIXELFORMAT_ARGB8888,
                                          SDL_TEXTUREACCESS_STREAMING, w, h);
        if (!g_overlay_tex)
            return;
        /* The panel is opaque, so this is not what makes it visible — but it
         * is what stops a future translucent panel from hitting the same
         * alpha trap the game texture did (see SDL_BLENDMODE_NONE below). */
        SDL_SetTextureBlendMode(g_overlay_tex, SDL_BLENDMODE_BLEND);
    }
    SDL_UpdateTexture(g_overlay_tex, NULL, px, w * 4);
    /* Same destination rect as the game texture: the panel is exactly 2x the
     * SNES frame, so it lands on the window with no aspect correction. */
    snesrecomp_sdl_render_texture(renderer, g_overlay_tex, NULL, NULL);
}

/* One present. redraw_game is 0 while the guest is frozen — the texture
 * still holds the last frame, so re-presenting it costs nothing and keeps
 * the window repainting under the overlay. */
static void game_present(SDL_Renderer *renderer, SDL_Texture *texture,
                         int redraw_game)
{
    if (redraw_game) {
        void *pixels = NULL;
        int pitch = 0;
        if (snesrecomp_sdl_lock_texture(texture, NULL, &pixels, &pitch)) {
            RtlDrawPpuFrame((uint8 *)pixels, (size_t)pitch, 0);
            SDL_UnlockTexture(texture);
        }
        /* Offer the composited frame as the next save's thumbnail. Must come
         * after RtlDrawPpuFrame — that is the call that fills renderBuffer. */
        if (g_ppu && g_ppu->renderBuffer)
            snes_savestate_menu_note_frame((const uint32_t *)g_ppu->renderBuffer,
                                           GAME_WIDTH, GAME_HEIGHT);
    }
    SDL_RenderClear(renderer);
    snesrecomp_sdl_render_texture(renderer, texture, NULL, NULL);
    game_draw_overlay(renderer);
    SDL_RenderPresent(renderer);
}

/* Modal pump: runs until the menu closes or the window does. */
static void game_savestate_menu_loop(SDL_Renderer *renderer,
                                     SDL_Texture *texture, int *running)
{
    while (snes_savestate_menu_is_open() && *running) {
        SDL_Event event;

        while (SDL_PollEvent(&event)) {
            if (event.type == SDL_QUIT) {
                *running = 0;
                snes_savestate_menu_close();
            } else if (event.type == SDL_KEYDOWN) {
                /* Escape closes the menu here rather than quitting the game.
                 * The main loop's Escape-quits binding is deliberately not
                 * reachable while the menu is up: a player backing out of a
                 * slot browser has not asked to exit. */
                snes_savestate_menu_handle_key(
                    (int)SNESRECOMP_SDL_EVENT_KEY(event),
                    event.key.repeat ? 1 : 0);
            } else if (event.type == SDL_CONTROLLERDEVICEADDED) {
                game_open_pads();
            } else if (event.type == SDL_CONTROLLERDEVICEREMOVED) {
                game_close_pads();
                game_open_pads();
            }
        }
        /* Keyboard and pad OR'd into the same word the game would have seen,
         * so the menu answers to whichever the player is already holding. */
        snes_savestate_menu_poll_nav(read_keyboard() | read_gamepad(0),
                                     (uint32_t)SDL_GetTicks());
        game_present(renderer, texture, 0);
        SDL_Delay(8);
    }
}

int main(int argc, char **argv)
{
    char rom_path[1024] = "";
    char beside_exe[1024] = "";
    uint8_t sha[32];
    const uint8_t *want_sha = expected_rom_sha256(sha);
    /* First non-flag argument, not argv[1]: this ecosystem's hosts take their
     * flags BEFORE the ROM (`<exe> --launcher <rom>`), so reading argv[1] here
     * would see "--launcher" and conclude no ROM was given. */
    int pos_arg = 0;
    {
        int ai;
        for (ai = 1; ai < argc; ++ai) {
            if (!argv[ai] || !argv[ai][0] || argv[ai][0] == '-') continue;
            pos_arg = ai;
            break;
        }
    }
    int has_positional = (pos_arg != 0);
    /* --launcher / --no-launcher, as the reference host spells them.
     *
     * A positional ROM used to suppress the launcher outright, which was
     * wrong in the case that matters most: Studio always knows the ROM and
     * always passes it, so the launcher never appeared from the Build or
     * Diagnostics tabs. A ROM says WHICH rom, not whether a human is here,
     * so it preloads the launcher instead. Suppression stays explicit,
     * because scripted harnesses run `<exe> <rom>` and would otherwise hang
     * on a window nobody is watching. */
    int force_launcher = 0, no_launcher = 0;
    {
        int ai;
        for (ai = 1; ai < argc; ++ai) {
            if (argv[ai] && strcmp(argv[ai], "--launcher") == 0) force_launcher = 1;
            else if (argv[ai] && strcmp(argv[ai], "--no-launcher") == 0) no_launcher = 1;
        }
    }
    int rom_size = 0;
    uint8 *rom;
    SDL_Window *window;
    SDL_Renderer *renderer;
    SDL_Texture *texture;
    int running = 1;

    if (!has_positional)
        find_rom_beside_exe(beside_exe, sizeof(beside_exe));

#if defined(RECOMP_LAUNCHER)
    /* A dummy video driver means CI or a screenshot harness: there is no one
     * to answer a GUI, and blocking on one would hang the job. */
    {
        const char *vd = getenv("SDL_VIDEODRIVER");
        int headless = (vd && strcmp(vd, "dummy") == 0);

        if (!headless && !no_launcher && (force_launcher || !has_positional)) {
            /* Open on the ROM the player already has, so a second launch is
             * PLAY rather than Change-ROM: the copy beside the executable
             * first, else whatever the last run cached in rom.cfg. */
            char hint[1024];

            /* An explicit ROM outranks a copy beside the exe and the cache:
             * --launcher with a ROM means "show the launcher, loaded with
             * THIS one". */
            if (has_positional)
                snprintf(hint, sizeof(hint), "%s", argv[pos_arg]);
            else
                snprintf(hint, sizeof(hint), "%s", beside_exe);
            if (!hint[0] && !snesrecomp_rom_cache_read(hint, sizeof(hint)))
                hint[0] = '\0';
            {
                int lr = run_gui_launcher(hint, rom_path, sizeof(rom_path));
                if (lr < 0)
                    return 0;          /* player quit from the launcher */
            }
        }
    }
#endif

    /* Players supply their own ROM; none is shipped with this build. Without
     * one from the GUI, resolve it the text-mode way: the positional argument,
     * then the copy beside the executable, then <exe_dir>/rom.cfg, then a
     * native file picker — each checked against the digests above. */
    if (!rom_path[0]) {
        char *resolve_argv[2];
        int resolve_argc = 1;

        resolve_argv[0] = argv[0];
        if (has_positional) {
            resolve_argv[1] = argv[pos_arg];
            resolve_argc = 2;
        } else if (beside_exe[0]) {
            resolve_argv[1] = beside_exe;
            resolve_argc = 2;
        }
        if (!snesrecomp_launcher_resolve_rom_sha256(resolve_argc, resolve_argv,
                                                    rom_path, sizeof(rom_path),
                                                    want_sha)) {
            fprintf(stderr,
                    "usage: %s [path-to-rom.sfc]\n"
                    "You must legally own a copy of Gundam Wing Endless Duel.\n", argv[0]);
            return 1;
        }
    }

    rom = read_rom(rom_path, &rom_size);
    if (!rom)
        return 1;

    RtlRegisterGame(&kGameInfo);
    if (!SnesInit(rom, rom_size)) {
        fprintf(stderr, "GundamWingEndlessDuelSNESRecomp: SnesInit failed\n");
        return 1;
    }

    /* Give the PPU somewhere to composite into. Must happen after SnesInit
     * (which creates g_ppu) and before the first frame is drawn. */
    /* kPpuRenderFlags_NewRenderer selects the priority-buffer compositor. The
     * legacy pixel-at-a-time path documents ignoring several features
     * (ppu.h: "the legacy renderer ignores these"), and colour math only
     * started being exercised at all once the raster-IRQ deadlock was fixed
     * and CGADSUB went from $00 to $3F. */
    PpuBeginDrawing(g_ppu, (uint8_t *)g_render_pixels, GAME_WIDTH * 4,
                    kPpuRenderFlags_NewRenderer);
    start_debug_server();
#if defined(SNES_HAS_LOBBY_CLIENT)
    if (g_netplay_pending) {
        int nrc = snes_netplay_start(&g_netplay_cfg);
        if (nrc != 0)
            fprintf(stderr,
                    "netplay: snes_netplay_start failed (%d) — continuing "
                    "offline\n", nrc);
        g_netplay_pending = 0;
    }
#endif

    /* snesrecomp_sdl_* wrap the SDL2/SDL3 API differences, so this host
     * builds against either backend (-DSNESRECOMP_SDL_BACKEND=SDL2|SDL3). */
    if (!snesrecomp_sdl_init(SDL_INIT_VIDEO | SDL_INIT_AUDIO |
                             SDL_INIT_GAMECONTROLLER)) {
        fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    window = snesrecomp_sdl_create_window("Gundam Wing Endless Duel", GAME_WIDTH * 3,
                                          GAME_HEIGHT * 3, 0);
    /* Default OFF for this title. config.ini is generated at runtime by the
     * launcher and is not shipped, so a fresh install falls back to whatever
     * this default says -- and with vsync on, a 60.00 Hz panel duplicates a
     * frame every 10 s (measured). Set [Video] Vsync = 1 to opt back in. */
    g_vsync = game_config_int("[Video]", "Vsync", 0) != 0;
    renderer = window ? snesrecomp_sdl_create_renderer(window, false, g_vsync)
                      : NULL;
    fprintf(stderr, "[video] vsync %s (config.ini [Video] Vsync)\n",
            g_vsync ? "on" : "off — pacing on the 60.0988 Hz SNES clock");
    texture = renderer
        ? SDL_CreateTexture(renderer, SDL_PIXELFORMAT_ARGB8888,
                            SDL_TEXTUREACCESS_STREAMING,
                            GAME_WIDTH, GAME_HEIGHT)
        : NULL;
    if (!window || !renderer || !texture) {
        fprintf(stderr, "SDL setup failed: %s\n", SDL_GetError());
        return 1;
    }
    /* The PPU composites XRGB: it fills R, G and B and leaves the top byte 0.
     * Measured — every one of the 256x224 pixels comes out with alpha 0. A
     * texture left on the default blend mode therefore draws the whole frame
     * fully transparent over RenderClear's black, which looks exactly like a
     * game that never rendered: correct pixels in the texture, no SDL error,
     * and an identically black window on both the opengl and software
     * renderers. Say that the frame is opaque. */
    SDL_SetTextureBlendMode(texture, SDL_BLENDMODE_NONE);

    /* Sound. Must come after snesrecomp_sdl_init() — SDL_OpenAudioDeviceStream
     * fails with "Audio subsystem is not initialized" otherwise — and after
     * SnesInit, so the APU exists before the audio thread can pull from it. */
    open_audio();

    /* Input. Without the gamepad subsystem above and these two calls, a pad
     * mapped in the launcher does nothing once the game starts — the launcher
     * runs its own SDL context and none of its input state carries over. */
    game_load_pad_map();
    game_open_pads();

    while (running) {
        SDL_Event event;
        uint32 inputs;

        while (SDL_PollEvent(&event)) {
            /* SDL2 spellings: sdl_compat.h defines SDL_ENABLE_OLD_NAMES for
             * SDL3, so these compile against either backend. The SDL3-only
             * names (SDL_EVENT_QUIT, ...) do not. */
            if (event.type == SDL_QUIT)
                running = 0;
            if (event.type == SDL_KEYDOWN &&
                SNESRECOMP_SDL_EVENT_KEY(event) == SDLK_ESCAPE)
                running = 0;
            /* Hotplug: a pad connected after launch must still work, and one
             * unplugged mid-game must not leave a dangling handle. */
            if (event.type == SDL_CONTROLLERDEVICEADDED)
                game_open_pads();
            if (event.type == SDL_CONTROLLERDEVICEREMOVED) {
                game_close_pads();
                game_open_pads();
            }
        }

#if defined(SNES_HAS_LOBBY_CLIENT)
        /* Netplay session: the delay-sync admit pump owns the frame cadence.
         * Both seats' inputs come back merged from the netcode; on a stall
         * the held framebuffer is re-presented so the window stays live. */
        if (snes_netplay_active()) {
            SnesHostBarrierHooks hooks;
            int run = running;
            int admitted;
            memset(&hooks, 0, sizeof(hooks));
            hooks.capture_local_pad = &netplay_capture_pad;
            hooks.poll_events = &netplay_poll_events;
            admitted = snes_host_barrier_admit(g_netplay_from_lobby, &run,
                                               &hooks);
            running = run;
            if (!running)
                break;
            if (admitted) {
                int burst = 0;
                for (;;) {
                    inputs = snes_netplay_published_inputs() |
                             snes_netplay_active_mask();
                    RtlRunFrame(inputs);
                    snes_netplay_finish_frame();
                    /* Catch-up: extra sim ticks when the peer is ahead, so a
                     * hitch on one side doesn't grow into permanent lag. */
                    if (burst >= snes_host_catchup_budget())
                        break;
                    snes_netplay_stage_local(netplay_capture_pad(NULL));
                    if (!snes_netplay_poll_admit())
                        break;
                    burst++;
                }
                game_present(renderer, texture, 1);
            } else {
                game_present(renderer, texture, 0);   /* held frame */
            }
            if (!g_vsync)
                game_frame_limit();
            continue;
        }
#endif /* SNES_HAS_LOBBY_CLIENT */

        /* Seat 0 in the low 12 bits, seat 1 in the next 12. Seats 2..7 (with
         * a multitap) go through RtlSetPadState — see
         * snesrecomp/docs/MULTITAP.md. */
        /* Keyboard and pad are OR'd, not exclusive: either drives seat 0, so
         * plugging a controller never takes the keyboard away. Seat 1 goes in
         * the next 12 bits. */
        inputs = read_keyboard() | read_gamepad(0);
        /* Mask anything still held from the last time the menu closed, then
         * test the Select+R gesture on what is left. Both take the seat-0
         * word before the seat-1 shift, because the overlay is a player-1
         * facility. */
        inputs = snes_savestate_menu_filter_guest_input(inputs);
        if (snes_savestate_menu_poll_open(inputs)) {
            game_savestate_menu_loop(renderer, texture, &running);
            continue;   /* guest was frozen: no frame to run or present */
        }
        {
            /* Scripted input: SNESRECOMP_INPUT_SCRIPT="frame:hexbits,..." holds
             * the given pad bits from that frame until the next entry. Off
             * unless the variable is set.
             *
             * This exists so a screen that can only be reached by pressing
             * buttons — a menu, a pause screen — can be reproduced headlessly
             * and captured by the diagnostic tools. Bits are the runner's input
             * word: 0=B 1=Y 2=Select 3=Start 4=Up 5=Down 6=Left 7=Right 8=A
             * 9=X 10=L 11=R. Example: "400:8,430:0" taps Start at frame 400. */
            static const char *script; static int inited;
            if (!inited) { inited = 1; script = getenv("SNESRECOMP_INPUT_SCRIPT"); }
            if (script) {
                extern int snes_frame_counter;
                const char *s = script; unsigned long want = 0;
                while (*s) {
                    char *e; unsigned long f = strtoul(s, &e, 10);
                    if (*e != ':') break;
                    { unsigned long b = strtoul(e + 1, &e, 16);
                      if ((unsigned long)snes_frame_counter >= f) want = b; }
                    if (*e != ',') break;
                    s = e + 1;
                }
                inputs |= (uint32)want;
            }
        }
        inputs |= (uint32)read_gamepad(1) << 12;
        /* TCP-injected pads (debug_server's "set_controller"). The reference
         * host merges this word; this host never did, so every scripted
         * harness that thought it was pressing Start was actually watching
         * the attract reel. No-op inline in a non-trace build. */
        inputs |= debug_server_get_controller_inputs();
        /* TCP-requested save/load (debug_server's "savestate N" / "loadstate
         * N"). The reference host consumes these; the scaffold never did, so
         * the commands answered OK and then nothing happened. Consuming them
         * here is also what makes the save path checkable without a human at
         * the controller. Both are no-op inlines in a non-trace build. */
        {
            int ls = debug_server_consume_loadstate();
            if (ls >= 0)
                RtlSaveLoad(kSaveLoad_Load, ls);
            ls = debug_server_consume_savestate();
            if (ls >= 0)
                RtlSaveLoad(kSaveLoad_Save, ls);
        }
        RtlRunFrame(inputs);
        game_present(renderer, texture, 1);
        if (!g_vsync)
            game_frame_limit();
    }

    game_close_pads();
    if (g_overlay_tex)
        SDL_DestroyTexture(g_overlay_tex);
    SDL_DestroyTexture(texture);
    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    SDL_Quit();
    free(rom);
    return 0;
}
