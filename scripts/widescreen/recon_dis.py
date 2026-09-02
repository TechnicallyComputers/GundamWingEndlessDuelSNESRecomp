#!/usr/bin/env py -3
"""Small 65816 disassembler for the GWED widescreen recon (R6).

Ghidra is barred for SNES, so ROM evidence for the sprite-clip sites comes
from the recompiler's own canonical opcode table
(`snesrecomp/recompiler/snes65816.py decode_insn`) driven over the raw ROM.
This wrapper adds the two things a by-hand read needs and the decoder does
not do on its own:

  * LoROM address arithmetic (bank B offset X -> file offset
    (B & 0x7F) * 0x8000 + (X - 0x8000)) in both directions, and
  * an M/X (accumulator / index width) tracker so the immediate lengths and
    the "which compare width is in effect" question are answered rather than
    guessed: REP/SEP #$20/#$10 flip them, and every listed line carries the
    widths that were in effect when it was decoded.

Usage:
    py -3 recon_dis.py 00:85A0 64            # 64 instructions from $00:85A0
    py -3 recon_dis.py 00:85A0 64 --m 0 --x 1
    py -3 recon_dis.py --find 8d0421         # every occurrence of a byte seq
"""

from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "snesrecomp", "recompiler"))
sys.path.insert(0, _HERE)

import snes65816 as S  # noqa: E402

ROM_PATH = (r"F:\Projects\snesrecomp\GundamWingEndlessDuelSNESRecomp"
            r"\Shin Kidou Senki Gundam W - Endless Duel (J).smc")


def load_rom(path: str = ROM_PATH) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def to_off(bank: int, addr: int) -> int:
    """LoROM: bank B offset X -> file offset."""
    return (bank & 0x7F) * 0x8000 + (addr - 0x8000)


def to_pc(off: int) -> tuple:
    """file offset -> (bank, addr) in the $00-$7F mirror."""
    return (off // 0x8000, 0x8000 + (off % 0x8000))


def parse_pc(text: str) -> tuple:
    text = text.replace("$", "")
    if ":" in text:
        b, a = text.split(":")
        return int(b, 16) & 0x7F, int(a, 16)
    v = int(text, 16)
    return (v >> 16) & 0x7F, v & 0xFFFF


# The decoder's mode constants are module-level ints; render operands here so
# the listing reads like a disassembly rather than a tuple dump.
def fmt_operand(ins) -> str:
    m = ins.mode
    o = ins.operand
    if m == S.IMM:
        return "#$%02x" % o if ins.length == 2 else "#$%04x" % o
    if m == S.ABS:
        return "$%04x" % o
    if m == S.ABS_X:
        return "$%04x,X" % o
    if m == S.ABS_Y:
        return "$%04x,Y" % o
    if m == S.LONG:
        return "$%06x" % o
    if m == S.LONG_X:
        return "$%06x,X" % o
    if m == S.DP:
        return "$%02x" % o
    if m == S.DP_X:
        return "$%02x,X" % o
    if m == S.DP_Y:
        return "$%02x,Y" % o
    if m == S.INDIR:
        return "($%04x)" % o
    if m == S.INDIR_X:
        return "($%04x,X)" % o
    if m == S.DP_INDIR:
        return "($%02x)" % o
    if m == S.INDIR_Y:
        return "($%02x),Y" % o
    if m == S.INDIR_X if hasattr(S, "INDIR_X") else False:
        return "($%04x,X)" % o
    if m == S.INDIR_DPX:
        return "($%02x,X)" % o
    if m == S.INDIR_L:
        return "[$%02x]" % o
    if m == S.INDIR_LY:
        return "[$%02x],Y" % o
    if m == S.STK:
        return "$%02x,S" % o
    if m == S.STK_IY:
        return "($%02x,S),Y" % o
    if m in (S.REL, S.REL16):
        return "$%04x" % o
    return ""


def disasm(rom: bytes, bank: int, addr: int, count: int,
           m: int = 1, x: int = 1, stop_on_ret: bool = False) -> list:
    off = to_off(bank, addr)
    pc = addr
    out = []
    for _ in range(count):
        ins = S.decode_insn(rom, off, pc, bank, m, x)
        if ins is None:
            out.append(dict(pc24="$%02x:%04x" % (bank, pc), bytes="%02x"
                            % rom[off], text="???", m=m, x=x))
            off += 1
            pc += 1
            continue
        raw = rom[off:off + ins.length].hex()
        out.append(dict(pc24="$%02x:%04x" % (bank, pc), bytes=raw,
                        text="%-4s %s" % (ins.mnem, fmt_operand(ins)),
                        m=m, x=x, mnem=ins.mnem, operand=ins.operand,
                        length=ins.length, off=off))
        if ins.mnem == "REP":
            if ins.operand & 0x20:
                m = 0
            if ins.operand & 0x10:
                x = 0
        elif ins.mnem == "SEP":
            if ins.operand & 0x20:
                m = 1
            if ins.operand & 0x10:
                x = 1
        off += ins.length
        pc = (pc + ins.length) & 0xFFFF
        if stop_on_ret and ins.mnem in ("RTS", "RTL", "RTI"):
            break
    return out


def show(lines: list) -> None:
    for L in lines:
        print("%-11s %-14s m=%d x=%d  %s"
              % (L["pc24"], L["bytes"], L["m"], L["x"], L["text"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pc", nargs="?")
    ap.add_argument("count", nargs="?", type=int, default=48)
    ap.add_argument("--m", type=int, default=1)
    ap.add_argument("--x", type=int, default=1)
    ap.add_argument("--rom", default=ROM_PATH)
    ap.add_argument("--find", default=None, help="hex byte sequence")
    ap.add_argument("--stop-on-ret", action="store_true")
    a = ap.parse_args()
    rom = load_rom(a.rom)
    if a.find:
        pat = bytes.fromhex(a.find)
        i = rom.find(pat)
        n = 0
        while i >= 0:
            b, ad = to_pc(i)
            print("$%02x:%04x  (file 0x%06x)" % (b, ad, i))
            n += 1
            i = rom.find(pat, i + 1)
        print("%d hit(s)" % n)
        return 0
    if not a.pc:
        ap.error("need a pc or --find")
    b, ad = parse_pc(a.pc)
    show(disasm(rom, b, ad, a.count, a.m, a.x, a.stop_on_ret))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
