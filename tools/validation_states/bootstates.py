"""Bank the two boot-path states: just before the title fade-in and just
before the attract crawl."""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tcp import launch, Conn
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    port = int(sys.argv[1])
    p = launch(port, "en")
    try:
        c = Conn(port)
        # Sweep: screenshot every second and bank a state each second, keeping
        # the ones the sweep identifies as "just before" each target.
        d = os.path.join(HERE, "boot"); os.makedirs(d, exist_ok=True)
        for i in range(46):
            f = os.path.join(d, "b%02d.state" % i)
            c.cmd("save_state " + f.replace("\\", "/"))
            b = os.path.join(d, "b%02d.bmp" % i)
            c.cmd("screenshot " + b.replace("\\", "/"))
            time.sleep(0.25)
            try:
                Image.open(b).save(b[:-4] + ".png"); os.remove(b)
            except Exception:
                pass
            time.sleep(0.8)
        print("done", flush=True)
    finally:
        try:
            c.close()
        except Exception:
            pass
        p.terminate()
