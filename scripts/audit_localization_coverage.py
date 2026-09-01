#!/usr/bin/env python3
"""Report Endless Duel runtime-localization coverage."""

from __future__ import annotations

import argparse
import sys
import tomllib
from collections import Counter
from pathlib import Path


LANGS = ("en", "es", "fr", "it", "pt", "tl", "id", "zh", "ko")
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


def dialogue_langs(source: dict) -> Counter[str]:
    counts: Counter[str] = Counter()
    for entry in source.get("line", []):
        for lang in LANGS:
            if lang in entry and str(entry[lang]):
                counts[lang] += 1
    return counts


def label_langs(source: dict) -> Counter[str]:
    counts: Counter[str] = Counter()
    for key in ("label", "record"):
        for entry in source.get(key, []):
            for lang in LANGS:
                if lang in entry and str(entry[lang]):
                    counts[lang] += 1
    return counts


def cjk_crawl_langs(source: dict) -> dict[str, int]:
    result: dict[str, int] = {}
    zh_lines = (
        source.get("prototype", {})
        .get("zh_compact", {})
        .get("lines", [])
    )
    ko_lines = (
        source.get("pending", {})
        .get("ko", {})
        .get("lines", [])
    )
    if isinstance(zh_lines, list):
        result["zh"] = len([line for line in zh_lines if str(line)])
    if isinstance(ko_lines, list):
        result["ko"] = len([line for line in ko_lines if str(line)])
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
    parser.add_argument(
        "--dialogue-targets",
        default=str(root / "translations" / "endless_duel_dialogue_targets.toml"),
        help="source-backed dialogue target-language text table",
    )
    parser.add_argument(
        "--dialogue-cjk",
        default=str(root / "translations" / "endless_duel_dialogue_cjk.toml"),
        help="source-backed Korean/Chinese dialogue text table",
    )
    parser.add_argument(
        "--title-menu",
        default=str(root / "translations" / "endless_duel_title_menu.toml"),
        help="source-backed title-menu label text table",
    )
    parser.add_argument(
        "--option-text",
        default=str(root / "translations" / "endless_duel_option_text.toml"),
        help="source-backed native option/key-config record text table",
    )
    parser.add_argument(
        "--cjk-candidates",
        default=str(root / "translations" / "endless_duel_cjk_candidates.toml"),
        help="source-backed compact CJK crawl text table",
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
    dialogue_source = load_toml(Path(args.dialogue_targets))
    dialogue_cjk_source = load_toml(Path(args.dialogue_cjk))
    title_menu_source = load_toml(Path(args.title_menu))
    option_text_source = load_toml(Path(args.option_text))
    cjk_crawl_source = load_toml(Path(args.cjk_candidates))
    option_counts = text_patch_langs(option_source)
    crawl_counts = crawl_langs(crawl_source)
    cjk_crawl_counts = cjk_crawl_langs(cjk_crawl_source)
    dialogue_counts = dialogue_langs(dialogue_source)
    # The CJK dialogue rows live in their own source table (they carry glyph
    # pages, not fixed-width Latin payloads), so count them too -- otherwise
    # ko/zh read as dialogue_lines=0 while shipping 161 authored rows each.
    dialogue_counts.update(dialogue_langs(dialogue_cjk_source))
    title_menu_counts = label_langs(title_menu_source)
    option_text_counts = label_langs(option_text_source)

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
            f"crawl_lines={crawl_counts.get(lang, 0)} "
            f"cjk_crawl_lines={cjk_crawl_counts.get(lang, 0)} "
            f"title_labels={title_menu_counts[lang]} "
            f"option_records={option_text_counts[lang]} "
            f"dialogue_lines={dialogue_counts[lang]}"
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
