#!/usr/bin/env python3
"""Load a state, drive it, and POLL until a Latin dialogue row is live.

`catch_dialogue_page.py` is the ko/zh instrument: it keys on the generated CJK
page guards in VRAM. The native Latin languages (fr/it/pt/tl/id) do not ship
glyph pages at all - their dialogue is a rewritten BG tilemap row shipped as a
[[rom_patch]] over `endless_duel_dialogue.toml`'s row - so the thing to assert
on is that ROW's tilemap words, and the place to find them depends on the
surface:

* `battle_dialogue_*` (the victory/defeat quotes) draw straight out of the cart,
  so the row's words show up in the live BG3 tilemap in VRAM.
* `battle_dialogue_3` and `ending_dialogue` STAGE their whole script into WRAM
  when the scene loads and draw from there
  (docs/LOCALIZATION_PLAYBOOK.md 6, tools/validation_states/README.md), so the
  row's words show up in WRAM at the staging block.

This polls BOTH: every step it dumps VRAM and WRAM and searches for a needle
taken from the generated payload of any row of the requested language. It stops
on the first hit, reports which row matched and where, and writes the usual
capture set. Because the needle IS the generated payload, a hit is the
assertion - there is nothing to compare afterwards.

    py -3 scripts/catch_latin_dialogue_page.py <port> <lang> <state> <out-name>
                                               [seconds] [needle_bytes]

Exit 0 only if a row of <lang> was found live.
"""
from __future__ import annotations

import json
import re
import sys
import time
import tomllib
from pathlib import Path


def load(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def generated_rows(table_path: Path, lang: str) -> dict[int, bytes]:
    """address -> generated payload, for the dialogue tilemap section only."""
    text = table_path.read_text(encoding="utf-8")
    begin = text.index("# BEGIN GENERATED DIALOGUE TILEMAP PATCHES")
    end = text.index("# END GENERATED DIALOGUE TILEMAP PATCHES")
    rows: dict[int, bytes] = {}
    for block in text[begin:end].split("[[rom_patch]]")[1:]:
        address = int(re.search(r"address = (0x[0-9a-f]+)", block).group(1), 16)
        m = re.search(rf'(?m)^{lang}_hex = "([0-9a-f]+)"', block)
        if m:
            rows[address] = bytes.fromhex(m.group(1))
    return rows


def main(argv: list[str]) -> int:
    root = Path(__file__.replace("\\", "/")).resolve().parents[1]
    sys.path.insert(0, str(root / "tools" / "validation_states"))
    import tcp

    port, lang, state, name = int(argv[0]), argv[1], argv[2], argv[3]
    seconds = float(argv[4]) if len(argv) > 4 else 70.0
    needle_len = int(argv[5]) if len(argv) > 5 else 24
    # Optional "lo,hi" ROM-address window, to pin the poll to ONE dialogue
    # group. Without it the first row of ANY group wins, and the scenes run
    # into each other: a pre_stage_battle_dialogue_3 run polled long enough
    # matches an ending_dialogue row instead.
    window = (0, 1 << 30)
    if len(argv) > 6:
        lo, hi = argv[6].split(",")
        window = (int(lo, 16), int(hi, 16))
    # 8th arg "par": hold the PAR HP freezes while polling, so a mid-fight
    # state (pre_quote) actually ends its round into the victory-quote box.
    par = len(argv) > 7 and argv[7] == "par"

    rows = generated_rows(root / "translations" / "endless_duel.toml", lang)
    if not rows:
        raise SystemExit(f"{lang} has no generated dialogue rows")
    source = load(root / "translations" / "endless_duel_dialogue.toml")
    en_by_address = {int(e["address"]): e.get("en", "") for e in source["line"]}
    targets = load(root / "translations" /
                   "endless_duel_dialogue_targets.toml")
    text_by_address = {int(e["address"]): e.get(lang, "")
                       for e in targets["line"]}

    # A needle must be UNIQUE to its row, and the row's leading columns are
    # blank fill in every row, so start the needle at the first text word.
    needles: list[tuple[int, bytes]] = []
    for address, payload in rows.items():
        if not window[0] <= address <= window[1]:
            continue
        body = payload.lstrip(b"\x00\x08")
        start = len(payload) - len(body)
        needle = payload[start:start + needle_len]
        if len(needle) == needle_len:
            needles.append((address, needle))

    out = root / "analysis" / "state_validation" / name
    out.mkdir(parents=True, exist_ok=True)
    proc = tcp.launch(port, lang)
    conn = None
    try:
        conn = tcp.Conn(port)
        time.sleep(10)
        print(conn.cmd("xlate_stats"))
        print(conn.cmd("load_state %s/tools/validation_states/%s.state"
                       % (str(root).replace("\\", "/"), state)))
        deadline = time.time() + seconds
        hit = None
        vram = wram = b""
        while time.time() < deadline and hit is None:
            if par:
                # PAR HP/energy/timer freezes (tools/validation_states/README).
                # A mid-fight state never reaches the victory quote on its own;
                # re-applying the freezes every tick ends the round instantly.
                for write in ("1b70 ff70", "1b80 2c01", "1b74 0100",
                              "1b84 0100", "060c 99"):
                    conn.cmd("write_ram " + write)
            tcp.tap(conn, "a", 0.1, 0.25)
            vram = bytes.fromhex(conn.j("dump_vram 0 65536")["hex"])
            wram = bytes.fromhex(conn.j("dump_ram 0 131072")["hex"])
            for where, blob in (("VRAM", vram), ("WRAM", wram)):
                for address, needle in needles:
                    at = blob.find(needle)
                    if at >= 0:
                        hit = (where, address, at)
                        break
                if hit:
                    break
        if hit is None:
            print(f"no {lang} dialogue row appeared within {seconds:.0f}s")
            return 1
        where, address, at = hit
        (out / "vram.json").write_text(
            json.dumps({"addr": "0x0", "len": len(vram), "hex": vram.hex()}))
        (out / "ppu.json").write_text(conn.cmd("get_ppu_state"))
        time.sleep(2.5)
        (out / "cgram.json").write_text(conn.cmd("dump_cgram"))
        # The WRAM/VRAM hit above IS the assertion. The screenshots are the
        # human-readable companion, and the box is often still typing (or not
        # yet drawn) at the instant the staged bytes appear - so take a short
        # burst rather than one guessed frame.
        print(conn.cmd("screenshot " + str(out / "shot.bmp").replace("\\", "/")))
        for i in range(1, 5):
            time.sleep(2.0)
            print(conn.cmd("screenshot "
                           + str(out / f"shot_{i}.bmp").replace("\\", "/")))
            tcp.tap(conn, "a", 0.1, 0.4)
        print(f"MATCH {lang} dialogue row 0x{address:06x} live in {where} at "
              f"0x{at:05x}: {text_by_address.get(address, '')!r} "
              f"(en: {en_by_address.get(address, '')!r})")
        return 0
    finally:
        if conn is not None:
            conn.close()
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
