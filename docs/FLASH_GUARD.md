# Flash reduction (photosensitivity) — Gundam Wing: Endless Duel

Opt-in filter that caps how far the presented picture may move between one
displayed frame and the next, so the game's full-screen strobes are dulled
instead of flashing. Requested by a player with photosensitivity, against the
end-of-fight finisher.

Presentation only. Nothing here touches the CPU, WRAM, VRAM, OAM, CGRAM, the
APU or a save state.

## Status

Implemented, unit-tested, and measured against a player capture of the
sequence that prompted it. **Not yet verified in a live process**: the ROM was
not available on the machine this was built on, so the filter has never run
against the real PPU output — only against the shipped code driven over that
capture. See [Known gaps](#known-gaps).

## What was measured

Source: a 1.93 s player capture of the end of a fight (`FlashingLights.mp4`,
1280x720 at 30 fps), cropped to the 4:3 picture and scaled to the native
256x224.

The backdrop alternates between two flat fills while sprites and the HUD keep
rendering normally:

| phase | mean RGB | Rec.601 luma |
|---|---|---|
| blue  | 31, 26, 140   | 55  |
| white | 208, 208, 205 | 208 |

* **Step between phases: 173/255 — 68% of the range**, across essentially the
  whole screen.
* **Rate: 15 Hz.** Each phase is held for two guest frames, which is why a
  30 fps capture shows a clean alternation rather than a constant value.
* **Duration: ~0.65 s**, i.e. about nineteen threshold-crossing flashes.

For context, WCAG 2.3.1 counts a flash as a pair of opposing changes of **10%**
of the range and calls content unsafe above three per second over more than 25%
of the area. The risk band runs ~3–60 Hz and peaks at 15–25 Hz. This sequence
is roughly five times the amplitude threshold, at the worst frequency, over the
whole screen.

**Frame blending does not help and cannot.** This title ships `FrameBlend = 1`,
and the capture was taken with it on. The blend averages each frame with the
one before it; each flash phase lasts two frames, so it mostly averages a frame
with its own twin and both plateaus survive. The 173/255 figure above is a
measurement of already-blended output.

## What the filter does

One number: the largest per-channel change, frame mean to frame mean, allowed
to reach the display in a single frame.

* Step within the limit — the frame is presented **exactly as drawn**,
  byte-identical, no arithmetic at all. In ordinary play that is every frame.
* Step over the limit — the frame is mixed with the previously *presented*
  frame at `alpha = limit/step`, landing the presented step on exactly the
  limit.

Because a strobe alternates, the remaining visible swing is about twice the
limit. Everything presented is a convex mix of two frames the game actually
drew, so the filter cannot invent a colour, clip, or leave the gamut.

**Cuts still cut.** A round starting or a stage loading is one large step that
does not come back; the filter releases after two limited frames so a cut costs
~33 ms of ramp rather than becoming a fade. A strobe *reverses*, and a reversal
over the limit latches a hold that disables release — so a strobe stays pinned
however long it runs and whatever its period. (A fixed release counter alone
would be out-waited by a slow enough strobe; `flash_guard_test.c` case 6
exercises exactly that at 7.5 Hz.)

The first flash of a sequence is limited before any of this is known, because
the limiter is always armed and detection only decides whether to stop
releasing. A guard that waited to confirm a flash would have to let the first
one through at full amplitude.

## Result on the measured sequence

Driving the shipped `recomp_flash_guard.c` over the capture, with each frame
repeated to reconstruct the 60 Hz sequence:

| strength | limit | peak presented step | flashes over the WCAG threshold |
|---|---|---|---|
| *(off)* | — | 173/255 (68%) | 19 |
| Mild | 20 | 21/255 (8%) | 0 |
| Standard | 12 | 13/255 (5%) | 0 |
| Maximum | 6 | 7/255 (3%) | 0 |

44 of 116 frames were mixed; the rest passed through untouched.

The cost during a flash is ghosting — the mix is mostly the previous frame, so
sprites smear for the half-second the strobe lasts. In this sequence the
fighters are in a hit-freeze pose and barely move. It reads as an afterimage.

## How a player turns it on

The launcher's **Mods** page, package `gwed.accessibility.flashguard` 1.0.0,
feature `flash_guard` (group *Accessibility*, `default_enabled = false`), with
a `strength` choice of Mild / Standard / Maximum.

* Manifest: `mods/preloaded/packages/gwed.accessibility.flashguard/1.0.0/manifest.toml`
* Activation glue: `src/flashguard_mod.c`, `src/flashguard_mod.h`
* Filter: `recomp-ui/src/recomp_flash_guard.h`, `recomp-ui/src/common/recomp_flash_guard.c`
* Present-path wiring: `src/main.c`, in the staged upload branch

There is **no `config.ini` key and no Display checkbox**, on the same ruling
widescreen operates under (Beads `beads-8wg.1.10`): the Mods page is the single
authority, so a player cannot end up with a config file saying one thing and
the Mods page another.

## Netplay

**Either player may run it alone — where the match's authority approves it.**

The manifest declares the feature `presentation_only = true`. That flag is only
the mod *asking*. On its own it grants nothing, and it must not, because a
manifest is written by whoever wrote the mod: a cheat that wants out of the mod
comparison writes that line too, and nothing in the client can tell the two
apart. Left self-served, "presentation-only" would be a way for any mod to
exempt itself from the checks that keep two players simulating the same game.

The grant comes from the **authority for the match**, never from the machine
that wants it:

| match | authority | where the list comes from |
|---|---|---|
| automatch | the server | the ruleset's `match_caps.mod_cosmetic_allow` |
| lobby | the host | the host's published `match_caps.mod_cosmetic_allow` |
| offline | nobody | nothing is compared, so it does not arise |

An entry is `id@version` or, preferably, `id@version#sha256`. **Pin the
digest.** A bare `id@version` is keyed on a string the client picks for itself,
so anyone wanting the exemption names their mod `gwed.accessibility.flashguard`
and takes it — that is a naming convention, not a whitelist. The digest is of
the package packed by the mod runtime's own deterministic zip (`zip_store_tree`
sorts its file list precisely so the bytes are identical on every machine), so
the entry names specific bytes: a "flash guard" with something else inside it
packs differently and is refused wherever it is installed.

