#!/usr/bin/env py -3
"""Proof harness for the P8 OAM-emitter X-clip patch (Beads beads-8wg.9.13.6).

The shipped patch is 13 bytes written over $00:A09F by
`src/gwed_ws_patch.c`.  Reviewing 13 bytes of 65816 by eye is exactly how a
carry bug ships, so this program proves the property mechanically, and it
proves it about *the bytes that are actually written* rather than about a
paraphrase of them:

  1. `--expect` : the vanilla bytes still sit at $00:A09F in the ROM (the
     guard the runtime applies at arm time, checked offline against the ROM
     file so a guard mismatch is diagnosed without launching the game).
  2. `--sim`    : a 13-byte 65816 simulator executes the replacement sequence
     for **all 65536** values of the 16-bit accumulator and compares, value by
     value, against the vanilla sequence.  Four properties must hold:
       (a) the accept set is exactly signed X in [-(32+extra), 256+extra-1];
       (b) every X the vanilla code accepted keeps an IDENTICAL carry, because
           that carry is what $00:A0DD..A0EE turns into the sprite's 9th OAM
           X bit -- a widened accept range with a changed carry silently
           breaks the right margin, which is the whole trap in P8;
       (c) the raw 9-bit OAM X the patched code produces (256*C | (A & 0xFF))
           decodes back to the same signed X under the engine's
           PpuDecodeOamX reading (raw >= 256 + extra  =>  raw - 512);
       (d) exactly 2*extra positions are newly accepted.
  3. `--reentry`: every byte offset in bank $00 is treated as a potential
     opcode and every relative branch / BRL / JMP / JSR whose target lands
     inside the replaced span is listed.  Deliberately over-approximating: it
     reports control transfers that a linear disassembly would not even
     decode, so "only the two branches being replaced" is a strong claim.

Usage (from PowerShell, native py -3):
    py -3 verify_oam_patch.py                        # all three, extra=43
    py -3 verify_oam_patch.py --extra 43 --bytes c92b019005c9b5ff9065c90001
    py -3 verify_oam_patch.py --extra 0              # inert-at-4:3 check

`--bytes` verifies an explicit hex sequence -- feed it the `emit=` hex the
game logs at arm time and this checks the shipped bytes end to end, with no
shared source of truth between the C emitter and this model.

Exit status 0 = every requested check passed, 1 = a check failed.
"""

from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# ── the site ────────────────────────────────────────────────────────────────
SITE_PC24 = 0x00A09F
SITE_LEN = 13
REJECT_PC = 0xA10E          # the emitter's "skip this sprite" continuation
ACCEPT_PC = 0xA0AC          # PHP: the accept path, and the byte after the span
NATIVE_SPRITE_BIAS = 32     # vanilla's -32 left bound = the 32px sprite width

VANILLA_HEX = "c9000190 08c9e0ff b0034c0e a1".replace(" ", "")

ROM_PATH = (r"F:\Projects\snesrecomp\GundamWingEndlessDuelSNESRecomp"
            r"\Shin Kidou Senki Gundam W - Endless Duel (J).smc")


def lorom_off(pc24: int) -> int:
    return ((pc24 >> 16) & 0x7F) * 0x8000 + ((pc24 & 0xFFFF) - 0x8000)


def emit_bytes(extra: int) -> bytes:
    """The replacement sequence, derived from ws_extra exactly as
    GwedWsPatch's emit_oam_x_clip() derives it.  Kept in this file as an
    independent model; use --bytes to check the C output instead."""
    right = 0x0100 + extra                       # first rejected positive X
    left = (-(NATIVE_SPRITE_BIAS + extra)) & 0xFFFF   # first accepted negative
    # BCC at $A0A7 is relative to the byte after it, i.e. $A0A9 = site + 10.
    disp_reject = (REJECT_PC - ((SITE_PC24 & 0xFFFF) + 10)) & 0xFF
    return bytes([
        0xC9, right & 0xFF, (right >> 8) & 0xFF,   # $A09F CMP #right
        0x90, 0x05,                                # $A0A2 BCC $A0A9
        0xC9, left & 0xFF, (left >> 8) & 0xFF,     # $A0A4 CMP #left
        0x90, disp_reject,                         # $A0A7 BCC $A10E (reject)
        0xC9, 0x00, 0x01,                          # $A0A9 CMP #$0100 (9th bit)
    ])


# ── a 13-byte 65816 simulator (M=0: 16-bit A, 16-bit immediates) ────────────
#
# Only the opcodes these two sequences use are implemented, and an unknown
# opcode raises rather than being silently skipped: a model that quietly
# ignores an instruction would "prove" the wrong program.

