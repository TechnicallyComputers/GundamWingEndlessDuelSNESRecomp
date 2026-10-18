/* Real-ROM regression for file/memory execution continuity. Run in an empty
 * directory: all snapshots below belong to this test. No audio device opens. */
#define main GwedDesktopMain
#include "../src/main.c"
#undef main
#include "snapshot_guard.h"

static void check(bool ok, const char *what) {
    if (!ok) { fprintf(stderr, "FAIL: %s\n", what); exit(1); }
    printf("PASS: %s\n", what);
}

static uint32_t pixels[256 * 240];
static void frame(unsigned input) {
    RtlRunFrame(input);
    RtlDrawPpuFrame((uint8_t *)pixels, 256 * 4, kPpuRenderFlags_NewRenderer);
}
static void replay(unsigned frames) {
    for (unsigned i = 0; i < frames; ++i)
        frame(i < frames / 2 ? SNES_PAD_A : 0);
}

int main(int argc, char **argv) {
    check(argc == 2 || argc == 3, "owner ROM supplied");
    FILE *f = fopen(argv[1], "rb");
    check(f != NULL, "ROM opens");
    fseek(f, 0, SEEK_END); long size = ftell(f); rewind(f);
    uint8_t *rom = malloc((size_t)size);
    check(rom && fread(rom, 1, (size_t)size, f) == (size_t)size, "ROM reads");
    fclose(f);
    SDL_SetMainReady();
    check(snesrecomp_sdl_init(SDL_INIT_EVENTS), "SDL initializes without audio output");
    RtlRegisterGame(&kGameInfo);
    GameSessionReset();
    check(SnesInit(rom, (size_t)size) != NULL, "Endless Duel initializes");
    PpuBeginDrawing(g_ppu, (uint8_t *)pixels, 256 * 4, kPpuRenderFlags_NewRenderer);
    const size_t capacity = 2u << 20;
    uint8_t *start = malloc(capacity), *control = malloc(capacity), *restored = malloc(capacity);
    check(start && control && restored, "snapshot buffers allocate");
    if (argc == 3) {
        check(RtlLoadSnapshot("restart.state"), "file loads in a new process");
        replay(30);
        size_t rn = RtlSaveSnapshotToMemory(restored, capacity);
        f = fopen("expected.bin", "rb"); check(f != NULL, "replay reference opens");
        size_t cn = fread(control, 1, capacity, f); fclose(f);
        check(rn == cn && !memcmp(restored, control, rn), "new-process replay matches complete snapshot");
        return 0;
    }
    replay(600);
    check(g_cpu.master_cycles != 0, "guest executes real boot frames");
    size_t n = RtlSaveSnapshotToMemory(start, capacity);
    uint64_t clock = g_cpu.master_cycles;
    check(n != 0, "memory snapshot saves");
    replay(30);
    size_t cn = RtlSaveSnapshotToMemory(control, capacity);
    check(RtlLoadSnapshotFromMemory(start, n), "memory snapshot loads");
    check(g_cpu.master_cycles == clock, "file serializer restores recompiled CPU clock");
    replay(30);
    size_t rn = RtlSaveSnapshotToMemory(restored, capacity);
    check(rn == cn && !memcmp(restored, control, rn), "30-frame replay matches complete snapshot");
    n = RtlRollbackSaveToMemory(start, capacity);
    check(n != 0, "rollback snapshot saves with the execution extension");
    replay(10);
    cn = RtlRollbackSaveToMemory(control, capacity);
    check(RtlRollbackLoadFromMemory(start, n), "rollback snapshot restores");
    replay(10);
    rn = RtlRollbackSaveToMemory(restored, capacity);
    check(rn == cn && !memcmp(restored, control, rn), "rollback replay matches complete snapshot");
    check(RtlSaveSnapshot("restart.state"), "restart snapshot saves");
    replay(30);
    cn = RtlSaveSnapshotToMemory(control, capacity);
    f = fopen("expected.bin", "wb");
    check(f && fwrite(control, 1, cn, f) == cn, "replay reference saves"); fclose(f);
    n = RtlSaveSnapshotToMemory(start, capacity);
    const void *payload = NULL; size_t payload_size = 0;
    check(snes_snapshot_guard_open(start, n, kGameInfo.snapshot_guard_identity(),
                                  &payload, &payload_size), "snapshot envelope opens");
    check(!RtlLoadSnapshotFromMemory(payload, payload_size), "legacy unguarded snapshot is rejected");
    rn = RtlSaveSnapshotToMemory(restored, capacity);
    check(rn == cn && !memcmp(restored, control, rn), "legacy rejection leaves the machine untouched");
    /* Corruption must be rejected before it can replace the current machine. */
    n = RtlSaveSnapshotToMemory(start, capacity);
    start[n / 2] ^= 1;
    check(!RtlLoadSnapshotFromMemory(start, n), "corrupt snapshot is rejected");
    rn = RtlSaveSnapshotToMemory(restored, capacity);
    check(rn == cn && !memcmp(restored, control, rn), "rejection leaves the machine untouched");
    puts("All Endless Duel state checks passed.");
    return 0;
}
