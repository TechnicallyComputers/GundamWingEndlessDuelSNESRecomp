"""Bank states from before a dialogue block is STAGED, not before it draws.

statehunt.py keys its rolling-state promotion on the VRAM tilemap, i.e. on the
text already being on screen. That is too late for battle_dialogue_3 and
ending_dialogue: those screens memcpy their whole script out of the cart into
WRAM ($7f:0100+ and $7e:6000+ respectively) when the scene loads, and the draw
routine then feeds off WRAM. A state banked after that copy carries the SAVING
process's language no matter how long the loading process waits -- see the
README section "Before the draw is not early enough for a STAGED surface".

So watch WRAM for the copy itself and promote the older rolling state when it
appears. Same ring-buffer discipline, earlier trigger.

    py -3 tools\\validation_states\\stagehunt.py <port> <seconds>

Banks pre_stage_<group>.state into this directory. Runs the same PAR-derived HP
freezes as statehunt.py so story mode clears unattended.
"""
import json, os, shutil, sys, time, tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tcp import launch, Conn, tap

ROOT = os.path.dirname(os.path.dirname(HERE))
# Groups whose screens stage their script into WRAM. Both were confirmed live.
STAGED_GROUPS = ("battle_dialogue_3", "ending_dialogue")
# How many of a group's rows must be present before we call it a staged block.
# One row could be a coincidence; five cannot.
MIN_ROWS = 5
STORY = [("start", 1.5), ("start", 1.5), ("start", 1.5),
         ("a", 1.5), ("a", 2.0), ("a", 2.0), ("a", 2.0), ("a", 2.0), ("a", 2.0)]


def needles():
    path = os.path.join(ROOT, "translations", "endless_duel_dialogue.toml")
    with open(path, "rb") as f:
        table = tomllib.load(f)
    out = {}
    for line in table["line"]:
        if line["group"] in STAGED_GROUPS:
            out.setdefault(line["group"], []).append(
                bytes.fromhex(line["en_hex"])[4:16])
    return out


def freeze(c):
    for write in ("1b70 ff70", "1b80 2c01", "1b74 0100", "1b84 0100",
                  "060c 99"):
        c.cmd("write_ram " + write)


def main(port, duration):
    marks = needles()
    proc = launch(port, "en")
    log = []
    c = None
    try:
        c = Conn(port)
        time.sleep(12)
        for button, wait in STORY:
            tap(c, button, 0.14, wait)
        start = time.time()
        done, roll, last_roll = set(), 0, 0.0
        rolling = [None, None]
        while time.time() - start < duration and len(done) < len(marks):
            wram = bytes.fromhex(json.loads(c.cmd("dump_ram 0 131072"))["hex"])
            for group, rows in marks.items():
                if group in done:
                    continue
                hits = sum(1 for n in rows if n in wram)
                if hits < MIN_ROWS:
                    continue
                # Promote the OLDER of the rolling pair: it predates the copy.
                older = rolling[1 - roll]
                kept = None
                if older and os.path.exists(older):
                    kept = os.path.join(HERE, "pre_stage_%s.state" % group)
                    shutil.copyfile(older, kept)
                    done.add(group)
                entry = {"t": round(time.time() - start, 1), "group": group,
                         "rows_staged": hits, "state": kept}
                log.append(entry)
                print(json.dumps(entry), flush=True)
                with open(os.path.join(HERE, "stagehunt_log.json"), "w") as f:
                    json.dump(log, f, indent=1)
            freeze(c)
            if time.time() - last_roll > 8.0:
                last_roll = time.time()
                path = os.path.join(HERE, "_stageroll%d.state" % roll)
                if '"ok":true' in c.cmd("save_state " + path.replace("\\", "/")):
                    rolling[roll] = path
                roll = 1 - roll
            tap(c, "a", 0.1, 0.05)
            time.sleep(0.2)
        print("banked " + ", ".join(sorted(done)), flush=True)
        return 0 if len(done) == len(marks) else 1
    finally:
        if c is not None:
            c.close()
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]), float(sys.argv[2])))
