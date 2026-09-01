#!/usr/bin/env python3
"""Analyze Endless Duel reference IPS patches for translation work.

The English Aeon Genesis patch was authored for a copier-headered ROM, while
the Spanish patch targets a headerless ROM. This script normalizes both into
headerless file offsets, applies them in memory, then classifies changed spans.
The output is deliberately heuristic: it gives a work map for reverse
engineering, not a claim that every byte is fully understood.
"""

from __future__ import annotations

import argparse
import math
import string
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


PRINTABLE = set(bytes(string.printable, "ascii")) - {0x0b, 0x0c}
CODE_OPS = {
    0x20, 0x22, 0x4C, 0x5C, 0x60, 0x6B, 0x80, 0x82, 0x85, 0x8D, 0x99, 0x9D,
    0xA0, 0xA2, 0xA9, 0xAD, 0xB9, 0xBD, 0xC9, 0xD0, 0xEA, 0xF0, 0xFB,
}


@dataclass(frozen=True)
class IpsRecord:
    raw_offset: int
    offset: int
    data: bytes
    rle: bool

    @property
    def end(self) -> int:
        return self.offset + len(self.data)


@dataclass
class Span:
    start: int
    end: int
    langs: set[str]

    @property
    def size(self) -> int:
        return self.end - self.start


def repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "translations" / "endless_duel.toml").is_file():
        return cwd
    return Path(__file__.replace("\\", "/")).resolve().parents[1]


def parse_ips(path: Path, offset_adjust: int) -> list[IpsRecord]:
    data = path.read_bytes()
    if data[:5] != b"PATCH":
        raise ValueError(f"{path} is not a classic IPS patch")
    i = 5
    records: list[IpsRecord] = []
    while i < len(data):
        if data[i:i + 3] == b"EOF":
            return records
        raw_offset = int.from_bytes(data[i:i + 3], "big")
        i += 3
        size = int.from_bytes(data[i:i + 2], "big")
        i += 2
        rle = size == 0
        if rle:
            run = int.from_bytes(data[i:i + 2], "big")
            i += 2
            payload = bytes([data[i]]) * run
            i += 1
        else:
            payload = data[i:i + size]
            i += size
        offset = raw_offset + offset_adjust
        if offset < 0:
            raise ValueError(f"{path}: normalized negative offset {offset:#x}")
        records.append(IpsRecord(raw_offset, offset, payload, rle))
    raise ValueError(f"{path}: missing IPS EOF")


def apply_records(source: bytes, records: list[IpsRecord], label: str) -> bytearray:
    image = bytearray(source)
    for rec in records:
        if rec.end > len(image):
            raise ValueError(
                f"{label}: record {rec.raw_offset:#x}->{rec.offset:#x} "
                f"extends past ROM end {len(image):#x}"
            )
        image[rec.offset:rec.end] = rec.data
    return image


def diff_spans(source: bytes, image: bytes, label: str) -> list[Span]:
    spans: list[Span] = []
    i = 0
    while i < len(source):
        if source[i] == image[i]:
            i += 1
            continue
        start = i
        while i < len(source) and source[i] != image[i]:
            i += 1
        spans.append(Span(start, i, {label}))
    return spans


def merge_spans(spans: list[Span], gap: int) -> list[Span]:
    merged: list[Span] = []
    for span in sorted(spans, key=lambda s: (s.start, s.end)):
        if merged and span.start <= merged[-1].end + gap:
            merged[-1].end = max(merged[-1].end, span.end)
            merged[-1].langs.update(span.langs)
        else:
            merged.append(Span(span.start, span.end, set(span.langs)))
    return merged


def load_native_ranges(table_path: Path) -> list[tuple[int, int]]:
    with table_path.open("rb") as f:
        table = tomllib.load(f)
    ranges: list[tuple[int, int]] = []
    for patch in table.get("rom_patch", []):
        if not any(f"{lang}_hex" in patch for lang in ("fr", "it", "pt")):
            continue
        address = int(patch.get("address", patch.get("addr", 0)))
        size = len(bytes.fromhex(patch.get("source_hex", patch.get("src_hex", ""))))
        ranges.append((address, address + size))
    return ranges


