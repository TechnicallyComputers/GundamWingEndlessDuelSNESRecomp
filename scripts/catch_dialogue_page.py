#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Load a state, drive it, and POLL until a generated CJK page guard fires.

`validate_from_state.ps1 -Advance N` takes ONE capture at a guessed offset.
On a typewriter dialogue scene that is a coin flip: the box is blank between
pages, cleared during transitions, and the surface is not even enabled for part
of the sequence -- three of five swept -Advance values landed on an empty box.
Guessing the moment is the wrong instrument. Poll the thing you want to assert
on, and capture when it is true.

This walks the scene forward, dumps VRAM each step, and stops the moment one of
the generated page patches' guards matches the live BG3 map. Then it asserts the
page bytes at that patch's payload address are byte-identical to the payload the
generator emitted, and writes the usual capture set (vram/ppu/cgram + a
screenshot taken a beat later, once the line has finished typing).

    py -3 scripts/catch_dialogue_page.py <port> <lang> <state> <out-name> [secs]

Exit 0 only if a guard fired AND the page matched. Captures land in
analysis/state_validation/<out-name>/.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    root = Path(__file__.replace("\\", "/")).resolve().parents[1]
    sys.path.insert(0, str(root / "tools" / "validation_states"))
    sys.path.insert(0, str(root / "scripts"))
    import tcp
    from generate_dialogue_cjk_patch import Build

    port, lang, state, name = int(argv[0]), argv[1], argv[2], argv[3]
    seconds = float(argv[4]) if len(argv) > 4 else 70.0
    # "par" holds the published Pro Action Replay freezes (P1 invincible, P2 at
    # 1 HP, timer pinned) while polling, so a mid-fight state ends its round and
    # the victory-quote screen actually draws.  Without it a battle_quote poll
    # can walk the whole window without the surface ever appearing.
    par = "par" in argv[5:]

    build = Build(root)
    if lang not in build.quotes[0]:
        raise SystemExit(f"{lang} has no CJK dialogue pages")
    wanted = []
    for surface in build.surfaces:
        for quote in build.quotes[surface.index][lang]:
            address, _, payload = build.page_payload(quote)
            guard = build.guards[lang][quote.addresses[0]]
            wanted.append((surface, quote, address, bytes.fromhex(payload),
                           surface.guard_address,
                           b"".join(bytes((w & 0xFF, w >> 8)) for w in guard)))

    out = root / "analysis" / "state_validation" / name
    out.mkdir(parents=True, exist_ok=True)
    proc = tcp.launch(port, lang)
    conn = None
    try:
        conn = tcp.Conn(port)
        time.sleep(10)
        print(conn.cmd("load_state %s/tools/validation_states/%s.state"
                       % (str(root).replace("\\", "/"), state)))
        deadline = time.time() + seconds
        hit = None
        while time.time() < deadline and hit is None:
            if par:
                for write in ("1b70 ff70", "1b80 2c01", "1b74 0100",
                              "1b84 0100", "060c 99"):
                    conn.cmd("write_ram " + write)
            tcp.tap(conn, "a", 0.1, 0.25)
            vram = bytes.fromhex(conn.j("dump_vram 0 65536")["hex"])
            for surface, quote, address, payload, guard_at, guard in wanted:
                if vram[guard_at:guard_at + len(guard)] == guard:
                    hit = (surface, quote, address, payload,
                           vram[address:address + len(payload)], vram)
                    break
        if hit is None:
            print(f"no page guard fired within {seconds:.0f}s")
            return 1
        surface, quote, address, payload, live, vram = hit
        (out / "vram.json").write_text(
            json.dumps({"addr": "0x0", "len": len(vram), "hex": vram.hex()}))
        (out / "ppu.json").write_text(conn.cmd("get_ppu_state"))
        # The VRAM above is the assertion; give the typewriter a beat so the
        # human-readable companion shows a whole line rather than one glyph.
        time.sleep(2.5)
        (out / "cgram.json").write_text(conn.cmd("dump_cgram"))
        print(conn.cmd("screenshot " + str(out / "shot.bmp").replace("\\", "/")))
        en = " / ".join(build.by_address[a]["en"] for a in quote.addresses)
        ok = live == payload
        print(f"{'MATCH' if ok else 'MISMATCH'} {surface.name} {lang} quote "
              f"0x{quote.addresses[0]:06x} base 0x{quote.base:03x}, "
              f"{len(payload)} page bytes at 0x{address:04x} | {en}")
        if not ok:
            differing = sum(1 for a, b in zip(live, payload) if a != b)
            print(f"  {differing} of {len(payload)} page bytes differ")
        return 0 if ok else 1
    finally:
        if conn is not None:
            conn.close()
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