Without a grant, the feature is treated as an ordinary simulation-affecting
mod — it appears in the compared set, it is published in the plan rows, an
adopt sweeps it off, and an automatch queue is refused naming it. That is the
behaviour that predates the flag, and it is what an older host, a ruleset with
no such key, and an unrecognised mod all get. **Absence never reads as
permission.**

### Why a malicious "cosmetic" mod cannot use this door

The allowlist decides *which* mods are exempt. Two structural properties decide
what a mod could do if it got through, and they are what actually close the
gap — they hold before any allowlist is consulted, so they hold even against a
server that approves something it should not have.

**1. A package cannot introduce code.** There is no `dlopen`, no `LoadLibrary`,
no script interpreter anywhere in the mod runtime. A manifest's `[[plugin]]`
entry *names* a plugin id, and activation runs only callbacks the executable
registered at compile time through `snes_mod_register_activation_plugin`. A
manifest naming an id the binary does not have resolves to nothing at all; the
validator already calls these "trusted plugins" for exactly this reason. So a
mod can only ever select among behaviour the shipped, reviewed binary already
implements.

**2. Only the executable may classify a plugin as presentation-only.**
`snes_mod_register_presentation_plugin` is the sole way to make that
classification, and it is a line of C in a build somebody reviewed — never
something a manifest can say about itself. A feature whose plugins were
registered the ordinary way is refused the exemption *however its manifest is
written*, and the refusal is logged as an attempt rather than passed over. A
manifest can therefore ask; it cannot describe new cosmetic behaviour into
existence.

