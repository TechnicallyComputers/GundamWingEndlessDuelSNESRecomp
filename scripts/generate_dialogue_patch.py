#!/usr/bin/env python3
"""Generate runtime dialogue tilemap overlays from decoded source text."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__.replace("\\", "/")).resolve().parent))

from analyze_reference_ips import repo_root
from decode_reference_tilemaps import CHAR_BY_TILE


BEGIN_MARKER = "# BEGIN GENERATED DIALOGUE TILEMAP PATCHES"
END_MARKER = "# END GENERATED DIALOGUE TILEMAP PATCHES"
TARGET_LANGS = ("fr", "it", "pt")
TILE_BY_CHAR = {char: tile for tile, char in CHAR_BY_TILE.items()}
ROW_WORDS = 32
ROW_BYTES = ROW_WORDS * 2


def load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def word_bytes(word: int) -> bytes:
    return bytes((word & 0xFF, (word >> 8) & 0xFF))


def encode_line(source_hex: str, start_col: int, text: str) -> str:
    source = bytearray.fromhex(source_hex)
    if len(source) != ROW_BYTES * 2:
        raise ValueError(f"dialogue source row must be 0x80 bytes, got {len(source):#x}")
    if len(text) > ROW_WORDS - start_col:
        raise ValueError(
            f"{text!r} is {len(text)} chars, max is {ROW_WORDS - start_col}"
        )

    for col in range(start_col, ROW_WORDS):
        top_off = col * 2
        bottom_off = ROW_BYTES + col * 2
        rel = col - start_col
        if rel >= len(text):
            source[top_off:top_off + 2] = word_bytes(0x0800)
            source[bottom_off:bottom_off + 2] = word_bytes(0x0800)
            continue
        char = text[rel]
        if char not in TILE_BY_CHAR:
            raise ValueError(f"unsupported dialogue glyph {char!r} in {text!r}")
        tile = TILE_BY_CHAR[char]
        if char == " ":
            top_word = 0x0400 | tile
            bottom_word = top_word
        else:
            top_word = 0x0400 | tile
            bottom_word = 0x0400 | (tile + 0x10)
        source[top_off:top_off + 2] = word_bytes(top_word)
        source[bottom_off:bottom_off + 2] = word_bytes(bottom_word)
    return source.hex()


def load_targets(path: Path) -> dict[int, dict[str, str]]:
    if not path.is_file():
        return {}
    data = load_toml(path)
    targets: dict[int, dict[str, str]] = {}
    for index, entry in enumerate(data.get("line", []), 1):
        address = int(entry["address"])
        if address in targets:
            raise ValueError(f"target line {index}: duplicate address 0x{address:06x}")
        values = {
            lang: str(entry[lang])
            for lang in TARGET_LANGS
            if lang in entry and str(entry[lang])
        }
        if values:
            targets[address] = values
    return targets


def generated_blocks(dialogue: dict, targets: dict[int, dict[str, str]]) -> list[str]:
    blocks = [BEGIN_MARKER]
    count = 0
    for index, entry in enumerate(dialogue.get("line", []), 1):
        address = int(entry["address"])
        start_col = int(entry["start_col"])
        source_hex = entry["en_hex"]
        values = {}
        for lang in TARGET_LANGS:
            text = targets.get(address, {}).get(lang, entry.get(lang))
            if not text:
                continue
            values[lang] = encode_line(source_hex, start_col, text)
        if not values:
            continue
        count += 1
        blocks.extend([
            "",
            "[[rom_patch]]",
            f"address = 0x{address:06x}",
            f"# generated_from = \"translations/endless_duel_dialogue.toml line {index}\"",
            f'source_hex = "{source_hex}"',
        ])
        for lang, hex_value in values.items():
            blocks.append(f'{lang}_hex = "{hex_value}"')
    blocks.extend(["", END_MARKER, ""])
    if count == 0:
        blocks.insert(1, "# No native dialogue overlays are authored yet.")
    return blocks


def replace_generated_section(table_text: str, generated: str) -> str:
    pattern = rf"(?ms)^{re.escape(BEGIN_MARKER)}\n.*?^{re.escape(END_MARKER)}\n?"
    if re.search(pattern, table_text):
        return re.sub(pattern, generated, table_text)
    return table_text.rstrip() + "\n\n" + generated


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=str(root / "translations" / "endless_duel_dialogue.toml"),
    )
    parser.add_argument(
        "--table",
        default=str(root / "translations" / "endless_duel.toml"),
    )
    parser.add_argument(
        "--targets",
        default=str(root / "translations" / "endless_duel_dialogue_targets.toml"),
        help="authored target-language dialogue text table",
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    source_path = Path(args.source)
    table_path = Path(args.table)
    dialogue = load_toml(source_path)
    targets = load_targets(Path(args.targets))
    decoded_addresses = {int(entry["address"]) for entry in dialogue.get("line", [])}
    unknown_targets = sorted(set(targets) - decoded_addresses)
    if unknown_targets:
        formatted = ", ".join(f"0x{address:06x}" for address in unknown_targets)
        raise ValueError(f"target dialogue address not present in decoded source: {formatted}")
    generated = "\n".join(generated_blocks(dialogue, targets))
    table_text = table_path.read_text(encoding="utf-8")
    updated = replace_generated_section(table_text, generated)

    if args.write:
        table_path.write_text(updated, encoding="utf-8")
        print(f"updated {table_path}")
        return 0

    if updated != table_text:
        print("dialogue patch section is not up to date")
        return 1
    print("dialogue patch section is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
