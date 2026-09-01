#!/usr/bin/env python3
"""Summarize English/Spanish reference patches as a localization work map."""

from __future__ import annotations

import argparse
import sys
import tomllib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__.replace("\\", "/")).resolve().parent))

from decode_reference_tilemaps import SCRIPT_RANGES


def in_script_range(address: int) -> bool:
    """True if the byte lives in a decoded dialogue tilemap page."""
    return any(start <= address < end for start, end, _ in SCRIPT_RANGES)


REFERENCE_LANGS = ("en", "es")
NATIVE_LATIN_LANGS = ("fr", "it", "pt")


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


def load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def patch_len(patch: dict) -> int:
    return len(bytes.fromhex(patch.get("source_hex", patch.get("src_hex", ""))))


def lang_hex(patch: dict, lang: str) -> str | None:
    value = patch.get(f"{lang}_hex")
    return value.lower() if isinstance(value, str) else None


def category(patch: dict) -> str:
    en = lang_hex(patch, "en")
    es = lang_hex(patch, "es")
    has_native = any(lang_hex(patch, lang) is not None for lang in NATIVE_LATIN_LANGS)
    if has_native:
        return "native_latin_mapped"
    if en is not None and es is not None:
        return "reference_diff_unmapped" if en != es else "reference_shared_unmapped"
    if en is not None:
        return "english_only_unmapped"
    if es is not None:
        return "spanish_only_unmapped"
    return "other"


# Words 0x0800 (tile 0 on palette 2) and 0x0000 are the dialogue tilemaps'
# blank fill. A run made only of those words carries no text, so it is never
# translation work no matter how the two reference hacks happen to differ
# there. Every real glyph word is 0x04xx, so "all bytes in {0x00, 0x08}" is an
# exact test at either byte alignment.
BLANK_FILL_BYTES = frozenset((0x00, 0x08))


def is_blank_fill(*payloads: bytes | None) -> bool:
    present = [p for p in payloads if p]
    if not present:
        return False
    return all(set(p) <= BLANK_FILL_BYTES for p in present)


def byte_owner_categories(patches: list[dict]) -> dict[int, str]:
    """Per-byte category honouring FILE-ORDER supersession.

    rom_patches are applied in file order, so a byte's fate is decided by the
    LAST patch covering it. An imported reference fragment that a later
    aggregate patch overwrites is not outstanding work, however it is labelled
    in isolation - which is why the naive per-patch tally over-reported the
    remaining FR/IT/PT surface by 63%.
    """
    owner: dict[int, str] = {}
    for patch in patches:
        address = int(patch.get("address", patch.get("addr", 0)))
        size = patch_len(patch)
        cat = category(patch)
        if cat != "reference_diff_unmapped":
            for offset in range(size):
                owner[address + offset] = cat
            continue
        # Reference fragments are stitched together from raw IPS deltas, so one
        # patch routinely spans a text row AND the blank fill next to it.
        # Refine to byte granularity: a byte where en == es is not
        # reference-different at all, and a byte that is part of the tilemaps'
        # blank fill is never translation work.
        en_payload = bytes.fromhex(lang_hex(patch, "en"))
        es_payload = bytes.fromhex(lang_hex(patch, "es"))
        for offset in range(size):
            en_byte = en_payload[offset]
            es_byte = es_payload[offset]
            if en_byte == es_byte:
                owner[address + offset] = "reference_shared_unmapped"
            elif is_blank_fill(bytes((en_byte,)), bytes((es_byte,))):
                owner[address + offset] = "blank_fill"
            elif not in_script_range(address + offset):
                # Outside every decoded tilemap page: the two reference hacks
                # differ here in tile ART (font glyph cells each language
                # repurposes for its own punctuation/accents), not in text.
                owner[address + offset] = "reference_glyph_art_diff"
            else:
                owner[address + offset] = cat
    return owner


def merge_spans(items: list[tuple[int, int, str]], nearby_gap: int) -> list[dict]:
    spans: list[dict] = []
    for address, size, cat in sorted(items):
        end = address + size
        if spans and cat == spans[-1]["category"] and address <= spans[-1]["end"] + nearby_gap:
            spans[-1]["end"] = max(spans[-1]["end"], end)
            spans[-1]["patches"] += 1
            spans[-1]["bytes"] += size
            continue
        spans.append({
            "start": address,
            "end": end,
            "category": cat,
            "patches": 1,
            "bytes": size,
        })
    return spans


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--table",
        default=str(root / "translations" / "endless_duel.toml"),
        help="runtime localization table",
    )
    parser.add_argument(
        "--nearby-gap",
        type=int,
        default=16,
        help="merge same-category patches separated by at most this many bytes",
    )
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    table = load_toml(Path(args.table))
    rom_patches = table.get("rom_patch", [])
    naive: list[tuple[int, int, str]] = []
    counts: Counter[str] = Counter()
    bytes_by_cat: Counter[str] = Counter()
    for patch in rom_patches:
        address = int(patch.get("address", patch.get("addr", 0)))
        size = patch_len(patch)
        cat = category(patch)
        naive.append((address, size, cat))
        counts[cat] += 1
        bytes_by_cat[cat] += size

    owner = byte_owner_categories(rom_patches)
    effective: Counter[str] = Counter(owner.values())

    print("Endless Duel reference patch map")
    print(f"  table: {Path(args.table)}")
    print(f"  rom patches: {len(naive)}")
    print(f"  distinct patched bytes: {len(owner)}")
    print()
    print("Patch categories (per patch, ignoring supersession):")
    for cat in sorted(counts):
        print(f"  {cat}: {counts[cat]} patches, {bytes_by_cat[cat]} bytes")
    print()
    print("Effective coverage (per byte, LAST patch in file order wins):")
    for cat in sorted(effective):
        print(f"  {cat}: {effective[cat]} bytes")

    items = [(address, 1, cat) for address, cat in sorted(owner.items())]
    spans = merge_spans(items, args.nearby_gap)
    candidates = [span for span in spans if span["category"] == "reference_diff_unmapped"]
    candidates.sort(key=lambda item: (-item["bytes"], item["start"]))
    print()
    print(
        "Largest unmapped English/Spanish-different spans "
        "(best next FR/IT/PT worklist):"
    )
    for span in candidates[: args.limit]:
        print(
            "  "
            f"{span['start']:#08x}-{span['end'] - 1:#08x}: "
            f"{span['bytes']} bytes"
        )
    if not candidates:
        print("  (none - every reference-different byte is superseded by a "
              "patch with a native fr/it/pt payload)")

    print()
    print(
        "Interpretation: English/Spanish-different spans are likely text, "
        "language-specific tilemaps, or language-specific graphics. "
        "English/Spanish-shared spans are often hack code, common assets, "
        "or common layout changes and are lower-value translation targets."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