def overlaps_any(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < hi and end > lo for lo, hi in ranges)


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    total = len(data)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def ascii_runs(data: bytes) -> list[str]:
    runs: list[str] = []
    current = bytearray()
    for byte in data:
        if byte in PRINTABLE and byte not in (0x00,):
            current.append(byte)
        else:
            if len(current) >= 4:
                runs.append(current.decode("ascii", errors="replace"))
            current.clear()
    if len(current) >= 4:
        runs.append(current.decode("ascii", errors="replace"))
    clean_runs = []
    for run in runs:
        useful = sum(1 for char in run if char.isalpha() or char in " .,!?'-:")
        letters = sum(1 for char in run if char.isalpha())
        if useful / len(run) >= 0.75 and letters >= 3:
            clean_runs.append(run)
    return clean_runs


def tilemap_score(data: bytes) -> float:
    pairs = len(data) // 2
    if pairs < 8:
        return 0.0
    hits = 0
    for i in range(0, pairs * 2, 2):
        a = data[i]
        b = data[i + 1]
        if (0x20 <= a <= 0x3F) or (0x20 <= b <= 0x3F) or b in (0x04, 0x08):
            hits += 1
    return hits / pairs


def code_score(data: bytes) -> float:
    if not data:
        return 0.0
    return sum(1 for byte in data if byte in CODE_OPS) / len(data)


def classify(span: Span, source: bytes, en: bytes, es: bytes, native_ranges: list[tuple[int, int]]) -> tuple[str, str]:
    if overlaps_any(span.start, span.end, native_ranges):
        return "already_native_latin_mapped", "FR/IT/PT runtime override exists"

    target = bytes(en[span.start:span.end])
    if span.langs == {"es"}:
        target = bytes(es[span.start:span.end])
    elif "es" in span.langs and bytes(en[span.start:span.end]) == bytes(source[span.start:span.end]):
        target = bytes(es[span.start:span.end])

    tscore = tilemap_score(target)
    cscore = code_score(target)
    ent = entropy(target)
    if tscore >= 0.65:
        return "likely_tilemap_or_tile_text_layout", f"tilemap_score={tscore:.2f} entropy={ent:.2f}"
    if span.size >= 0x400:
        return "likely_snes_4bpp_tile_graphics", f"large binary payload entropy={ent:.2f}"
    if span.size >= 32 and span.size % 32 == 0:
        return "likely_snes_4bpp_tile_graphics", f"32-byte-aligned tile payload entropy={ent:.2f}"

    samples = []
    for label, image in (("en", en), ("es", es)):
        runs = ascii_runs(bytes(image[span.start:span.end]))
        if runs:
            samples.append(f"{label}: " + " | ".join(runs[:3]))
    if samples:
        return "likely_ascii_text", "; ".join(samples)
    if cscore >= 0.18 or (span.size <= 8 and target[:1] in (b"\x20", b"\x22", b"\x4c", b"\x5c")):
        return "likely_code_or_pointer_fixup", f"opcode_score={cscore:.2f} entropy={ent:.2f}"
    return "unknown_binary_data", f"tilemap_score={tscore:.2f} opcode_score={cscore:.2f} entropy={ent:.2f}"


def format_span(span: Span, category: str, detail: str) -> str:
    langs = ",".join(sorted(span.langs))
    return f"0x{span.start:06x}-0x{span.end - 1:06x}  {span.size:5d}  {langs:5s}  {category:36s}  {detail}"


