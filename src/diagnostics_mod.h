#ifndef GWED_DIAGNOSTICS_MOD_H
#define GWED_DIAGNOSTICS_MOD_H

/*
 * The two things the performance log cannot learn on its own.
 *
 * The mod runtime hands a mod one callback, fired per emulated frame, and no
 * window, no renderer, and no way to see inside the host loop. That was enough
 * to say a frame took 28 ms and not enough to say why -- and the first real
 * report came from a player on a 4K TV at 300% Windows scaling, where "why" is
 * very likely the output surface rather than the emulation.
 *
 * So the host tells it. Both calls are inert until the diagnostics plugin is
 * active, cost nothing when it is not, and touch no guest state either way.
 */

struct SDL_Window;
struct SDL_Renderer;

/*
 * Publish the video objects, once, after they exist. Emits the '# video' line:
 * renderer backend actually chosen, window size in LOGICAL units, drawable
 * size in PHYSICAL pixels, the ratio between them (the DPI scale the process
 * really got), fullscreen state and display mode.
 *
 * The ratio is the point. Windows DPI awareness is process-wide and one-way,
 * and this host's launcher sets per-monitor-v2 before the game window is made
 * with no ALLOW_HIGHDPI of its own -- so which of logical and physical the
 * renderer is actually filling is a question the code cannot answer by
 * inspection. Measuring both and printing the ratio settles it per machine.
 */
void GwedDiag_NoteVideo(struct SDL_Window *window,
                        struct SDL_Renderer *renderer, int vsync_on);

/*
 * Time spent in one present -- everything from the first draw call to
 * SDL_RenderPresent returning.
 *
 * Attributed to the NEXT frame line, because a present belongs to the host
 * iteration that produced it and the frame hook fires earlier. Under
 * fast-forward one present covers several emulated frames, so it is reported
 * on the frame it landed on rather than divided between them; the header says
 * so, so nobody reads a per-frame present cost that was never measured.
 */
void GwedDiag_NotePresentMs(double ms);

/*
 * The breakdown inside that present, in milliseconds.
 *
 * `present=` alone was one number covering six operations -- clear, blit,
 * overlay, rewind filmstrip, OSD chrome, and the swap -- plus the guest
 * texture upload, which happens BEFORE the present timer starts and so was
 * being charged to emulation. A 50 ms spike in that number scoped to nothing.
 *
 * The split that matters is `swap` (SDL_RenderPresent alone) against
 * everything else: work in swap belongs to the driver, the display pipeline
 * or a swapchain rebuild, and work outside it is ours. Reported only on a
 * spike line and in the per-second summary, so a healthy log does not grow.
 *
 * Pass -1 for any phase that did not run this iteration.
 */
void GwedDiag_NotePresentPhases(double upload_ms, double clear_ms,
                                double blit_ms, double overlay_ms,
                                double osd_ms, double swap_ms);

/*
 * A host-loop event worth correlating against a spike: a window state change
 * (resize, pixel-size change, display change, occlusion, focus), a texture or
 * renderer rebuild, a vsync change.
 *
 * The reason this exists rather than a platform guess: a spike that lands
 * inside the swap looks like "the compositor" on X11 and like "the driver" on
 * Windows, and neither is actionable. If the same event precedes the spike on
 * both, it is ours. Text is copied, so callers may pass a stack buffer.
 * Logged immediately with a timestamp, and the most recent one is echoed on
 * the next spike line so cause and effect sit together.
 */
void GwedDiag_NoteEvent(const char *what);

/*
 * The rest of the host iteration: emulation, the SDL event pump, and the
 * frame limiter's wait (-1 when vsync paces instead).
 *
 * With this and the present phases, every millisecond of a frame is
 * attributed. The spike autopsy needs that: a stall that is NOT in the swap
 * and NOT in our drawing was previously an unexplained remainder, and the two
 * spikes in the 2026-09-10 report were one of each kind.
 *
 * Emulation is a SUM, not a single call -- a fast-forward iteration runs
 * several guest frames inside one present.
 */
void GwedDiag_NoteLoopPhases(double emulate_ms, double pump_ms,
                             double limit_ms);

/*
 * Thread CPU time burned inside emulation this iteration, against the wall
 * time already reported by NoteLoopPhases.
 *
 * This is the discriminator a stalled frame actually needs. A 72 ms frame
 * whose emulation burned 72 ms of CPU is host code doing too much work; a
 * 72 ms frame that burned 2 ms is BLOCKED -- a page fault, an allocation that
 * reached the kernel, a lock, a blocking write -- and the two need entirely
 * different hunts. Measured on Linux with CLOCK_THREAD_CPUTIME_ID and on
 * Windows with GetThreadTimes, so it reads the same on both platforms the
 * bug is reported on.
 */
void GwedDiag_NoteEmulateCpuMs(double cpu_ms);

/* Same discrimination for the guest->texture upload: a streaming-texture lock
 * that waits on the GPU burns no CPU, a slow copy burns all of it, and they
 * need different fixes. */
void GwedDiag_NoteUploadCpuMs(double cpu_ms);

/* SDL_LockTexture alone. On a GPU backend this is where the frame waits for
 * the GPU to release the streaming texture it is still reading; the copy that
 * follows is CPU. Splitting them is what decides between rotating the texture
 * and making the copy cheaper. */
void GwedDiag_NoteTextureLockMs(double ms);

/* The two halves of what used to be reported as `copy`: writing the pixels
 * into the mapped texture, and SDL_UnlockTexture -- which on a GPU backend is
 * where the staging buffer is handed to the GPU and where a transfer can
 * block. Measured separately because they have different fixes. */
void GwedDiag_NoteTextureFillUnlockMs(double fill_ms, double unlock_ms);

/* Wall and thread-CPU for the whole host loop iteration.
 *
 * `other` in the autopsy is a subtraction, so it can only say the time was not
 * in any measured phase. This bounds it: time missing from a frame is either
 * inside the loop body (iter_ms covers it) or between iterations (it does
 * not), and iter_cpu says whether the loop was working or waiting. Pass -1
 * for cpu where thread CPU time is unavailable. */
void GwedDiag_NoteIterationMs(double iter_ms, double iter_cpu_ms);

#endif /* GWED_DIAGNOSTICS_MOD_H */
