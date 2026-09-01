/*
 * Gundam Wing Endless Duel — per-game runtime glue.
 *
 * THIS FILE IS THE PORT. Everything else the scaffold produced is layout,
 * build wiring, and packaging; this is where the actual work happens.
 *
 * The framework does not, and cannot, drive an arbitrary SNES game on its
 * own. Two responsibilities always land on the game host:
 *
 *   1. Deciding what "one frame" means for THIS title, and returning from
 *      run_frame() at that boundary. A real ROM's reset vector never
 *      returns — it enters a main loop that waits on vblank — so somebody
 *      has to choose the yield point.
 *   2. Delivering NMI/IRQ at the hardware edge. Nothing in the framework
 *      pushes an interrupt frame for you (see snesrecomp/docs/FRAME_MODEL_HOSTS.md).
 *
 * What follows is the general LLE-first shape, built from documented bridge
 * entry points: run until the interpreter reaches a deterministic read-only
 * cycle (a real frame boundary, not a guessed address), then resume there
 * next frame. It compiles and it is the right starting point. Whether it
 * boots YOUR title is the porting work — expect to replace the body below
 * with something that understands this game's main loop, the way
 * MetalWarriorsSNESRecomp's src/mw_rtl.c does.
 */

#include "game_rtl.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "common_cpu_infra.h"
#include "common_rtl.h"
#include "cpu_state.h"
#include "snes/interp_bridge.h"
#include "snes/saveload.h"
#include "snes/dma.h"
#include "snes/ppu.h"
#include "snes/snes.h"

extern int snes_frame_counter;

extern CpuState g_cpu;
extern Ppu *g_ppu;

/* One NTSC frame: 262 scanlines x 1364 master clocks. Bounds a productive
 * MMIO loop so it cannot run across several vblanks atomically. */
#define GAME_MASTER_CYCLES_PER_FRAME 357368ull
#define GAME_MASTER_CYCLES_PER_LINE  1364ull

/* 0 until the first frame has booted from the reset vector. */
static uint32_t g_resume_pc;
/* Last cursor that passed cpu_pc24_resumable — the recovery candidate when
 * g_resume_pc comes back implausible from a state load. psxrecomp keeps the
 * same thing (g_last_good_bb_pc) for the same reason. */
static uint32_t g_last_good_resume_pc;

/* Take the bridge's resume cursor, remembering it while it looks like code. */
static void game_set_resume_pc(uint32_t pc)
{
    g_resume_pc = pc;
    if (cpu_pc24_resumable(pc))
        g_last_good_resume_pc = pc;
}

static uint32_t reset_vector(void)
{
    /* $FFFC/$FFFD in the current mapping, read through the guest bus so a
     * mapper or coprocessor window resolves the same way the CPU sees it. */
    uint32_t lo = snes_read(g_snes, 0x00FFFCu);
    uint32_t hi = snes_read(g_snes, 0x00FFFDu);
    return (hi << 8) | lo;
}

/* SnesInit hands the engine's 128 KiB WRAM to the SNES core (snes_init(g_ram))
 * but leaves CpuState pointing at nothing: g_cpu.ram is a host pointer, not
 * simulation state, so no reset path sets it. Wire it here, on the initialize
 * hook SnesInit calls before snes_reset(). Without this the first guest stack
 * push — interp816_pushByte -> cpu_write8 -> cpu->ram[off] — dereferences NULL
 * and the process dies on frame 1 before anything reaches the screen.
 * MetalWarriorsSNESRecomp's src/mw_rtl.c wires it the same way. */
static void GameInitialize(void)
{
    cpu_state_init(&g_cpu, g_ram);
    /* This host's render loop walks the REAL HDMA tables per line
     * (dma_initHdma + dma_primeHdmaFirstLine + dma_doHdma in
     * GameDrawPpuFrame) — the model every pixel of the intro letterbox and
     * the VS-screen band edges was validated under. Upstream's beam-timeline
     * HDMA (snesrecomp #16) would consume the same tables a second time per
     * frame, so opt out. Set here rather than in main.c so it holds no
     * matter which host shell wraps this game. */
    snes_set_hdma_beam_enabled(g_snes, false);
}

