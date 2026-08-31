#!/usr/bin/env python3
"""Generate or verify Endless Duel native Latin crawl tilemap hex."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


def load_source(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def validate_hex_word(value: str, label: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{4}", value):
        raise ValueError(f"{label} must be a 4-digit hex tile entry, got {value!r}")
    return value.lower()


def generate_language_hex(source: dict, lang: str) -> str:
    row_width = int(source["row_width"])
    rows = int(source["rows"])
    lang_data = source["languages"][lang]
    slots = [dict(slot) for slot in source["line_slot"]]
    lines = lang_data["lines"]
    for override in lang_data.get("line_slot", []):
        line_index = int(override["line"]) - 1
        if line_index < 0 or line_index >= len(slots):
            raise ValueError(f"{lang}: line slot override {line_index + 1} is outside the slot table")
        slots[line_index] = {
            "top": override["top"],
            "bottom": override["bottom"],
        }
    if len(lines) != len(slots):
        raise ValueError(f"{lang}: expected {len(slots)} lines, got {len(lines)}")

    glyphs = {
        key: (
            validate_hex_word(value["top"], f"glyph {key!r} top"),
            validate_hex_word(value["bottom"], f"glyph {key!r} bottom"),
        )
        for key, value in source["glyph"].items()
    }

    space = glyphs[" "][0]
    grid = [[space for _ in range(row_width)] for _ in range(rows)]

    for line_number, (line, slot) in enumerate(zip(lines, slots), 1):
        top_row, top_col = [int(x) for x in slot["top"]]
        bottom_row, bottom_col = [int(x) for x in slot["bottom"]]
        for row, col, name in ((top_row, top_col, "top"), (bottom_row, bottom_col, "bottom")):
            if row < 0 or row >= rows:
                raise ValueError(f"{lang} line {line_number}: {name} row {row} outside 0..{rows - 1}")
            if col < 0 or col + len(line) > row_width:
                raise ValueError(
                    f"{lang} line {line_number}: {name} column {col} plus "
                    f"{len(line)} chars exceeds row width {row_width}"
                )
        for i, char in enumerate(line):
            if char not in glyphs:
                raise ValueError(f"{lang} line {line_number}: unsupported glyph {char!r}")
            top, bottom = glyphs[char]
            grid[top_row][top_col + i] = top
            grid[bottom_row][bottom_col + i] = bottom

    for raw in lang_data.get("raw_tile", []):
        row = int(raw["row"])
        col = int(raw["col"])
        values = [validate_hex_word(value, f"{lang} raw tile") for value in raw["hex"]]
        if row < 0 or row >= rows:
            raise ValueError(f"{lang} raw tile row {row} outside 0..{rows - 1}")
        if col < 0 or col + len(values) > row_width:
            raise ValueError(
                f"{lang} raw tile column {col} plus {len(values)} entries "
                f"exceeds row width {row_width}"
            )
        for i, value in enumerate(values):
            grid[row][col + i] = value

    return "".join(entry for row in grid for entry in row)


def parse_rom_patch_blocks(table_text: str) -> list[tuple[int, int, str]]:
    starts = [m.start() for m in re.finditer(r"(?m)^\[\[rom_patch\]\]\s*$", table_text)]
    blocks: list[tuple[int, int, str]] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(table_text)
        blocks.append((start, end, table_text[start:end]))
    return blocks


def find_crawl_block(table_text: str, address: int) -> tuple[int, int, str]:
    matches: list[tuple[int, int, str]] = []
    address_re = re.compile(r"(?m)^address\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*$")
    for start, end, block in parse_rom_patch_blocks(table_text):
        m = address_re.search(block)
        if not m:
            continue
        if int(m.group(1), 0) == address and "fr_hex" in block and "it_hex" in block and "pt_hex" in block:
            matches.append((start, end, block))
    if len(matches) != 1:
        raise ValueError(f"expected one generated crawl rom_patch at {address:#x}, found {len(matches)}")
    return matches[0]


def table_targets(block: str) -> dict[str, str]:
    return {
        key[:-4]: value.lower()
        for key, value in re.findall(r'(?m)^([a-z]{2}_hex)\s*=\s*"([0-9a-fA-F]*)"\s*$', block)
    }


def update_block(block: str, generated: dict[str, str]) -> str:
    out = block
    for lang, hex_value in generated.items():
        pattern = re.compile(rf'(?m)^{re.escape(lang)}_hex\s*=\s*"[0-9a-fA-F]*"\s*$')
        replacement = f'{lang}_hex = "{hex_value}"'
        if pattern.search(out):
            out = pattern.sub(replacement, out, count=1)
        else:
            out = out.rstrip() + "\n" + replacement + "\n"
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=str(repo_root() / "translations" / "endless_duel_crawl.toml"),
        help="crawl text source TOML",
    )
    parser.add_argument(
        "--table",
        default=str(repo_root() / "translations" / "endless_duel.toml"),
        help="runtime localization table to verify or update",
    )
    parser.add_argument("--write", action="store_true", help="rewrite generated language hex in the table")
    parser.add_argument("--check", action="store_true", help="fail if generated hex differs from the table")
    args = parser.parse_args()

    source_path = Path(args.source)
    table_path = Path(args.table)
    source = load_source(source_path)
    address = int(source["patch_address"])
    generated = {
        lang: generate_language_hex(source, lang)
        for lang in sorted(source["languages"].keys())
    }

    table_text = table_path.read_text(encoding="utf-8")
    start, end, block = find_crawl_block(table_text, address)
    current = table_targets(block)
    mismatches = [lang for lang, hex_value in generated.items() if current.get(lang) != hex_value]

    if args.write and mismatches:
        table_path.write_text(table_text[:start] + update_block(block, generated) + table_text[end:], encoding="utf-8")
        print(f"updated {table_path} for: {', '.join(mismatches)}")
        return 0

    if mismatches:
        for lang in mismatches:
            print(f"{lang}: generated hex differs from {table_path}", file=sys.stderr)
        return 1

    print(
        f"crawl patch {address:#x}: {len(generated)} languages match "
        f"{len(next(iter(generated.values()))) // 2} generated bytes each"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
