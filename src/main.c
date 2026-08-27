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

    out[0] = '\0';
    lr = recomp_launcher_run_window("Gundam Wing Endless Duel", &ls, &gi, assets_dir,
                                    (initial_rom && initial_rom[0]) ? initial_rom
                                                                    : NULL,
                                    out, cap);
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

int main(int argc, char **argv)
{
    char rom_path[1024] = "";
    char beside_exe[1024] = "";
    uint8_t sha[32];
    const uint8_t *want_sha = expected_rom_sha256(sha);
    int has_positional = (argc > 1 && argv[1] && argv[1][0] && argv[1][0] != '-');
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

        if (!headless && !has_positional) {
            /* Open on the ROM the player already has, so a second launch is
             * PLAY rather than Change-ROM: the copy beside the executable
             * first, else whatever the last run cached in rom.cfg. */
            char hint[1024];

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
            resolve_argv[1] = argv[1];
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

    /* snesrecomp_sdl_* wrap the SDL2/SDL3 API differences, so this host
     * builds against either backend (-DSNESRECOMP_SDL_BACKEND=SDL2|SDL3). */
    if (!snesrecomp_sdl_init(SDL_INIT_VIDEO | SDL_INIT_AUDIO)) {
        fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    window = snesrecomp_sdl_create_window("Gundam Wing Endless Duel", GAME_WIDTH * 3,
                                          GAME_HEIGHT * 3, 0);
    renderer = window ? snesrecomp_sdl_create_renderer(window, false, true)
                      : NULL;
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

    while (running) {
        SDL_Event event;
        void *pixels = NULL;
        int pitch = 0;
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
        }

        /* Seat 0 in the low 12 bits, seat 1 in the next 12. Seats 2..7 (with
         * a multitap) go through RtlSetPadState — see
         * snesrecomp/docs/MULTITAP.md. */
        inputs = read_keyboard();
        RtlRunFrame(inputs);

        if (snesrecomp_sdl_lock_texture(texture, NULL, &pixels, &pitch)) {
            RtlDrawPpuFrame((uint8 *)pixels, (size_t)pitch, 0);
            SDL_UnlockTexture(texture);
        }
        SDL_RenderClear(renderer);
        snesrecomp_sdl_render_texture(renderer, texture, NULL, NULL);
        SDL_RenderPresent(renderer);
    }

    SDL_DestroyTexture(texture);
    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    SDL_Quit();
    free(rom);
    return 0;
}