/* Read a 16-bit CPU vector out of bank $00 through the guest bus. The
 * argument is the VECTOR ADDRESS ($FFEA, $FFEE, ...); the return value is the
 * handler entry PC, which is what interp_bridge_run_interrupt() wants. Passing
 * the vector address straight through instead runs the vector TABLE as code. */
static uint32_t read_vector_pc24(uint16_t vec_addr)
{
    uint32_t lo = snes_read(g_snes, 0x000000u | vec_addr);
    uint32_t hi = snes_read(g_snes, 0x000000u | (uint16_t)(vec_addr + 1));
    return (hi << 8) | lo;   /* bank $00 */
}

/* Native ($FFEA) vs emulation ($FFFA) NMI vector. */
static uint32_t nmi_vector(void)
{
    return read_vector_pc24(g_cpu.emulation ? 0xFFFAu : 0xFFEAu);
}

/* Native ($FFEE) vs emulation ($FFFE) IRQ vector. */
static uint32_t irq_vector(void)
{
    return read_vector_pc24(g_cpu.emulation ? 0xFFFEu : 0xFFEEu);
}

/* 0 until the reset vector has been entered once. */
static int g_booted;

uint64_t g_park_skipped_master;
uint8 g_q22_at_nmi[256];   /* $22 at NMI entry (diagnostic ring) */   /* diagnostic; see the park block */

/* Scanout latch: OAM/CGRAM as of the frame's start (post-NMI, pre-pass).
 *
 * Hardware scans out the sprite table that is in OAM at line 0; this title's
 * pose DMA fires from the line-219 raster IRQ (measured in Mesen's dma.csv:
 * CGRAM at line 216, OAM at 219 — below every mech sprite), so it belongs to
 * the NEXT scanout, while the art DMA lands in vblank (lines 228-239) and
 * pairs with it there. Rendering from LIVE OAM after the pass showed the
 * mid-pass pose one frame early: presented (pose N, art N-1) against
 * hardware's (pose N-1, art N-1) — the detached-thruster artifact. Marker-
 * anchored proof: our presented pairing was O0+OV1/O2+OV0 where hardware
 * scans out O0+HV0/O2+HV1, byte-identical states, opposite art age.
 *
 * The latch renders each frame from the OAM/CGRAM that were current when its
 * field began. VRAM stays live: the art queue drains in the NMI at the top
 * of this function, i.e. before the snapshot, so it is already correctly
 * aged — and mid-pass VRAM writes don't exist on hardware (active-display
 * VRAM writes are ignored). */
static uint16_t s_scanout_oam[0x100];
static uint8_t  s_scanout_high_oam[0x20];
static uint16_t s_scanout_cgram[0x100];
static int      s_scanout_valid;

