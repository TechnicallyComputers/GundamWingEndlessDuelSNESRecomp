#!/usr/bin/env python3
"""Audit pending CJK crawl text against currently identified CJK glyphs."""

from __future__ import annotations

import argparse
import unicodedata
import tomllib
from pathlib import Path


IGNORED = set(" \t\r\n。、，：:.,'\"-MS5")


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


def load_source(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def meaningful_chars(lines: list[str]) -> set[str]:
    chars: set[str] = set()
    for line in lines:
        for char in line:
            if char in IGNORED or char.isascii():
                continue
            category = unicodedata.category(char)
            if category.startswith("P") or category.startswith("Z"):
                continue
            chars.add(char)
    return chars


def is_hangul(char: str) -> bool:
    codepoint = ord(char)
    return (
        0xAC00 <= codepoint <= 0xD7AF
        or 0x1100 <= codepoint <= 0x11FF
        or 0x3130 <= codepoint <= 0x318F
        or 0xA960 <= codepoint <= 0xA97F
        or 0xD7B0 <= codepoint <= 0xD7FF
    )


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=str(root / "translations" / "endless_duel_cjk_candidates.toml"),
        help="pending CJK candidate translation TOML",
    )
    args = parser.parse_args()

    source = load_source(Path(args.source))
    available = meaningful_chars(source["source"]["ja"]["lines"])

    print("Endless Duel pending CJK feasibility")
    print(f"  available original-crawl CJK/kana glyphs: {len(available)}")
    print(f"  available: {''.join(sorted(available))}")

    for lang, entry in sorted(source.get("pending", {}).items()):
        chars = meaningful_chars(entry["lines"])
        missing = chars - available
        reusable = chars & available
        hangul = {char for char in chars if is_hangul(char)}
        print()
        print(f"{lang}: {entry.get('name', lang)}")
        print(f"  status: {entry.get('status', 'pending')}")
        print(f"  unique non-ASCII text glyphs: {len(chars)}")
        print(f"  reusable from original crawl: {len(reusable)}")
        print(f"  missing/new asset glyphs: {len(missing)}")
        if missing:
            print(f"  missing: {''.join(sorted(missing))}")
        if hangul:
            print(f"  hangul glyphs needing authored tiles: {''.join(sorted(hangul))}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
