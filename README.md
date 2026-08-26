# Gundam Wing Endless Duel Recomp

A native recompilation of **Gundam Wing Endless Duel** (JPN), built on
[snesrecomp](https://github.com/mstan/snesrecomp).

It is the year After Colony 195, and war between the Space Colonies and Earth has begun. To give the colonies an edge, they send 5 young soldiers, trained to perfection, to earth in the most powerful of Mobile Suits-Gundams. With their arrival, the tide of the war changes as they battle against the Earth forces and the Colonies of their origin.

> **You must legally own a copy of the game.** No ROM data is distributed with
> this project, in the repository or in any release. The recompiled C is
> generated locally from your own copy and is never committed.

## Status

Scaffolded on 2026-08-26 — **not yet a working port.** The layout, build,
regeneration pipeline, CI, and packaging are wired up; the game does not run
until the host work in `src/game_rtl.c` is done. See
[Porting from here](#porting-from-here).

## ROM identity

| | |
|---|---|
| File | `Shin Kidou Senki Gundam W - Endless Duel (Japan).sfc` |
| Publisher | Bandai |
| Year | 1996 |
| Mapping | lorom |
| Region | JPN (Japan) |
| Coprocessor | none |
| CRC32 | `c0aecdca` |
| SHA-256 | `dd94308d822636c6ddf73c5e2644c84f2eb8fb4d9201150fc5f37d44d6f423f1` |

`tools/regen.sh` refuses to run against anything else, so a mismatched
revision fails immediately instead of producing subtly wrong output.

## Build

```sh
git submodule update --init --recursive
bash tools/regen.sh --rom /path/to/Shin Kidou Senki Gundam W - Endless Duel (Japan).sfc
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
```

The ROM does not have to live in the repository — keeping it on your own
drive is the better habit, and `SNESRECOMP_ROM` sets the path once for a
shell. `tools/regen.sh` also finds `Shin Kidou Senki Gundam W - Endless Duel (Japan).sfc` at the repo root if you
prefer that; `.gitignore` blocks it from ever being committed either way.

`tools/regen.sh` verifies the ROM, generates `src/gen/*.c`, and re-syncs
`recomp/funcs.h`. Re-run it whenever you change anything under `recomp/`.

## Layout

| Path | What lives there |
|---|---|
| `recomp/` | Analysis input: `bank*.cfg`, `symbols.toml`, generated `funcs.h` |
| `src/` | Host code you own: `main.c`, `game_rtl.c`, `codegen_setup.c` |
| `src/gen/` | Generated C. Never committed — regenerate locally |
| `snesrecomp/` | Framework submodule (owns `lib/recomp-net`, `lib/retcomm-rbengine`) |
| `tools/` | `regen.sh` — the ROM → C pipeline |
| `scripts/` | `package_release.sh` — player-facing zip |
| `framework_pins.txt` | Exact framework commits this project was scaffolded against |

## Porting from here

The scaffold stops where the game-specific work starts. In rough order:

1. **Make it boot.** `src/game_rtl.c` holds the frame driver. The generic
   LLE-first shape is there; a real title usually needs a frame boundary and
   NMI delivery that understand its own main loop. This is the bulk of the
   work.
2. **Name things.** Add entries to `recomp/symbols.toml` as you identify
   routines, then re-run `tools/regen.sh`. Set `emit = true` to promote one
   into ahead-of-time codegen; leave it false to keep it interpreted.
3. **Resolve dispatch misses.** After every run, deal with unresolved
   indirect targets before anything else — they are the reason a port
   diverges, and they are cheap to fix early.
4. **Never synthesise a result** to get past uncovered code, and never edit
   `src/gen/` by hand. Fix the config or the framework and regenerate.

## Multiplayer

Two players, one controller per port.

Netplay is built with rollback available (`SNES_NET_MODE=rollback`); delay-sync
remains the default. See `snesrecomp/docs/ROLLBACK.md`.

## License

This project's own source is under the license in `LICENSE`. The framework
carries its own terms — see `snesrecomp/LICENSE` and
`snesrecomp/THIRD_PARTY_ATTRIBUTION.md`. Neither covers the game data, which
is not distributed here.