void GameRunOneFrame(void)
{
    const uint64_t frame_end =
        g_cpu.master_cycles + GAME_MASTER_CYCLES_PER_FRAME;
    int slices = 0;

    int booting = !g_booted;
    if (!booting && !cpu_pc24_resumable(g_resume_pc)) {
        /* The cursor cannot be code, so running it would execute whatever
         * happens to live there. Take the first plausible candidate instead,
         * loudest first — never synthesize one silently. */
        if (cpu_pc24_resumable(g_last_good_resume_pc)) {
            fprintf(stderr, "game: resume pc $%06X implausible — recovering "
                    "to last good $%06X\n",
                    (unsigned)g_resume_pc, (unsigned)g_last_good_resume_pc);
            g_resume_pc = g_last_good_resume_pc;
        } else {
            fprintf(stderr, "game: resume pc $%06X implausible and no good "
                    "cursor to fall back to — cold booting. Expect a reset, "
                    "not a resume.\n", (unsigned)g_resume_pc);
            booting = 1;
        }
    }
    if (booting) {
        g_resume_pc = reset_vector();
        g_booted = 1;
    } else {
        /* Vblank edge hardware work. FRAME_MODEL_HOSTS.md puts this on the
         * host: a frame-model host owns the HBlank/VBlank edges, so nothing
         * else calls these — measured, ppu_handleVblank and ppu_checkOverscan
         * have no other caller anywhere in the runner. handleVblank re-latches
         * OAMADDR from $2102/$2103 at vblank, which every game relies on when
         * it streams its sprite table during the NMI handler; skip it and OAM
         * writes land at whatever address the last frame left behind. */
        ppu_checkOverscan(g_ppu);
        ppu_handleVblank(g_ppu);
    }

    /* Arm the raster journal BEFORE the field alignment below.
     *
     * The alignment walk SERVICES pending raster IRQs, and those handlers are
     * exactly the ones that write the per-line waveform. Arming after them
     * throws the whole frame's splits away: measured, the journal came back
     * with 0 entries where the unaligned build recorded 17, and the frame drew
     * flat. The journal must be open across every path that can run guest code
     * for this field. */
    ppu_rasterBegin(g_ppu);


    if (!booting && g_snes->nmiEnabled) {
        /* Vblank edge. NMITIMEN gates this: firing before the guest enables
         * NMI would land an interrupt frame in the middle of the SEI boot
         * sequence. The frame is pushed at the *resume* PC so the handler's
         * terminal RTI returns into the interrupted instruction stream. */
        /* Record where the PPU field actually is when NMI is delivered. On
         * hardware this is a vblank event (line 225+); anything inside the
         * visible field means the host frame boundary and the PPU field
         * boundary have drifted, and the splits this NMI schedules for early
         * lines are already behind the beam. */
        g_snes->dbgNmiBeamLine = g_snes->vPos;
        g_snes->dbgBeamLagAtNmi = g_cpu.master_cycles - g_snes->beamMasterLast;
        if (g_snes->vPos < 200u) g_snes->dbgNmiLateCount++;

        g_snes->inNmi = true;
        cpu_push_interrupt_frame_at(&g_cpu, g_resume_pc);
        interp_bridge_set_master_deadline(frame_end);
        /* Freeze the beam across NMI, as for the raster handlers.
         *
         * NMI is a vblank event and everything it writes is the NEXT field's
         * baseline. Letting the beam run during it means a long handler
         * straddles the field boundary and its tail executes at lines 0-2 —
         * where the raster journal, which attributes by beam line, files those
         * writes as mid-frame SPLITS. Measured: a bad frame journaled
         * `$212C = 0x06` at line 1, switching layers back on at the top of the
         * screen and defeating the HDMA letterbox, while a good frame in the
         * same scene journaled nothing at all and folded the identical writes
         * into the baseline. That is the intermittent missing top bar. */
        snes_beam_hold(1);
        /* DIAGNOSTIC: guest DMA work-queue write pointer ($22) at NMI
         * entry, kept in a ring the debug server can read. Writing it to
         * stderr every frame wedged the runtime; a ring costs nothing. */
        g_q22_at_nmi[(unsigned)snes_frame_counter & 255u] = g_ram[0x22];
        (void)interp_bridge_run_interrupt(&g_cpu, nmi_vector());
        snes_beam_hold(0);
        interp_bridge_set_master_deadline(0);
        g_snes->inNmi = false;
        game_set_resume_pc(interp_bridge_lle_resume_pc());
    }

    /* Latch OAM/CGRAM for this frame's render — the scanout state. Taken
     * AFTER the NMI (vblank uploads, including the art drain, are visible
     * this frame, as on hardware) and BEFORE the pass (whose line-216/219
     * IRQ DMAs belong to the NEXT scanout). See the block comment at
     * s_scanout_oam. */
    memcpy(s_scanout_oam, g_ppu->oam, sizeof(s_scanout_oam));
    memcpy(s_scanout_high_oam, g_ppu->highOam, sizeof(s_scanout_high_oam));
    memcpy(s_scanout_cgram, g_ppu->cgram, sizeof(s_scanout_cgram));
    s_scanout_valid = 1;

    /* Charge the frame's HDMA CPU-stall time. Hardware steals these clocks
     * line by line across the visible field; a lump charge here keeps the
     * pass count honest without slicing the CPU per line (measured at
     * ~15 fps when tried). Six active channels on this title's menu/VS
     * screens cost ~10.7% of a frame — uncharged, the menu lag blocks ran a
     * frame short of Mesen. See dma_hdmaMasterEstimate().
     *
     * AFTER the NMI, and with the beam HELD. This block used to sit before
     * the NMI and let the charge advance the beam — in the intro's heavy
     * HDMA stretches that pushed the beam across the field boundary, the
     * NMI was delivered at beam line 1, and its entire write-set journaled
     * as line-1 raster splits: base TM lost its layers and the letterbox's
     * top bar vanished for the stretch (measured: bad frames journal 11
     * entries all at line 1, nmiBeamLine=1; good frames journal 0,
     * nmiBeamLine=257). Hardware order is NMI at vblank first, then the
     * field's stalls. The hold keeps the charge a pure CPU-budget theft so
     * the raster splits the NMI just armed (VTIME=21/23) stay AHEAD of the
     * beam; the end-of-frame drain realigns beam and CPU time as always. */
    if (!booting) {
        uint64_t hdma_master = dma_hdmaMasterEstimate(g_snes->dma);
        if (hdma_master) {
            snes_beam_hold(1);
            g_cpu.master_cycles += hdma_master;
            snes_refresh_exempt();   /* stall, not execution: no refresh tax */
            snes_sync_master_clock(g_snes, g_cpu.master_cycles);
            snes_beam_hold(0);
        }
    }

    /* Run out the frame, servicing raster IRQs as the beam crosses them.
     *
     * The subtle part is what happens when the CPU parks. This title spends
     * most of every frame spinning in the vblank wait at $00:80B4
     * (`LDA $0E / BIT #$0001 / BEQ`), so run_until_quiescent returns almost
     * immediately. Simply breaking there — which is what this loop used to do —
     * means guest time never advances while the CPU is parked, so the beam
     * never reaches the raster line and the IRQ never latches.
     *
     * That is not a cosmetic loss. Measured against Mesen: the game programs
     * VTIME=21 from NMI case $00:83E0, and the IRQ handler at $00:885B
     * dispatches through `JSR ($8875,X)` into $00:8950, which does
     * `LDA #$02 / TRB $0E` — the only thing that clears bit 1 of the vblank
     * flag. Mesen executes that block 29,513 times across frames 3108-4056;
     * this runtime executed it zero times, so $0E stuck at $02, the NMI's
     * `BIT #$03 / BNE` skipped its update forever, and the main loop never
     * woke. Gameplay never started.
     *
     * So when the CPU parks, walk the beam a scanline at a time and let the
     * IRQ latch. That costs a bounded number of cheap beam steps only while
     * the CPU has nothing to do — it does not slice CPU execution per line,
     * which was measured at ~15 fps. */
    while (g_cpu.master_cycles < frame_end && slices < 400) {
        uint64_t next;
        uint32_t to_irq;

        slices++;

        /* Dispatch a pending IRQ BEFORE running the CPU. When the beam step
         * below latches one, letting run_until_quiescent go first was measured
         * costing ~1.7 scanlines of emulated time before the handler ran
         * (latch at v=21, dispatch at v=23.5). This title's split handler
         * programs its NEXT split only two lines ahead ($00:888E, VTIME=$17
         * written from the line-21 handler), so that latency alone pushed the
         * beam past the new target before it was armed — the line-23 split
         * never fired and INIDISP brightness was never restored: the entire
         * gameplay demo rendered black. Handler first, CPU after. */
        if (!g_snes->inIrq) {
            interp_bridge_set_master_deadline(frame_end);
            (void)interp_bridge_run_until_quiescent(&g_cpu, g_resume_pc);
            interp_bridge_set_master_deadline(0);
            game_set_resume_pc(interp_bridge_lle_resume_pc());
        }

        if (g_snes->inIrq) {            cpu_push_interrupt_frame_at(&g_cpu, g_resume_pc);
            /* Freeze the beam for the handler's duration: on silicon a raster
             * handler runs within tens of clocks of the latch, but here
             * master_cycles already carries the pre-empted mainline's time, and
             * walking the beam through it mid-handler pushes any close-ahead
             * split target ($00:888E schedules VTIME=+2 lines) into the past
             * before it is armed. */
            snes_beam_hold(1);
            interp_bridge_set_master_deadline(frame_end);
            (void)interp_bridge_run_interrupt(&g_cpu, irq_vector());
            interp_bridge_set_master_deadline(0);
            snes_beam_hold(0);            game_set_resume_pc(interp_bridge_lle_resume_pc());
            continue;
        }

        if (g_cpu.master_cycles >= frame_end)
            break;

        /* CPU is parked (WAI, or spinning on an MMIO/flag wait). The beam does
         * not stop for it: step forward so a programmed raster IRQ can latch,
         * then let the CPU run again.
         *
         * Step to the NEXT SCHEDULED MATCH when one is nearer than a scanline,
         * a whole scanline otherwise. A fixed whole-line step lands the beam
         * up to a line past the match, and that overshoot eats into the
         * two-line window this title's chained splits leave the handler. */
        /* Diagnostic: where is the CPU parked, and how much time do we skip
         * on its behalf? Hardware has no "park" -- a spinning CPU still burns
         * bus cycles -- so every clock skipped here is a cycle the guest did
         * NOT execute. Measured at ~109k master clocks/frame (30% of a frame),
         * which is why our cpu_cycles/frame reads ~31k against hardware's
         * ~43k. Harmless only if the CPU really is in a pure poll loop; if it
         * is parked somewhere that does work, we are dropping that work.
         * Enabled with SNESRECOMP_PARK_STATS=1. */
        {
            static int park_stats = -1;
            static uint64_t ev, last_pc;
            static int last_frame;
            extern int snes_frame_counter;
            if (park_stats < 0) {
                const char *e = getenv("SNESRECOMP_PARK_STATS");
                park_stats = (e && e[0] && e[0] != '0') ? 1 : 0;
            }
            if (park_stats) {
                ev++;
                last_pc = g_resume_pc;
                if (snes_frame_counter != last_frame) {
                    if (last_frame)
                        fprintf(stderr,
                                "[park] f=%d events=%llu skipped_master=%llu "
                                "last_resume_pc=$%06X\n",
                                last_frame, (unsigned long long)ev,
                                (unsigned long long)g_park_skipped_master,
                                (unsigned)last_pc);
                    last_frame = snes_frame_counter; ev = 0;
                    g_park_skipped_master = 0;
                }
            }
        }

        to_irq = snes_master_clocks_until_irq(g_snes);
        /* +1: snes_advance_beam's window test is [h, h+span) — EXCLUSIVE of
         * the end. A step landing exactly ON the match point does not latch;
         * measured, the latch then slipped to the next CPU run and the
         * dispatch landed 2.5 lines late. Land one clock past the match. */
        if (to_irq)
            to_irq += 1;
        if (to_irq == 0 || to_irq > GAME_MASTER_CYCLES_PER_LINE)
            to_irq = (uint32_t)GAME_MASTER_CYCLES_PER_LINE;
        next = g_cpu.master_cycles + to_irq;
        if (next > frame_end)
            next = frame_end;
        {
            static int ps = -1;
            if (ps < 0) { const char *e = getenv("SNESRECOMP_PARK_STATS");
                          ps = (e && e[0] && e[0] != '0') ? 1 : 0; }
            if (ps) g_park_skipped_master += (next - g_cpu.master_cycles);
        }
        g_cpu.master_cycles = next;
        /* Parked time is idle skipped on the guest's behalf — it displaces no
         * work, so exempt it from the DRAM-refresh tax; taxing it would drift
         * the IRQ latch point this jump aims at. */
        snes_refresh_exempt();
        snes_sync_master_clock(g_snes, g_cpu.master_cycles);
    }

    /* Drain the beam to the frame boundary, servicing whatever latches on the
     * way.
     *
     * The walk above exits on CPU time (master_cycles >= frame_end), but the
     * BEAM can still be behind: snes_advance_beam stops at every latch and is
     * frozen across handlers, so clocks it did not consume stay owed. That debt
     * carries into the next frame, which is how the host frame boundary drifts
     * away from the PPU field boundary — and once the drift passes the chain's
     * first split line, the NMI programs that split BEHIND the beam, where a
     * window comparator cannot fire it at all. Measured: NMI delivered at line
     * 18 on attract loop 1 and lines 30/34 later; targetInPast 0.00 -> 1.00 per
     * frame; latches 5.01 -> 2.01 per frame; the demo's palette upload, which
     * the chain drives, never ran again.
     *
     * Draining HERE rather than before the next frame's NMI is deliberate: the
     * raster journal is armed for THIS field, so splits serviced during the
     * drain are still recorded. Draining at the start of the next frame threw
     * them away — measured, the journal went from 17 entries to 0 and the frame
     * drew flat. */
    {
        int drain = 0;
        /* Run the beam on to the START OF VBLANK, servicing what latches.
         *
         * Two things at once, and both have to happen here rather than at the
         * next frame's start. Paying the debt keeps the beam from falling
         * behind the CPU; stopping at line 225 rather than at frame_end makes
         * the host's frame boundary coincide with the PPU's FIELD boundary, so
         * the next NMI is delivered in vblank the way hardware delivers it.
         *
         * Measured, each half alone is not enough: draining to frame_end fixes
         * the lag but preserves whatever phase the frame started with (NMI
         * still arrived at line 30), while correcting the phase at the START of
         * the next frame consumed that field's raster splits before the journal
         * was armed and the frame drew flat (journal 17 entries -> 0). */
        while (g_snes->vPos < 225u && drain++ < 600) {
            uint32_t step;
            if (g_snes->inIrq) {
                cpu_push_interrupt_frame_at(&g_cpu, g_resume_pc);
                snes_beam_hold(1);
                (void)interp_bridge_run_interrupt(&g_cpu, irq_vector());
                snes_beam_hold(0);
                game_set_resume_pc(interp_bridge_lle_resume_pc());
                continue;
            }
            step = snes_master_clocks_until_line(g_snes, 225u);
            if (!step) break;
            g_cpu.master_cycles += step;
            snes_sync_master_clock(g_snes, g_cpu.master_cycles);
        }
    }
}