def write_report(path: Path, rows: list[tuple[Span, str, str]], en_records: list[IpsRecord], es_records: list[IpsRecord]) -> None:
    counts = Counter(category for _, category, _ in rows)
    lines = [
        "# Endless Duel Reference IPS Map",
        "",
        "This report is generated by `scripts/analyze_reference_ips.py`. It parses the",
        "English and Spanish IPS references, normalizes the English copier-header",
        "offsets by -0x200, applies both patches in memory, then classifies changed",
        "spans heuristically.",
        "",
        "The categories are a reverse-engineering worklist, not final truth. Anything",
        "marked tilemap/graphics still needs screenshot validation after it becomes a",
        "runtime patch.",
        "",
        "Follow-up decoding has confirmed the four `likely_tilemap_or_tile_text_layout`",
        "ranges below are 32-column, two-row dialogue tilemaps. The decoded source table",
        "is `translations/endless_duel_dialogue.toml`, and the readable audit is",
        "`translations/reference_dialogue_decode.md`. The large graphics ranges remain",
        "tile-art/font work: `0x015400-0x016843` contains title/logo and menu label",
        "tiles, `0x006f00-0x007eff` contains mixed text-like font tiles and other art,",
        "and `0x00e000-0x00ffff` contains larger UI/character tile art.",
        "",
        "## Patch Inputs",
        "",
        f"- English IPS records: {len(en_records)}",
        f"- Spanish IPS records: {len(es_records)}",
        "",
        "## Category Counts",
        "",
    ]
    for category, count in sorted(counts.items()):
        byte_count = sum(span.size for span, cat, _ in rows if cat == category)
        lines.append(f"- `{category}`: {count} spans, {byte_count} bytes")
    lines.extend([
        "",
        "## Largest Unmapped Translation Candidates",
        "",
        "| Range | Bytes | Langs | Category | Detail |",
        "| --- | ---: | --- | --- | --- |",
    ])
    candidates = [
        (span, category, detail)
        for span, category, detail in rows
        if category != "already_native_latin_mapped"
    ]
    candidates.sort(key=lambda item: (-item[0].size, item[0].start))
    for span, category, detail in candidates[:80]:
        detail = detail.replace("|", "/")
        lines.append(
            f"| `0x{span.start:06x}-0x{span.end - 1:06x}` | "
            f"{span.size} | {','.join(sorted(span.langs))} | `{category}` | {detail} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", default=str(root / "Shin Kidou Senki Gundam W - Endless Duel (J).smc"))
    parser.add_argument("--en-ips", required=True)
    parser.add_argument("--es-ips", required=True)
    parser.add_argument("--en-offset-adjust", type=lambda x: int(x, 0), default=-0x200)
    parser.add_argument("--es-offset-adjust", type=lambda x: int(x, 0), default=0)
    parser.add_argument("--merge-gap", type=int, default=16)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--report", default=str(root / "translations" / "reference_patch_map.md"))
    args = parser.parse_args()

    source = Path(args.rom).read_bytes()
    en_records = parse_ips(Path(args.en_ips), args.en_offset_adjust)
    es_records = parse_ips(Path(args.es_ips), args.es_offset_adjust)
    en_image = apply_records(source, en_records, "en")
    es_image = apply_records(source, es_records, "es")
    spans = merge_spans(diff_spans(source, en_image, "en") + diff_spans(source, es_image, "es"), args.merge_gap)
    native_ranges = load_native_ranges(root / "translations" / "endless_duel.toml")

    rows = []
    for span in spans:
        category, detail = classify(span, source, en_image, es_image, native_ranges)
        rows.append((span, category, detail))

    counts = Counter(category for _, category, _ in rows)
    print("Endless Duel reference IPS analysis")
    print(f"  ROM: {Path(args.rom)} ({len(source)} bytes)")
    print(f"  EN IPS: {Path(args.en_ips)} records={len(en_records)} offset_adjust={args.en_offset_adjust:+#x}")
    print(f"  ES IPS: {Path(args.es_ips)} records={len(es_records)} offset_adjust={args.es_offset_adjust:+#x}")
    print(f"  merged changed spans: {len(rows)}")
    print()
    print("Categories:")
    for category in sorted(counts):
        byte_count = sum(span.size for span, cat, _ in rows if cat == category)
        print(f"  {category}: {counts[category]} spans, {byte_count} bytes")
    print()
    print("Largest unmapped candidates:")
    candidates = [
        (span, category, detail)
        for span, category, detail in rows
        if category != "already_native_latin_mapped"
    ]
    candidates.sort(key=lambda item: (-item[0].size, item[0].start))
    for span, category, detail in candidates[:args.limit]:
        print("  " + format_span(span, category, detail))

    report_path = Path(args.report)
    write_report(report_path, rows, en_records, es_records)
    print()
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