class Outcome:
    __slots__ = ("accepted", "carry")

    def __init__(self, accepted: bool, carry: int):
        self.accepted = accepted
        self.carry = carry

    def __eq__(self, o):
        return self.accepted == o.accepted and self.carry == o.carry

    def __repr__(self):
        return "accept(C=%d)" % self.carry if self.accepted else "reject"


def simulate(code: bytes, a: int, base: int = SITE_PC24 & 0xFFFF,
             max_steps: int = 64) -> Outcome:
    """Run `code` (loaded at `base`) with accumulator `a`.

    Terminates when control reaches ACCEPT_PC (fall-through or branch: the
    sprite is emitted, and the live carry becomes its 9th OAM X bit) or
    REJECT_PC (the sprite is skipped)."""
    pc = base
    carry = 0
    for _ in range(max_steps):
        if pc == ACCEPT_PC:
            return Outcome(True, carry)
        if pc == REJECT_PC:
            return Outcome(False, carry)
        if not (base <= pc < base + len(code)):
            raise AssertionError("control left the span at $%04X" % pc)
        op = code[pc - base]
        if op == 0xC9:                              # CMP #imm16
            imm = code[pc - base + 1] | (code[pc - base + 2] << 8)
            carry = 1 if a >= imm else 0
            pc += 3
        elif op in (0x90, 0xB0, 0x80):              # BCC / BCS / BRA
            disp = code[pc - base + 1]
            if disp >= 0x80:
                disp -= 0x100
            taken = (op == 0x80 or (op == 0x90 and carry == 0)
                     or (op == 0xB0 and carry == 1))
            pc = (pc + 2 + disp) if taken else pc + 2
        elif op == 0x4C:                            # JMP abs
            pc = code[pc - base + 1] | (code[pc - base + 2] << 8)
        elif op == 0x08:                            # PHP -- accept path marker
            return Outcome(True, carry)
        else:
            raise AssertionError("opcode $%02X at $%04X not modelled"
                                 % (op, pc))
    raise AssertionError("simulation did not terminate (loop?)")


def signed16(v: int) -> int:
    return v - 0x10000 if v >= 0x8000 else v


def decode_oam_x(raw: int, extra: int) -> int:
    """runner/src/snes/ppu.c PpuDecodeOamX, non-strict (no hints published)."""
    return raw - 512 if raw >= 256 + extra else raw


# ── checks ──────────────────────────────────────────────────────────────────

def check_expect(rom_path: str) -> bool:
    want = bytes.fromhex(VANILLA_HEX)
    try:
        with open(rom_path, "rb") as fh:
            fh.seek(lorom_off(SITE_PC24))
            got = fh.read(SITE_LEN)
    except OSError as exc:
        print("FAIL expect  cannot read ROM: %s" % exc)
        return False
    off = lorom_off(SITE_PC24)
    if got != want:
        print("FAIL expect  $%06X (file offset 0x%X): got %s want %s"
              % (SITE_PC24, off, got.hex(), want.hex()))
        return False
    print("PASS expect  $%06X (file offset 0x%X) = %s (%d bytes)"
          % (SITE_PC24, off, got.hex(), SITE_LEN))
    return True


def check_sim(extra: int, patched: bytes) -> bool:
    vanilla = bytes.fromhex(VANILLA_HEX)
    if len(patched) != len(vanilla):
        print("FAIL sim     patch is %d bytes, the site is %d -- a different "
              "length would shift every following instruction"
              % (len(patched), len(vanilla)))
        return False

    want_lo = -(NATIVE_SPRITE_BIAS + extra)
    want_hi = 256 + extra - 1
    ok = True
    accept, newly, carry_changed, decode_bad = [], [], [], []

    for a in range(0x10000):
        v = simulate(vanilla, a)
        p = simulate(patched, a)
        sx = signed16(a)
        if p.accepted:
            accept.append(sx)
            raw = (0x100 if p.carry else 0) | (a & 0xFF)
            if decode_oam_x(raw, extra) != sx:
                decode_bad.append((sx, raw, decode_oam_x(raw, extra)))
        if v.accepted:
            if not p.accepted:
                print("FAIL sim     signed X %d was accepted by vanilla and "
                      "is rejected by the patch" % sx)
                ok = False
                break
            if v.carry != p.carry:
                carry_changed.append((sx, v.carry, p.carry))
        elif p.accepted:
            newly.append(sx)

    if not ok:
        return False

    lo, hi = min(accept), max(accept)
    contiguous = len(accept) == hi - lo + 1
    if (lo, hi) != (want_lo, want_hi) or not contiguous:
        print("FAIL sim     accept set = [%d, %d] (%d values, contiguous=%s); "
              "wanted exactly [%d, %d]"
              % (lo, hi, len(accept), contiguous, want_lo, want_hi))
        ok = False
    if carry_changed:
        print("FAIL sim     %d vanilla-accepted X values changed carry (the "
              "9th OAM X bit), e.g. %r" % (len(carry_changed),
                                           carry_changed[:6]))
        ok = False
    if decode_bad:
        print("FAIL sim     %d accepted X values do not survive "
              "PpuDecodeOamX at extra=%d, e.g. %r"
              % (len(decode_bad), extra, decode_bad[:6]))
        ok = False
    if len(newly) != 2 * extra:
        print("FAIL sim     %d newly accepted positions, wanted 2*extra = %d"
              % (len(newly), 2 * extra))
        ok = False

    if ok:
        print("PASS sim     bytes %s at extra=%d: accept set exactly "
              "[%d, %d]; %d newly accepted (= 2*%d); every vanilla-accepted "
              "X keeps its carry; every accepted X round-trips through "
              "PpuDecodeOamX  (65536/65536 values checked)"
              % (patched.hex(), extra, lo, hi, len(newly), extra))
    return ok


