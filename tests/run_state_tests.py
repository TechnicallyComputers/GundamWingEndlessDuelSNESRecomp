"""Exercise real-ROM file/memory and rollback replay in isolated processes."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("executable", type=Path)
parser.add_argument("rom", type=Path)
args = parser.parse_args()
exe, rom = args.executable.resolve(strict=True), args.rom.resolve(strict=True)
env = dict(os.environ, SDL_AUDIODRIVER="dummy", SDL_AUDIO_DRIVER="dummy",
           SDL_VIDEODRIVER="dummy", SDL_VIDEO_DRIVER="dummy")
options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
with tempfile.TemporaryDirectory(prefix="gwed-state-") as cwd:
    for extra in ([], ["--resume"]):
        result = subprocess.run([str(exe), str(rom), *extra], cwd=cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=180, **options)
        print(result.stdout, end="", flush=True)
        result.check_returncode()
        marker = ("new-process replay matches complete snapshot" if extra
                  else "All Endless Duel state checks passed.")
        if marker not in result.stdout:
            raise RuntimeError("State harness exited before completing its checks")
