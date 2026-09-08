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

#endif /* GWED_DIAGNOSTICS_MOD_H */