void GameDrawPpuFrame(void)
{
    int line;

    /* Swap the scanout latch in for the render (see s_scanout_oam): the
     * presented frame must show the OAM/CGRAM its field STARTED with, not
     * whatever the mid-pass IRQ DMAs left behind. The live arrays are
     * restored afterwards so guest-visible state is untouched. Bypassed for
     * render_inject, whose contract is "render exactly the state given". */
    uint16_t live_oam[0x100];
    uint8_t  live_high_oam[0x20];
    uint16_t live_cgram[0x100];
    int latched = s_scanout_valid && !g_ppu_scanout_latch_bypass;
    if (latched) {
        memcpy(live_oam, g_ppu->oam, sizeof(live_oam));
        memcpy(live_high_oam, g_ppu->highOam, sizeof(live_high_oam));
        memcpy(live_cgram, g_ppu->cgram, sizeof(live_cgram));
        memcpy(g_ppu->oam, s_scanout_oam, sizeof(s_scanout_oam));
        memcpy(g_ppu->highOam, s_scanout_high_oam, sizeof(s_scanout_high_oam));
        memcpy(g_ppu->cgram, s_scanout_cgram, sizeof(s_scanout_cgram));
    }

    /* Render the visible field, stepping HDMA once per scanline.
     *
     * A frame-model host owns the HBlank edges, so nothing else drives HDMA —
     * dma_doHdma had no caller anywhere in the runner because the engine did
     * not exist. This title needs it: through the intro, channel 1 is
     * HDMA-active onto $212C (TM), turning BG layers off on the top and bottom
     * bands and on in the middle, with OBJ left enabled so the characters draw
     * over the black bars. Without the per-line stream every scanline got one
     * TM value and the letterbox vanished.
     *
     * Stepping HDMA here rather than interleaving CPU execution per scanline
     * is deliberate: it costs a few register writes per line and leaves guest
     * timing untouched. Chopping the CPU into 262 slices to walk the beam was
     * measured at ~15 fps instead of 60. */
    /* Line-0 register state first, so per-line HDMA (the intro letterbox's
     * TM stream) and journal entries both land ON TOP of it, in beam order. */
    ppu_rasterRenderBegin(g_ppu);
    dma_initHdma(g_snes->dma);
    /* Hardware's vblank init also performs each channel's FIRST transfer
     * before any visible pixel; without it the first rendered row keeps the
     * pre-HDMA register state — a one-line bright strip above the intro
     * letterbox, visible wherever the backdrop isn't dark. State-neutral:
     * the per-line cadence below still consumes the table exactly as the
     * Mesen-validated band edges require. */
    dma_primeHdmaFirstLine(g_snes->dma);
    for (line = 1; line <= 224; line++) {
        /* Replay the PREVIOUS line's journal entries, not this line's.
         *
         * A raster split works by writing registers in H-blank, after the
         * line has been drawn, so the write takes effect on the NEXT line.
         * The journal stores only (line, reg, value) with no horizontal
         * position, and every write this game makes is in H-blank -- Mesen
         * stamps them at hclk 1190-1322 of a ~1364-clock line. Applying them
         * to the line they were recorded on therefore lands them one line
         * early, which is exactly what the pixels showed: our band edges at
         * output rows 54/102/134/182 against hardware's 55/103/135/183, and
         * the INIDISP teardown a row early at 214.
         *
         * A write early in a visible line would take effect part-way along
         * that line on hardware; a whole-line renderer cannot express that
         * either way, and for a title that raster-splits deliberately, the
         * H-blank case is the one that matters. */
        ppu_rasterApplyLine(g_ppu, line - 1);
        {
            /* A journalled $420C write: apply the enable mask. Channels this
             * switches on are marked as owing a table initialization, which
             * dma_doHdma() spends the NEXT slot performing -- it no longer
             * initializes and transfers on the same line.
             *
             * Doing both at once here put the first transfer one scanline
             * early, and every band edge with it: measured against Mesen,
             * this screen enables $420C on line 21 and hardware's first
             * $212C write lands on line 23, while we wrote it on line 22. */
            uint8_t hdmaen;
            if (ppu_rasterTakeHdmaen(&hdmaen))
                dma_startDma(g_snes->dma, hdmaen, true);
        }
        ppu_runLine(g_ppu, line);
        /* HDMA runs in the H-blank AFTER this line, so what it writes lands
         * on the NEXT one. Stepping it before the line instead made every
         * band edge one output row early -- measured in a sprite-free column
         * against Mesen, our transitions sat at rows 54/102/134/182 where
         * hardware has 55/103/135/183.
         *
         * Note the loop renders lines 1..224 into output rows 0..223, so a
         * per-line register log reading "changed at line 55" is output row
         * 54. Comparing that index against the oracle's write scanline makes
         * an off-by-one look correct; compare pixels. */
        dma_doHdma(g_snes->dma);
    }

    if (latched) {
        memcpy(g_ppu->oam, live_oam, sizeof(live_oam));
        memcpy(g_ppu->highOam, live_high_oam, sizeof(live_high_oam));
        memcpy(g_ppu->cgram, live_cgram, sizeof(live_cgram));
    }
}

