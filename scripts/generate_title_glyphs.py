#!/usr/bin/env python3
"""Generate title-menu glyph override TOML for authored and CJK UI glyphs."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:
    raise SystemExit("generate_title_glyphs.py requires Pillow: py -3 -m pip install pillow") from exc


ASCII_OVERRIDES = [
    (
        "H",
        "authored from native A/M vertical stems and native 8px title-menu crossbar weight",
        8,
        8,
        (0xC3, 0xC3, 0xC3, 0xFF, 0xFF, 0xC3, 0xC3, 0xC3),
    ),
    (
        "C",
        "authored from native O by opening the right side and preserving native top/bottom caps",
        8,
        8,
        (0x7E, 0xFF, 0xC0, 0xC0, 0xC0, 0xC0, 0xFF, 0x7E),
    ),
]


DEFAULT_FONTS = {
    "zh": r"C:\Windows\Fonts\msyhbd.ttc",
    "ko": r"C:\Windows\Fonts\malgunbd.ttf",
}


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel_title_menu.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


ENTRY_KEYS = ("label", "record")


def ordered_non_ascii_chars(sources: list[dict], langs: list[str]) -> list[tuple[str, str]]:
    """Every non-ASCII codepoint the translated UI strings need, first use first.

    Sources carry either `[[label]]` (title-menu tile art) or `[[record]]`
    (option display-list text) entries; both are scanned so the atlas stays a
    superset of what the two generators consume.
    """
    chars: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        for lang in langs:
            for key in ENTRY_KEYS:
                for entry in source.get(key, []):
                    for char in str(entry.get(lang, "")):
                        if ord(char) < 128 or char in seen:
                            continue
                        seen.add(char)
                        chars.append((lang, char))
    return chars


def render_mask(char: str, font_path: Path, font_size: int, target: int, threshold: int) -> tuple[int, ...]:
    font = ImageFont.truetype(str(font_path), font_size)
    canvas = Image.new("L", (target * 6, target * 6), 0)
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), char, font=font)
    draw.text((target - bbox[0], target - bbox[1]), char, font=font, fill=255)
    bbox = canvas.getbbox()
    if bbox is None:
        return tuple(0 for _ in range(target))

    crop = canvas.crop(bbox)
    scale = min(target / crop.width, target / crop.height)
    out_w = max(1, round(crop.width * scale))
    out_h = max(1, round(crop.height * scale))
    small = crop.resize((out_w, out_h), Image.Resampling.LANCZOS)
    fitted = Image.new("L", (target, target), 0)
    fitted.paste(small, ((target - out_w) // 2, (target - out_h) // 2))

    rows = []
    for y in range(target):
        bits = 0
        for x in range(target):
            bits = (bits << 1) | (1 if fitted.getpixel((x, y)) >= threshold else 0)
        rows.append(bits)
    return tuple(rows)


def emit_header(lines: list[str]) -> None:
    lines.extend(
        [
            "# Authored/generated glyph masks for the translated Endless Duel UI.",
            "#",
            "# The base atlas is seeded from glyph masks extracted from native selected",
            "# title-menu rows. Entries here override that atlas for letters or",
            "# codepoints needed by translations but not present in the original English",
            "# labels. Rows are bitmasks read left to right within `width`.",
            "#",
            "# Consumers: scripts/generate_title_menu_vram_patch.py (title label tile",
            "# art) and scripts/generate_option_cjk_patch.py (option/key-config font",
            "# slot injection).",
            "",
            "schema = 1",
            'source_capture = "C:\\\\Users\\\\Matthew\\\\AppData\\\\Local\\\\Temp\\\\gwed_title_menu_wram_select_20260830"',
            'style = "Endless Duel title mode selector"',
            "",
        ]
    )


def emit_ascii_overrides(lines: list[str]) -> None:
    for char, origin, width, height, rows in ASCII_OVERRIDES:
        lines.extend(
            [
                "[[glyph]]",
                f'char = "{char}"',
                f'origin = "{origin}"',
                f"width = {width}",
                f"height = {height}",
            ]
        )
        for index, row in enumerate(rows):
            lines.append(f'row{index} = "{row:0{(width + 3) // 4}x}"')
        lines.append("")


def emit_codepoint_glyph(
    lines: list[str],
    char: str,
    font_path: Path,
    font_name: str,
    target: int,
    rows: tuple[int, ...],
) -> None:
    digits = (target + 3) // 4
    lines.extend(
        [
            "[[glyph]]",
            f'codepoint = "U+{ord(char):04X}"',
            f'origin = "generated from {font_name}, reduced to compact {target}px title-menu mask"',
            f"width = {target}",
            f"height = {target}",
        ]
    )
    for index, row in enumerate(rows):
        lines.append(f'row{index} = "{row:0{digits}x}"')
    lines.append("")


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        action="append",
        default=[
            str(root / "translations" / "endless_duel_title_menu.toml"),
            str(root / "translations" / "endless_duel_option_text.toml"),
        ],
        help="source TOML with translated [[label]]/[[record]] entries; may be passed more than once",
    )
    parser.add_argument("--out", default=str(root / "translations" / "endless_duel_title_glyphs.toml"))
    parser.add_argument("--langs", default="zh,ko")
    parser.add_argument("--font-zh", default=DEFAULT_FONTS["zh"])
    parser.add_argument("--font-ko", default=DEFAULT_FONTS["ko"])
    parser.add_argument("--font-size", type=int, default=24)
    parser.add_argument("--target", type=int, default=8)
    parser.add_argument("--threshold", type=int, default=72)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    langs = [part.strip() for part in args.langs.split(",") if part.strip()]
    font_by_lang = {
        "zh": (Path(args.font_zh), "Microsoft YaHei Bold"),
        "ko": (Path(args.font_ko), "Malgun Gothic Bold"),
    }
    sources = []
    for source_path in args.source:
        with Path(source_path).open("rb") as f:
            sources.append(tomllib.load(f))

    lines: list[str] = []
    emit_header(lines)
    emit_ascii_overrides(lines)

    for lang, char in ordered_non_ascii_chars(sources, langs):
        font_path, font_name = font_by_lang[lang]
        if not font_path.is_file():
            raise FileNotFoundError(font_path)
        rows = render_mask(char, font_path, args.font_size, args.target, args.threshold)
        emit_codepoint_glyph(lines, char, font_path, font_name, args.target, rows)

    text = "\n".join(lines).rstrip() + "\n"
    out = Path(args.out)
    if args.check:
        current = out.read_text(encoding="utf-8") if out.is_file() else ""
        if current != text:
            raise SystemExit(f"{out} is not up to date")
        print(f"title glyph asset is up to date: {out}")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
