#!/usr/bin/env python3
"""Generate or verify Endless Duel fixed-width option text hex."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path


# Latin languages only. ko/zh/th render these screens natively through the font
# slot injection in scripts/generate_option_cjk_patch.py, which rewrites whole
# records; these sub-span entries must stay inert for them.
LANGS = ("en", "es", "fr", "it", "pt", "tl", "id")
STALE_LANGS = ("zh", "ko", "th")


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


def load_source(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def ascii_hex(text: str, label: str, width: int | None = None) -> str:
    try:
        data = text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label}: fixed option text only supports ASCII") from exc
    if width is not None and len(data) != width:
        raise ValueError(f"{label}: expected {width} bytes, got {len(data)}")
    return data.hex()


GENERATED_SECTION_RE = re.compile(
    r"(?ms)^# BEGIN GENERATED .+?^# END GENERATED [^\r\n]*$")


def generated_spans(table_text: str) -> list[tuple[int, int]]:
    """Byte ranges owned by other generators' marker sections."""
    return [(m.start(), m.end()) for m in GENERATED_SECTION_RE.finditer(table_text)]


def parse_rom_patch_blocks(table_text: str) -> list[tuple[int, int, str]]:
    """Hand-maintained [[rom_patch]] blocks, bounded by the next section start.

    Blocks inside a generated marker section belong to the generator that owns
    that section (option records are also patched whole-span by
    generate_option_cjk_patch.py), so they are skipped here entirely.
    """
    owned = generated_spans(table_text)
    boundaries = [
        m.start()
        for m in re.finditer(
            r"(?m)^(?:\[\[[^\]]+\]\]|# BEGIN GENERATED .+|# END GENERATED .+)\s*$",
            table_text,
        )
    ]
    blocks: list[tuple[int, int, str]] = []
    for m in re.finditer(r"(?m)^\[\[rom_patch\]\]\s*$", table_text):
        start = m.start()
        if any(lo <= start < hi for lo, hi in owned):
            continue
        later = [b for b in boundaries if b > start]
        end = later[0] if later else len(table_text)
        blocks.append((start, end, table_text[start:end]))
    return blocks


def block_address(block: str) -> int | None:
    m = re.search(r"(?m)^address\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*$", block)
    if not m:
        return None
    return int(m.group(1), 0)


def index_blocks(table_text: str) -> dict[int, tuple[int, int, str]]:
    indexed: dict[int, tuple[int, int, str]] = {}
    for start, end, block in parse_rom_patch_blocks(table_text):
        address = block_address(block)
        if address is None:
            continue
        indexed[address] = (start, end, block)
    return indexed


def block_targets(block: str) -> dict[str, str]:
    values = {
        key: value.lower()
        for key, value in re.findall(r'(?m)^([a-z_]+)\s*=\s*"([0-9a-fA-F]*)"\s*$', block)
        if key == "source_hex" or key.endswith("_hex")
    }
    return values


def generate_entries(source: dict) -> dict[int, dict[str, str]]:
    generated: dict[int, dict[str, str]] = {}
    for index, entry in enumerate(source.get("text_patch", []), 1):
        address = int(entry["address"])
        if address in generated:
            raise ValueError(f"text_patch {index}: duplicate address {address:#x}")
        source_text = str(entry["source"])
        source_hex = ascii_hex(source_text, f"{address:#x} source")
        width = len(bytes.fromhex(source_hex))
        values = {"source_hex": source_hex}
        for lang in LANGS:
            if lang in entry:
                values[f"{lang}_hex"] = ascii_hex(str(entry[lang]), f"{address:#x} {lang}", width)
        generated[address] = values
    return generated


def strip_stale_langs(block: str) -> str:
    out = block
    for lang in STALE_LANGS:
        out = re.sub(rf'(?m)^{lang}_hex[ \t]*=[ \t]*"[0-9a-fA-F]*"[ \t]*\r?\n?', "", out)
    return out


def update_block(block: str, generated: dict[str, str]) -> str:
    out = strip_stale_langs(block)
    for key, value in generated.items():
        if key == "source_hex":
            continue
        pattern = re.compile(rf'(?m)^{re.escape(key)}[ \t]*=[ \t]*"[0-9a-fA-F]*"[ \t]*$')
        replacement = f'{key} = "{value}"'
        if pattern.search(out):
            out = pattern.sub(replacement, out, count=1)
        else:
            trailing = "\n\n" if out.endswith("\n\n") else "\n"
            out = out.rstrip() + "\n" + replacement + trailing
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=str(repo_root() / "translations" / "endless_duel_options.toml"),
        help="fixed option text source TOML",
    )
    parser.add_argument(
        "--table",
        default=str(repo_root() / "translations" / "endless_duel.toml"),
        help="runtime localization table to verify or update",
    )
    parser.add_argument("--write", action="store_true", help="rewrite generated language hex in the table")
    parser.add_argument("--check", action="store_true", help="fail if generated hex differs from the table")
    args = parser.parse_args()

    source = load_source(Path(args.source))
    generated = generate_entries(source)
    table_path = Path(args.table)
    table_text = table_path.read_text(encoding="utf-8")
    indexed = index_blocks(table_text)

    mismatches: list[str] = []
    replacements: list[tuple[int, int, str]] = []
    source_mismatches: list[str] = []
    for address, values in generated.items():
        if address not in indexed:
            mismatches.append(f"{address:#x}: missing rom_patch block")
            continue
        start, end, block = indexed[address]
        current = block_targets(block)
        for lang in STALE_LANGS:
            if f"{lang}_hex" in current:
                mismatches.append(
                    f"{address:#x}: stale {lang}_hex; ko/zh/th option text is "
                    "generated by scripts/generate_option_cjk_patch.py")
        for key, value in values.items():
            if current.get(key) != value:
                message = f"{address:#x}: {key} generated hex differs"
                mismatches.append(message)
                if key == "source_hex":
                    source_mismatches.append(message)
        updated = update_block(block, values)
        if updated != block:
            replacements.append((start, end, updated))

    if args.write and source_mismatches:
        for mismatch in source_mismatches:
            print(mismatch, file=sys.stderr)
        return 1

    if args.write and replacements:
        out = table_text
        for start, end, updated in sorted(replacements, reverse=True):
            out = out[:start] + updated + out[end:]
        table_path.write_text(out, encoding="utf-8", newline="\n")
        print(f"updated {table_path} for {len(replacements)} option text patch blocks")
        return 0

    if mismatches:
        for mismatch in mismatches:
            print(mismatch, file=sys.stderr)
        return 1

    print(f"option patches: {len(generated)} fixed-width entries match {table_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