# Opcode lengths for the over-approximating re-entry scan.  Only the control
# transfers matter; everything else is skipped one byte at a time on purpose.
def check_reentry(rom_path: str) -> bool:
    try:
        with open(rom_path, "rb") as fh:
            bank = fh.read(0x8000)
    except OSError as exc:
        print("FAIL reentry cannot read ROM: %s" % exc)
        return False

    span = range((SITE_PC24 & 0xFFFF), (SITE_PC24 & 0xFFFF) + SITE_LEN)
    # The two branches the patch replaces; anything else is a finding.
    known = {0xA0A2, 0xA0A7}
    hits = []

    for off in range(0x8000):
        pc = 0x8000 + off
        op = bank[off]
        tgt = None
        if op in (0x10, 0x30, 0x50, 0x70, 0x90, 0xB0, 0xD0, 0xF0, 0x80):
            if off + 1 >= 0x8000:
                continue
            d = bank[off + 1]
            tgt = (pc + 2 + (d - 0x100 if d >= 0x80 else d)) & 0xFFFF
        elif op in (0x82, 0x62):                       # BRL / PER
            if off + 2 >= 0x8000:
                continue
            d = bank[off + 1] | (bank[off + 2] << 8)
            tgt = (pc + 3 + signed16(d)) & 0xFFFF
        elif op in (0x4C, 0x20):                       # JMP abs / JSR abs
            if off + 2 >= 0x8000:
                continue
            tgt = bank[off + 1] | (bank[off + 2] << 8)
        elif op in (0x5C, 0x22):                       # JML / JSL long
            if off + 3 >= 0x8000:
                continue
            if bank[off + 3] != 0x00:                  # not into bank $00
                continue
            tgt = bank[off + 1] | (bank[off + 2] << 8)
        if tgt is not None and tgt in span and pc not in known:
            hits.append((pc, op, tgt))

    if hits:
        print("FAIL reentry %d control transfer(s) into $%04X..$%04X other "
              "than the two replaced branches: %r"
              % (len(hits), span[0], span[-1],
                 [("$%04X op=$%02X -> $%04X" % h) for h in hits[:8]]))
        return False
    print("PASS reentry no byte offset in bank $00 forms a branch/BRL/JMP/JSR "
          "into $%04X..$%04X except the two branches the patch replaces "
          "($A0A2, $A0A7) -- byte-exhaustive, not linear-disassembly scoped"
          % (span[0], span[-1]))
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", default=ROM_PATH)
    ap.add_argument("--extra", type=int, default=43,
                    help="widescreen margin per side (43 = 342-wide 16:9)")
    ap.add_argument("--bytes", dest="hexbytes",
                    help="verify this explicit hex sequence instead of the "
                         "formula (feed it the game's arm-time emit= log)")
    ap.add_argument("--expect", action="store_true")
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--reentry", action="store_true")
    a = ap.parse_args(argv)

    run_all = not (a.expect or a.sim or a.reentry)
    ok = True
    if run_all or a.expect:
        ok &= check_expect(a.rom)
    if run_all or a.sim:
        patched = (bytes.fromhex(a.hexbytes.replace(" ", ""))
                   if a.hexbytes else emit_bytes(a.extra))
        ok &= check_sim(a.extra, patched)
    if run_all or a.reentry:
        ok &= check_reentry(a.rom)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
