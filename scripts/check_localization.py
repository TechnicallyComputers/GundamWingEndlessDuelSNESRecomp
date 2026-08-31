#!/usr/bin/env python3
"""Run the source-backed localization validation checks."""

from __future__ import annotations

import argparse
import os
import py_compile
import subprocess
import sys
import tomllib
from pathlib import Path


LANGS = ("en", "es", "fr", "it", "pt")
PATCH_KINDS = ("rom_patch", "ram_patch", "vram_patch", "glyph_label", "entry")
SCRIPT_CHECKS = (
    "scripts/check_localization.py",
    "scripts/generate_crawl_patch.py",
    "scripts/generate_cjk_crawl_patch.py",
    "scripts/generate_title_glyphs.py",
    "scripts/generate_option_patch.py",
    "scripts/generate_dialogue_patch.py",
    "scripts/audit_localization_coverage.py",
    "scripts/render_dialogue_previews.py",
    "scripts/render_title_menu_overlay_preview.py",
    "scripts/render_vram_bg_capture.py",
    "scripts/render_oam_capture.py",
)


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


def run(root: Path, args: list[str]) -> None:
    print("+ " + " ".join(args), flush=True)
    subprocess.run(args, cwd=root, check=True)


def load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def validate_patch_widths(root: Path) -> None:
    table = load_toml(root / "translations" / "endless_duel.toml")
    errors: list[str] = []
    for kind in PATCH_KINDS:
        for patch in table.get(kind, []):
            source = patch.get("source_hex") or patch.get("src_hex")
            if not isinstance(source, str):
                continue
            width = len(bytes.fromhex(source))
            address = int(patch.get("address", patch.get("addr", 0)))
            for lang in LANGS:
                key = f"{lang}_hex"
                if key in patch and len(bytes.fromhex(str(patch[key]))) != width:
                    errors.append(f"{kind} 0x{address:06x} {key} width mismatch")
    if errors:
        raise ValueError("\n".join(errors))
    print("hex width ok")


def compile_scripts(root: Path) -> None:
    cache_root = root / "build-local-xlate-trace" / "pycache-check"
    cache_root.mkdir(parents=True, exist_ok=True)
    sys.pycache_prefix = str(cache_root)
    for script in SCRIPT_CHECKS:
        py_compile.compile(str(root / script), doraise=True)
    print(f"py_compile ok: {len(SCRIPT_CHECKS)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-previews",
        action="store_true",
        help="do not regenerate ignored dialogue preview SVGs",
    )
    parser.add_argument(
        "--skip-py-compile",
        action="store_true",
        help="skip Python bytecode compilation checks",
    )
    args = parser.parse_args()

    root = repo_root()
    env_prefix = root / "build-local-xlate-trace" / "pycache-check"
    os.environ.setdefault("PYTHONPYCACHEPREFIX", str(env_prefix))

    run(root, [sys.executable, "scripts/generate_crawl_patch.py", "--check"])
    run(root, [sys.executable, "scripts/generate_cjk_crawl_patch.py", "--check"])
    run(root, [sys.executable, "scripts/generate_title_glyphs.py", "--check"])
    run(root, [sys.executable, "scripts/generate_option_patch.py", "--check"])
    run(root, [sys.executable, "scripts/generate_dialogue_patch.py"])
    validate_patch_widths(root)
    run(root, [sys.executable, "scripts/audit_localization_coverage.py"])
    if not args.skip_previews:
        run(root, [sys.executable, "scripts/render_dialogue_previews.py"])
    if not args.skip_py_compile:
        compile_scripts(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