static void GameStateSaveExtra(SaveLoadInfo *sli);
static void GameStateLoadExtra(SaveLoadInfo *sli, uint32_t version);

const RtlGameInfo kGameInfo = {
    .title = "gundamwingendlessduel",
    .initialize = &GameInitialize,
    .run_frame = &GameRunOneFrame,
    .session_reset = &GameSessionReset,
    .draw_ppu_frame = &GameDrawPpuFrame,
    .state_save_extra = &GameStateSaveExtra,
    .state_load_extra = &GameStateLoadExtra,
    .save_name_prefix = "save",
};

/*
 * Host execution cursor. g_resume_pc / g_booted live here, not in the guest
 * blob snes_saveload writes, so nothing but these hooks can carry them across
 * a state load. Rollback goes through the same path
 * (RtlRollbackSaveToMemory -> RtlSaveSnapshotToMemory -> state_save_extra),
 * and without them a rewind restored WRAM and g_cpu but left the resume PC on
 * the timeline it had just discarded: measured as the netplay FOLLOWER
 * wedging at reset PPU state (inidisp=$80, bgmode 0) from its first episode
 * onward while its sim tick kept advancing — the "guest stayed on a black
 * screen while the host kept simulating" report. MetalWarriorsSNESRecomp
 * carries the same chunk for the same reason (mw_rtl.c MwStateSaveExtra).
 *
 * Not included: the scanout latch (presentation, not simulation — rendering
 * is disabled during resim) and g_q22_at_nmi (a diagnostic ring).
 */