**3. A cosmetic package may carry nothing but its manifest.** Since code is
impossible, the one remaining lever over the guest is *data* fed to a built-in
feature that patches — which is precisely how localization rewrites ROM text,
from a `.toml` inside its own package. So a package with any file beyond
`manifest.toml` is refused the exemption outright, checked by walking the
package rather than by reading what it declares (the localization table is not
a declared `[[resource]]`; counting declarations would have missed it).

Together these mean an "optional cosmetic mod" is not a payload at all. It is a
selection among audited, engine-side capabilities, carrying no bytes, vouched
for in compiled code. `mod_presentation_only_test.c` cases 7 and 8 are the
security cases: case 7 puts two features in the *same package*, under the *same
grant*, with *identical manifest claims*, differing only in which plugin the
binary vouched for — and the unvouched one is still compared.

### What this does and does not prevent

Worth being exact about, because the difference decides whether anyone is
misled.

It **does** stop a mod from exempting itself by declaring a flag, and it stops
someone *redistributing* something that claims to be cosmetic and is not — a
shared "cosmetic pack" with a wallhack in it does not match the pinned digest
and is refused on every machine that installs it.

It **does not** stop someone who patches their own executable. A patched client
reports whatever it likes about its own mods, and no client-side check can fix
that. What the allowlist removes is the *omission*: getting an unapproved mod
into a pool now takes patching and shipping a binary, rather than one line in a
manifest nobody checks.

It also does not, and cannot, stop a player from *seeing* state the game
already put in their RAM. In a deterministic rollback netcode both peers
simulate the whole game, so the opponent's "hidden" state is on the local
machine by construction — a debugger or a memory scanner reads it with no mod
system involved. The mod framework is not the boundary for that, and treating
it as one would be a mistake. Two things that actually matter there: ship only
builds with `SNESRECOMP_ENABLE_TRACE=OFF` (a trace build exposes a full TCP
debug server with `dump_ram`), and accept that information-revealing cheats in
a peer-to-peer deterministic sim are a detection problem, not a prevention
one.

### The server decides, not the client

The automatch ticket used to carry a **verdict** — one `mods_enabled` boolean —
so the server could not see what had been claimed, only whether the client had
decided it was acceptable. Every rule about which mods count as cosmetic ran on
the machine with the incentive to get it wrong, and could be changed by
changing that machine.

The ticket now carries the **evidence** as well:

```json
"mods_enabled": false,
"mod_exempt": ["gwed.accessibility.flashguard@1.0.0#<sha256>"]
```

one entry per exemption actually being relied on
(`snes_mod_runtime_exempted_packages_c`). The server holds the allowlist in the
ruleset, checks each claim against it itself, and refuses an unapproved one
with `mod_not_approved`, echoing the ruleset and the package. Accepted claims
are logged — what a player was running is the first question after any dispute,
and an accepted claim is the record that makes a later change visible.

Checked against **every** ruleset in the ticket, not just the first: a ticket
enters a bucket per key and may be paired under any of them, so approval by one
permissive ruleset must not carry a mod into a strict pool listed beside it.

The two gates are layers, not alternatives. An old server ignores `mod_exempt`
and the boolean works exactly as before; a new server stops needing to believe
it. A client older than the field claims nothing and is as constrained as it
was. Server side: `automatch::unapproved_exemption` in `recomp-net-server`,
with nine tests, and `docs/AUTOMATCH.md` §5.1.

**This still does not make the client's report true.** A patched binary can
omit a package from `mod_exempt` and set `mods_enabled: false`, and nothing
here catches that — that needs verification of the simulation itself, which the
protocol does not attempt. What changed is where the *rule* lives: shipping a
modified client no longer widens what a player may run, because the client no
longer holds the allowlist. The worst it can do is lie about named bytes, on
the record, instead of flipping an invisible boolean.

### Where the exemption is applied

One predicate, `feature_exempt()` in `mod_runtime.cpp` — manifest claim AND
session grant — used by all three things netplay looks at. Testing
`feature.presentation_only` directly anywhere else is the bug the predicate
exists to prevent.

