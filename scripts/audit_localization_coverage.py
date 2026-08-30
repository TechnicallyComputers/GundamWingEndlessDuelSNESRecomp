#!/usr/bin/env python3
"""Report Endless Duel runtime-localization coverage."""

from __future__ import annotations

import argparse
import sys
import tomllib
from collections import Counter
from pathlib import Path


LANGS = ("en", "es", "fr", "it", "pt", "ko", "zh")
PATCH_KINDS = ("rom_patch", "ram_patch", "vram_patch", "glyph_label", "entry")


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


def load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def patch_sections(table: dict) -> list[tuple[str, dict]]:
    patches: list[tuple[str, dict]] = []
    for kind in PATCH_KINDS:
        for patch in table.get(kind, []):
            patches.append((kind, patch))
    return patches


def patch_has_lang(patch: dict, lang: str) -> bool:
    return f"{lang}_hex" in patch or lang in patch


def patch_width(patch: dict) -> int:
    source = patch.get("source_hex") or patch.get("src_hex")
    if not isinstance(source, str):
        return 0
    return len(bytes.fromhex(source))


def text_patch_langs(source: dict) -> Counter[str]:
    counts: Counter[str] = Counter()
    for patch in source.get("text_patch", []):
        if "source" in patch:
            counts["en"] += 1
        for lang in LANGS:
            if lang in patch:
                counts[lang] += 1
    return counts


def crawl_langs(source: dict) -> dict[str, int]:
    texts = source.get("languages", {})
    result: dict[str, int] = {}
    for lang in LANGS:
        lines = texts.get(lang, {}).get("lines", [])
        if isinstance(lines, list):
            result[lang] = len([line for line in lines if str(line)])
    return result


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--table",
        default=str(root / "translations" / "endless_duel.toml"),
        help="runtime localization table",
    )
    parser.add_argument(
        "--crawl",
        default=str(root / "translations" / "endless_duel_crawl.toml"),
        help="source-backed crawl text table",
    )
    parser.add_argument(
        "--options",
        default=str(root / "translations" / "endless_duel_options.toml"),
        help="source-backed option text table",
    )
    args = parser.parse_args()

    table = load_toml(Path(args.table))
    patches = patch_sections(table)
    by_kind = Counter(kind for kind, _ in patches)
    bytes_by_kind = Counter()
    lang_counts: Counter[str] = Counter()
    lang_bytes: Counter[str] = Counter()
    width_errors: list[str] = []

    for kind, patch in patches:
        width = patch_width(patch)
        bytes_by_kind[kind] += width
        address = int(patch.get("address", patch.get("addr", 0)))
        for lang in LANGS:
            if not patch_has_lang(patch, lang):
                continue
            lang_counts[lang] += 1
            lang_bytes[lang] += width
            target_hex = patch.get(f"{lang}_hex")
            if isinstance(target_hex, str) and len(bytes.fromhex(target_hex)) != width:
                width_errors.append(f"{kind} {address:#x} {lang}_hex width mismatch")

    option_source = load_toml(Path(args.options))
    crawl_source = load_toml(Path(args.crawl))
    option_counts = text_patch_langs(option_source)
    crawl_counts = crawl_langs(crawl_source)

    print("Endless Duel localization coverage")
    print(f"  default_lang: {table.get('default_lang', 'en')}")
    fallbacks = {
        key.removeprefix("fallback_"): value
        for key, value in table.items()
        if key.startswith("fallback_")
    }
    if fallbacks:
        fallback_text = ", ".join(f"{lang}->{target}" for lang, target in sorted(fallbacks.items()))
        print(f"  fallbacks: {fallback_text}")
    print(f"  total patches: {len(patches)}")
    for kind in sorted(by_kind):
        print(f"  {kind}: {by_kind[kind]} patches, {bytes_by_kind[kind]} source bytes")
    print()
    print("Native runtime entries:")
    for lang in LANGS:
        print(f"  {lang}: {lang_counts[lang]} patches, {lang_bytes[lang]} bytes")
    print()
    print("Source-backed editable data:")
    for lang in LANGS:
        print(
            f"  {lang}: option_entries={option_counts[lang]} "
            f"crawl_lines={crawl_counts.get(lang, 0)}"
        )
    print()
    print("Language status:")
    for lang in LANGS:
        if lang_counts[lang]:
            print(f"  {lang}: native data present")
        elif lang in fallbacks:
            print(f"  {lang}: fallback-only via {fallbacks[lang]}")
        else:
            print(f"  {lang}: no native data and no fallback")

    if width_errors:
        print()
        print("Width errors:", file=sys.stderr)
        for error in width_errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
