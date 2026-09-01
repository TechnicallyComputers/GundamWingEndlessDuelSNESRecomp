#!/usr/bin/env python3
"""Offline replay of the runtime localization table into per-language ROM images.

This mirrors the engine semantics in ``snesrecomp/runner/src/snes_text_xlate.cpp``
so generators and decoders can reason about the patched cart image without the
reference IPS files (see docs/LOCALIZATION_PLAYBOOK.md section 2):

* ``[[rom_patch]]`` entries are applied ONCE, in FILE ORDER.
* A patch applies only when the current bytes equal ``source_hex`` OR any
  language's payload for that patch (so runtime language switching works).
* The payload is resolved through the ``fallback_<lang>`` chain rooted at the
  selected language; a patch with no payload anywhere in the chain writes
  ``source_hex`` back (the ROM restore path).

Guard-failure caveat: some aggregate patches were authored against the *English*
post-patch image, so their ``source_hex`` does not match the Spanish replay at
that point in file order. Those patches are skipped, exactly as the engine would
skip them at runtime. ``build_image`` reports the skip count so callers can
assert it does not change.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ALL_LANGS = ("en", "es", "fr", "it", "pt", "tl", "id", "zh", "ko", "th")


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


def default_rom_path() -> Path:
    return repo_root() / "Shin Kidou Senki Gundam W - Endless Duel (J).smc"


def default_table_path() -> Path:
    return repo_root() / "translations" / "endless_duel.toml"


def load_table(path: Path | str | None = None) -> dict:
    path = Path(path) if path is not None else default_table_path()
    with path.open("rb") as f:
        return tomllib.load(f)


def fallback_chain(table: dict, lang: str) -> list[str]:
    chain = [lang]
    current = lang
    while True:
        nxt = table.get(f"fallback_{current}")
        if not isinstance(nxt, str) or nxt in chain:
            break
        chain.append(nxt)
        current = nxt
    return chain


def build_image(
    lang: str = "en",
    table: dict | None = None,
    rom: bytes | None = None,
) -> tuple[bytes, int, int]:
    """Return ``(image, applied, skipped)`` for ``lang``."""
    if table is None:
        table = load_table()
    if rom is None:
        rom = default_rom_path().read_bytes()
    image = bytearray(rom)
    chain = fallback_chain(table, lang)
    applied = skipped = 0
    for patch in table.get("rom_patch", []):
        address = int(patch["address"])
        source = bytes.fromhex(patch["source_hex"])
        size = len(source)
        current = bytes(image[address:address + size])
        allowed = {source}
        for other in ALL_LANGS:
            value = patch.get(f"{other}_hex")
            if isinstance(value, str):
                allowed.add(bytes.fromhex(value))
        if current not in allowed:
            skipped += 1
            continue
        payload = None
        for candidate in chain:
            value = patch.get(f"{candidate}_hex")
            if isinstance(value, str):
                payload = bytes.fromhex(value)
                break
        if payload is None:
            payload = source
        image[address:address + size] = payload
        applied += 1
    return bytes(image), applied, skipped


def main() -> int:
    table = load_table()
    rom = default_rom_path().read_bytes()
    for lang in ("en", "es", "fr", "it", "pt", "tl", "id"):
        _, applied, skipped = build_image(lang, table, rom)
        print(f"{lang}: applied {applied}, guard-skipped {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
