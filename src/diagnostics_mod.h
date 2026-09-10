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

#endif /* GWED_DIAGNOSTICS_MOD_H */