#define GAME_LLE_CHUNK_MAGIC 0x4C4C4757u  /* 'GWLL' */

typedef struct {
    uint32_t magic;
    uint32_t resume_pc;
    uint32_t booted;
    uint32_t reserved;
} GameLleChunk;

static void GameStateSaveExtra(SaveLoadInfo *sli)
{
    GameLleChunk c;
    memset(&c, 0, sizeof(c));
    c.magic     = GAME_LLE_CHUNK_MAGIC;
    c.resume_pc = g_resume_pc;
    c.booted    = (uint32_t)(g_booted ? 1 : 0);
    sli->func(sli, &c, sizeof(c));
}

static void GameStateLoadExtra(SaveLoadInfo *sli, uint32_t version)
{
    GameLleChunk c;
    (void)version;
    memset(&c, 0, sizeof(c));
    sli->func(sli, &c, sizeof(c));
    if (c.magic != GAME_LLE_CHUNK_MAGIC) {
        /* Refusing loudly beats resuming at a PC we did not write: a silently
         * ignored chunk is exactly the wedge this hook exists to prevent. */
        fprintf(stderr, "game: state extra chunk bad magic $%08X — ignored\n",
                (unsigned)c.magic);
        return;
    }
    if (c.booted && !cpu_pc24_resumable(c.resume_pc)) {
        /* Say so here rather than let it surface as a wedged guest three
         * frames later with no clue where it came from. */
        fprintf(stderr, "game: state extra carries an implausible resume pc "
                "$%06X — ignoring it\n", (unsigned)c.resume_pc);
        return;
    }
    g_resume_pc = c.resume_pc;
    g_booted    = c.booted ? 1 : 0;
}

