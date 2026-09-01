import socket, json, time, os, subprocess, sys

BUILD = r"F:\Projects\snesrecomp\GundamWingEndlessDuelSNESRecomp\build-agent"
EXE = os.path.join(BUILD, "GundamWingEndlessDuelSNESRecomp.exe")
ROM = r"F:\Projects\snesrecomp\GundamWingEndlessDuelSNESRecomp\Shin Kidou Senki Gundam W - Endless Duel (J).smc"

STATE = """format_version = 1

[[package]]
id = "gwed.localization"
version = "1.0.0"

[[feature]]
package_id = "gwed.localization"
id = "localization"
enabled = true

[feature.values]
language = "%s"
"""

def write_state(lang, build=BUILD):
    p = os.path.join(build, "mods", "preloaded", "state.toml")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    for _ in range(20):
        try:
            with open(p, "w", newline="\n") as f:
                f.write(STATE % lang)
            return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("state.toml contended")

class Conn:
    def __init__(self, port):
        d = time.time() + 25
        while time.time() < d:
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=5)
                s.settimeout(60)
                self.s = s
                self.f = s.makefile("rwb")
                return
            except OSError:
                time.sleep(0.3)
        raise RuntimeError("no connect %d" % port)
    def cmd(self, line):
        self.f.write((line + "\n").encode())
        self.f.flush()
        return self.f.readline().decode().strip()
    def j(self, line):
        return json.loads(self.cmd(line))
    def close(self):
        try: self.f.close(); self.s.close()
        except Exception: pass

def launch(port, lang, build=BUILD, visible=False):
    write_state(lang, build)
    env = dict(os.environ)
    env["SNESRECOMP_DEBUG_PORT"] = str(port)
    env["SDL_AUDIODRIVER"] = "dummy"
    if not visible:
        env["SDL_VIDEODRIVER"] = "dummy"
    p = subprocess.Popen([EXE, "--no-launcher", ROM], cwd=build, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p

def tap(c, btn, hold=0.14, after=0.45):
    c.cmd("set_controller " + btn)
    time.sleep(hold)
    c.cmd("clear_controller")
    time.sleep(after)