* `effective_set` — the per-feature text peers compare byte-for-byte
* `plan_rows` — the per-package list a host publishes and a server matches a
  joiner's offer against. Missing this one meant a host advertised the filter
  as a mod joiners had to install
* `adopt_set`'s disable sweep — which would otherwise switch a granted filter
  off at the moment its owner joined a lobby, silently

### Automatch

The automatch ticket carries no package list — only game name, version, disc
fingerprint, ruleset id, and one `mods_enabled` boolean. Before that boolean is
computed, `cb_automatch_queue` applies **the server's** list from the chosen
ruleset (not this build's host list, which is irrelevant in someone else's
room), then refuses the queue by name if any enabled cosmetic claim is
ungranted:

```
<package>@<version>/<feature> is not on this queue's approved list
```

That check lives in the framework rather than in the game, because it is the
framework's rule and a title that forgot to implement it would be the hole.
Note that the game's own `GwedSimAffectingModSet` defaults to counting an
*unknown* package as simulation-affecting, so the two gates fail closed in the
same direction.

`docs/automatch_rulesets.gundam.toml` carries the key with the flash guard
listed and a placeholder digest — **replace it** with the one your shipped
package prints at netplay init:

```
netplay: cosmetic allowlist published when hosting: <id>@<ver>#<sha256>
```

Nothing else of this game's belongs on that list. Widescreen patches the cart
image for sprite bounds and localization patches ROM text; both are simulation
state however cosmetic they look.

## Tests

| test | what it covers |
|---|---|
| `recomp-ui/tests/flash_guard_test.c` | the filter: pass-through is byte-exact, cuts cut, 15 Hz and 7.5 Hz strobes both flatten, each strength honours its limit, stats report |
| `snesrecomp/tests/netplay/mod_presentation_only_test.c` | the runtime: the flag alone grants nothing, a grant for another package or with the wrong digest grants nothing, the right digest does, the exemption reaches all three consumers, revoking it restores the comparison, and an ordinary feature behaves as before throughout |

Both are ROM-free. The first is a CTest target (`recomp-ui-flash-guard`); the
second runs from `snesrecomp/tests/run_c_tests.sh`.

## Known gaps

* **Never run against the real game.** Everything above is measured on the
  shipped filter driven over a capture, plus unit tests on synthetic frames.
  The ROM is not on the build machine, so the present-path wiring in
  `src/main.c` is compiled but unexercised. What still needs a live run: that
  the guard engages on the real sequence (`events >= 1` in its stats), that
  ordinary play reports `limited == 0`, and that nothing about the staged
  upload path regressed when it is enabled with `FrameBlend = 0`.
* **The allowlist has not been exercised over a real wire.** The runtime side
  is unit-tested, and the build compiles and links the lobby changes, but no
  two processes have exchanged a `mod_cosmetic_allow` field. The lobby caps
  round-trip test (`lobby_mod_plan_test.c`) does not link on this machine —
  `lib/recomp-net` is not built here and three `rnet_*` symbols are missing,
  which was already true before this change.
* **The shipped ruleset digest is a placeholder** of all zeroes. Until it is
  replaced the queue refuses the filter — fail-safe, but it fails.
* **This is the only flash that has been measured.** The filter is
  content-agnostic and will catch others, but nobody has swept the game for
  them — super moves, stage intros, the attract reel, endings.
* **No reset on save-state load or rewind.** The guard self-releases within
  two frames, so the visible cost is ~33 ms of ramp; a `recomp_flash_guard_reset`
  on those paths would be tidier. `recomp_frame_blend` has the same gap.
* **Default is off.** The argument for defaulting it on is not frivolous: a
  player who needs this may well have put the game down before finding the Mods
  page. It is one line in the manifest, and it is the owner's call.
* **Gamma space, not linear light.** The means are taken on sRGB bytes rather
  than linearised. The comparison is between two means measured the same way so
  the filter is self-consistent, but the figures here are not the same quantity
  as WCAG's "relative luminance"; they are compared against its thresholds as a
  proportion of range.