void GameSessionReset(void)
{
    /* Rematch / soft-return: clear anything that must not survive a new
     * session. recomp-ai-rules/NETPLAY.md §3 — sticky state that "has always
     * been fine" is the usual desync culprit, because single-player never
     * re-enters the boot path twice in one process. */
    g_resume_pc = 0;
    g_booted = 0;
    g_last_good_resume_pc = 0;
    /* Cold-boot the CPU from RESET rather than resuming stale registers or a
     * stale WAI: g_cpu survives a session teardown, so a rematch that skipped
     * this would start frame 1 mid-flight. Re-points g_cpu.ram too. */
    cpu_state_init(&g_cpu, g_ram);
    /* Scanout latch belongs to the retired machine's PPU (see s_scanout_oam):
     * presenting frame 1 of a rematch from the previous match's OAM/CGRAM
     * would show a stale sprite table over a freshly booted guest. */
    s_scanout_valid = 0;
    memset(s_scanout_oam, 0, sizeof(s_scanout_oam));
    memset(s_scanout_high_oam, 0, sizeof(s_scanout_high_oam));
    memset(s_scanout_cgram, 0, sizeof(s_scanout_cgram));
    memset(g_q22_at_nmi, 0, sizeof(g_q22_at_nmi));
    /* Self-reporting: a rematch that boots correctly prints this immediately
     * before its SnesInit. Its ABSENCE in a black-screen rematch log is the
     * diagnosis — the hook was not reached, so the guest resumed a dead
     * machine's PC. */
    fprintf(stderr, "game: session reset (cold boot for rematch)\n");
}
