#!/usr/bin/env python3
"""Summarize English/Spanish reference patches as a localization work map."""

from __future__ import annotations

import argparse
import tomllib
from collections import Counter
from pathlib import Path


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
    patches = []
    counts: Counter[str] = Counter()
    bytes_by_cat: Counter[str] = Counter()
    for patch in table.get("rom_patch", []):
        address = int(patch.get("address", patch.get("addr", 0)))
        size = patch_len(patch)
        cat = category(patch)
        patches.append((address, size, cat))
        counts[cat] += 1
        bytes_by_cat[cat] += size

    print("Endless Duel reference patch map")
    print(f"  table: {Path(args.table)}")
    print(f"  rom patches: {len(patches)}")
    print()
    print("Patch categories:")
    for cat in sorted(counts):
        print(f"  {cat}: {counts[cat]} patches, {bytes_by_cat[cat]} bytes")

    spans = merge_spans(patches, args.nearby_gap)
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
            f"{span['bytes']} bytes, {span['patches']} patches"
        )

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
