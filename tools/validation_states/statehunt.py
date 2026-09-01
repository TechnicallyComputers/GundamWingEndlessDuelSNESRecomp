"""Steamroll story mode with PAR-derived WRAM freezes and bank rolling
savestates so every dialogue surface we hit has a state from just before it.

PAR codes (GameFAQs, Darth_Nemesis) confirmed live on the Japanese ROM:
  7E1B70/71 P1 health  = ff 70      7E1B74/75 P2 health
  7E1B80/81 P1 energy  = 2c 01 (300) 7E1B84/85 P2 energy
  7E060C    infinite time = 99
Freezing them from the poll loop is exactly what a PAR does.
"""
import sys, os, time, json, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tcp import launch, Conn, tap
import sigs
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
STORY = [("start", 1.5), ("start", 1.5), ("start", 1.5),
         ("a", 1.5), ("a", 2.0), ("a", 2.0), ("a", 2.0), ("a", 2.0), ("a", 2.0)]


def snap(c, out, name, v=None):
    d = os.path.join(out, name)
    os.makedirs(d, exist_ok=True)
    if v is None:
        v = bytes.fromhex(json.loads(c.cmd("dump_vram 0 65536"))["hex"])
    open(os.path.join(d, "vram.bin"), "wb").write(v)
    for fn, cmd in (("ppu.json", "get_ppu_state"), ("cgram.json", "dump_cgram"),
                    ("oam.json", "dump_oam")):
        open(os.path.join(d, fn), "w").write(c.cmd(cmd))
    b = os.path.join(d, "shot.bmp")
    c.cmd("screenshot " + b.replace("\\", "/"))
    time.sleep(0.25)
    try:
        Image.open(b).save(b[:-4] + ".png"); os.remove(b)
    except Exception:
        pass
    return d


def freeze(c):
    c.cmd("write_ram 1b70 ff70")
    c.cmd("write_ram 1b80 2c01")
    c.cmd("write_ram 1b74 0100")
    c.cmd("write_ram 1b84 0100")
    c.cmd("write_ram 060c 99")


if __name__ == "__main__":
    port = int(sys.argv[1])
    out = os.path.join(HERE, sys.argv[2])
    dur = float(sys.argv[3])
    states = os.path.join(HERE, "states")
    os.makedirs(out, exist_ok=True)
    os.makedirs(states, exist_ok=True)
    p = launch(port, "en")
    log = []
    try:
        c = Conn(port)
        time.sleep(12)
        for b, w in STORY:
            tap(c, b, 0.14, w)
        t0 = time.time()
        seen, n, nshot = set(), 0, 0
        last_roll = last_shot = 0.0
        roll = 0
        roll_ok = [None, None]     # most recently completed rolling state files
        while time.time() - t0 < dur:
            v = bytes.fromhex(json.loads(c.cmd("dump_vram 0 65536"))["hex"])
            r = sigs.relaxed_scan(v)
            if r:
                key = tuple(sorted(set(i for i, _ in r)))
                if key not in seen:
                    seen.add(key)
                    groups = sorted(set(sigs.LINES[i]["group"] for i in key))
                    d = snap(c, out, "dlg%03d" % n, v)
                    # promote the older rolling state (further before the hit)
                    kept = None
                    other = roll_ok[1 - roll]
                    if other and os.path.exists(other):
                        kept = os.path.join(
                            states, "pre_%s_%03d.state" % (groups[0], n))
                        shutil.copyfile(other, kept)
                    e = {"t": round(time.time() - t0, 1), "dir": d,
                         "groups": groups, "state": kept,
                         "lines": [sigs.LINES[i]["en"] for i in key]}
                    log.append(e)
                    print(json.dumps(e)[:400], flush=True)
                    json.dump(log, open(os.path.join(out, "log.json"), "w"), indent=1)
                    n += 1
            freeze(c)
            if time.time() - last_roll > 8.0:
                last_roll = time.time()
                f = os.path.join(states, "_roll%d.state" % roll)
                res = c.cmd("save_state " + f.replace("\\", "/"))
                if '"ok":true' in res:
                    roll_ok[roll] = f
                roll = 1 - roll
            if time.time() - last_shot > 3.0:
                last_shot = time.time()
                b = os.path.join(out, "t%03d.bmp" % nshot)
                c.cmd("screenshot " + b.replace("\\", "/"))
                time.sleep(0.2)
                try:
                    Image.open(b).save(b[:-4] + ".png"); os.remove(b)
                except Exception:
                    pass
                nshot += 1
            tap(c, "a", 0.1, 0.05)
            time.sleep(0.25)
        json.dump(log, open(os.path.join(out, "log.json"), "w"), indent=1)
        print("hits", n, flush=True)
    finally:
        try:
            c.close()
        except Exception:
            pass
        p.terminate()
