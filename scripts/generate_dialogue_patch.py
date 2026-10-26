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
from decode_reference_tilemaps import CHAR_BY_TILE, toml_quote
from generate_dialogue_accent_patch import (
    load_source as load_accent_source,
    per_language_charmap,
)
from generate_dialogue_cjk_patch import row_payloads as cjk_row_payloads
from reconstruct_table_image import ALL_LANGS, build_image, default_rom_path


BEGIN_MARKER = "# BEGIN GENERATED DIALOGUE TILEMAP PATCHES"
END_MARKER = "# END GENERATED DIALOGUE TILEMAP PATCHES"
TARGET_LANGS = ("fr", "it", "pt", "tl", "id")
# Spanish is not normally authored here: it ships the imported Max1323
# reference tilemaps, decoded row-for-row into endless_duel_dialogue.toml and
# passed through as bytes.  An `es` string in the targets table is an OVERRIDE
# that repairs one reference row (see `split_reference_fragments`).
AUTHORED_LANGS = TARGET_LANGS + ("es",)
TILE_BY_CHAR = {char: tile for tile, char in CHAR_BY_TILE.items()}
ROW_WORDS = 32
ROW_BYTES = ROW_WORDS * 2
ROW_SIZE = ROW_BYTES * 2  # top row + bottom row = one 16px line of text


def load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def word_bytes(word: int) -> bytes:
    return bytes((word & 0xFF, (word >> 8) & 0xFF))


def encode_line(source_hex: str, start_col: int, text: str,
                charmap: dict[str, int] | None = None) -> str:
    charmap = TILE_BY_CHAR if charmap is None else charmap
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
        if char not in charmap:
            raise ValueError(f"unsupported dialogue glyph {char!r} in {text!r}")
        tile = charmap[char]
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
            for lang in AUTHORED_LANGS
            if lang in entry and str(entry[lang])
        }
        reference = entry.get("es_reference_hex")
        if "es" in values:
            if not isinstance(reference, str) or not reference:
                raise ValueError(
                    f"target line {index} (0x{address:06x}): an `es` override "
                    "must record `es_reference_hex`, the reference row it "
                    "replaces, so --check can prove replay equivalence")
            if len(reference) != ROW_SIZE * 2:
                raise ValueError(
                    f"target line {index} (0x{address:06x}): "
                    f"es_reference_hex must be {ROW_SIZE} bytes")
            values["es_reference_hex"] = reference
        elif isinstance(reference, str) and reference:
            raise ValueError(
                f"target line {index} (0x{address:06x}): es_reference_hex "
                "without an `es` override")
        if values:
            targets[address] = values
    return targets


def es_repairs(dialogue: dict, targets: dict[int, dict[str, str]],
               charmaps: dict[str, dict[str, int]]) -> dict[int, tuple[bytes, bytes]]:
    """address -> (reference row bytes, repaired row bytes) for `es` overrides."""
    repairs: dict[int, tuple[bytes, bytes]] = {}
    for entry in dialogue.get("line", []):
        address = int(entry["address"])
        authored = targets.get(address, {}).get("es")
        if not authored:
            continue
        repaired = encode_line(entry["en_hex"], int(entry["start_col"]),
                               authored, charmaps.get("es"))
        reference = targets[address]["es_reference_hex"]
        if bytes.fromhex(reference) == bytes.fromhex(repaired):
            raise ValueError(
                f"0x{address:06x}: the `es` override re-encodes to the "
                "reference row; drop the override instead")
        repairs[address] = (bytes.fromhex(reference), bytes.fromhex(repaired))
    return repairs


HEX_KEY = re.compile(r'(?m)^([a-z_]+)_hex = "([0-9a-f]*)"$')

# Dialogue rows that do NOT survive a switch back to Spanish, and did not
# before any of this work either: verified against the table at bc8554d, where
# the same seventeen rows fail the same way. Their Spanish reference bytes are
# byte-identical to some OTHER language's payload for the row, so on the way
# out the imported fragment recognises the row and restores English, and on the
# way back the ambiguity resolves to the wrong side. Boot is unaffected in
# every language - only a mid-session language change reaches it.
#
# This is a burn-down list, not a permission slip: `verify_replay` fails on any
# row outside it, so a new collision cannot be introduced quietly. Repairing a
# row with an `es` override also fixes its switch behaviour, because the
# generator then owns the row outright - 0x02cf80 was on this list until it was
# repaired here. Shorten the list as rows are fixed; never extend it.
KNOWN_SWITCH_LOSS = frozenset((
    0x017800, 0x017B00, 0x017C00, 0x027000, 0x027500, 0x02CB80, 0x02D500,
    0x02E080, 0x03F500, 0x03FA00, 0x05E080, 0x05E480, 0x05E780, 0x05EA80,
    0x05EC80, 0x05ED80, 0x05EE80,
))


def split_reference_fragments(table_text: str, rows: list[int]) -> tuple[str, int, int]:
    """Cut every imported fragment free of the rows the generator repairs.

    Returns ``(text, split, dropped)``.

    A repaired row cannot be co-owned. The imported IPS fragments are
    byte-granular (91 of them touch the ten rows repaired here), they straddle
    row boundaries, and they do NOT tile a row - an IPS record exists only
    where the reference image differs from the cart, so bytes that already
    matched belong to no fragment at all. Any scheme that leaves the fragments
    a partial claim produces intermediate images that neither they nor the row
    patch recognise: rewrite their Spanish payload and the row patch's guard
    rejects the result; fill the holes separately and a language switch strands
    a handful of Spanish bytes inside the previous language's row.

    So the generator takes the rows outright, exactly as
    docs/LOCALIZATION_PLAYBOOK.md section 2 prescribes: fragments are split
    around each repaired row (or dropped when wholly inside one), and the
    dialogue patch below becomes the row's only writer, carrying `source` (the
    untouched cart row) plus a payload per language. One owner means every
    boot and every language switch resolves in one step, `off` included.
    """
    if not rows:
        return table_text, 0, 0
    windows = [(row, row + ROW_SIZE) for row in rows]
    out: list[str] = []
    cursor = 0
    generated = False
    split = dropped = 0
    events = sorted(
        [(m.start(), "patch") for m in re.finditer(r"(?m)^\[\[rom_patch\]\]$", table_text)]
        + [(m.start(), m.group(1).lower())
           for m in re.finditer(r"(?m)^# (BEGIN|END) GENERATED ", table_text)])
    for index, (start, kind) in enumerate(events):
        if kind in ("begin", "end"):
            generated = kind == "begin"
            continue
        if generated:
            continue
        end = events[index + 1][0] if index + 1 < len(events) else len(table_text)
        chunk = table_text[start:end]
        address_m = re.search(r"(?m)^address = (0x[0-9a-f]+|\d+)$", chunk)
        payloads = {m.group(1): m.group(2) for m in HEX_KEY.finditer(chunk)}
        if not address_m or "source" not in payloads:
            continue
        address = int(address_m.group(1), 0)
        size = len(payloads["source"]) // 2
        if not any(address < hi and lo < address + size for lo, hi in windows):
            continue
        for key, value in payloads.items():
            if len(value) // 2 != size:
                raise ValueError(
                    f"fragment at 0x{address:06x}: {key}_hex is "
                    f"{len(value) // 2} bytes, source_hex is {size}")
        keep = [offset for offset in range(address, address + size)
                if not any(lo <= offset < hi for lo, hi in windows)]
        runs: list[list[int]] = []
        for offset in keep:
            if runs and runs[-1][1] == offset:
                runs[-1][1] = offset + 1
            else:
                runs.append([offset, offset + 1])
        order = [m.group(1) for m in HEX_KEY.finditer(chunk)]
        rebuilt: list[str] = []
        for low, high in runs:
            body = ["[[rom_patch]]", f"address = 0x{low:06x}"]
            for key in order:
                sliced = payloads[key][(low - address) * 2:(high - address) * 2]
                body.append(f'{key}_hex = "{sliced}"')
            rebuilt.append("\n".join(body) + "\n\n")
        if runs:
            split += 1
        else:
            dropped += 1
        out.append(table_text[cursor:start])
        out.append("".join(rebuilt))
        cursor = end
    out.append(table_text[cursor:])
    return "".join(out), split, dropped


def row_payloads(dialogue: dict, targets: dict[int, dict[str, str]],
                 charmaps: dict[str, dict[str, int]],
                 cjk: dict[int, dict[str, str]],
                 repairs: dict[int, tuple[bytes, bytes]],
                 rom: bytes) -> dict[int, dict[str, str]]:
    """address -> {"source": hex, "<lang>": hex} for every generated row patch."""
    rows: dict[int, dict[str, str]] = {}
    for entry in dialogue.get("line", []):
        address = int(entry["address"])
        start_col = int(entry["start_col"])
        base_hex = entry["en_hex"]
        payloads: dict[str, str] = {}
        for lang in TARGET_LANGS:
            text = targets.get(address, {}).get(lang, entry.get(lang))
            if text:
                payloads[lang] = encode_line(
                    base_hex, start_col, text, charmaps.get(lang))
        # CJK payloads are pre-encoded tilemap rows referencing per-quote glyph
        # pages (scripts/generate_dialogue_cjk_patch.py).  They ride inside THIS
        # patch rather than a second one over the same row: a second patch would
        # leave bytes the first one's guard no longer recognises, and es/fr/it/pt
        # would stop being restored on a language switch.
        payloads.update(cjk.get(address, {}))
        if not payloads:
            continue
        if address in repairs:
            # The generator owns a repaired row outright (see
            # split_reference_fragments), so this patch has to describe the row
            # from the cart up.  `source` is the untouched Japanese row, which
            # is what `off` restores to; `en_hex` is what every language whose
            # chain ends at English falls back to.  Without an explicit en_hex
            # a language carrying no payload of its own would resolve to
            # `source` and render Japanese tiles.
            payloads["source"] = rom[address:address + ROW_SIZE].hex()
            payloads["en"] = base_hex
            payloads["es"] = repairs[address][1].hex()
        else:
            # Elsewhere the imported fragments have already written the English
            # row, so that is this patch's guard source.  The table has no
            # fallback_es, so Spanish cannot reach the English baseline through
            # the chain: an es-less patch restores source_hex, which would show
            # English in Spanish wherever the guard matched.  Emit the decoded
            # Spanish row explicitly; it is a no-op when the fragments already
            # produced it.
            payloads["source"] = base_hex
            decoded = entry.get("es_hex")
            if isinstance(decoded, str) and decoded:
                payloads["es"] = decoded
        rows[address] = payloads
    return rows


def generated_blocks(dialogue: dict, rows: dict[int, dict[str, str]]) -> list[str]:
    blocks = [BEGIN_MARKER]
    order = ["es", "en"] + list(TARGET_LANGS) + ["ko", "zh", "th"]
    count = 0
    for index, entry in enumerate(dialogue.get("line", []), 1):
        address = int(entry["address"])
        payloads = rows.get(address)
        if not payloads:
            continue
        count += 1
        blocks.extend([
            "",
            "[[rom_patch]]",
            f"address = 0x{address:06x}",
            f'# generated_from = "translations/endless_duel_dialogue.toml line {index}"',
            f'source_hex = "{payloads["source"]}"',
        ])
        for lang in order:
            if lang in payloads:
                blocks.append(f'{lang}_hex = "{payloads[lang]}"')
        for lang in sorted(set(payloads) - set(order) - {"source"}):
            blocks.append(f'{lang}_hex = "{payloads[lang]}"')
    blocks.extend(["", END_MARKER, ""])
    if count == 0:
        blocks.insert(1, "# No native dialogue overlays are authored yet.")
    return blocks


def verify_replay(table_text: str, dialogue: dict, rows: dict[int, dict[str, str]],
                  repairs: dict[int, tuple[bytes, bytes]], rom: bytes) -> list[str]:
    """Replay the table and assert every dialogue row, for every language and switch.

    Two things are being defended here.  The first is that a repaired row shows
    the authored Spanish and nothing else shows any change: en stays
    byte-identical, `off` still restores the untouched Japanese row, and every
    language whose chain ends at English still gets the English row.

    The second is that language SWITCHING resolves in one step.  The engine
    re-applies rom_patches over the CURRENT cart image rather than a pristine
    one, so a guard that stops recognising the live row fails only after a
    switch -- which is exactly how the byte-level repair attempts died, each of
    them clean at boot and wrong the moment the language changed.
    """
    table = tomllib.loads(table_text)
    languages = ALL_LANGS + ("off",)
    entries = {int(entry["address"]): entry for entry in dialogue.get("line", [])}

    def expected(address: int, lang: str) -> bytes:
        # `off` means "no translation": every writer of the row restores what
        # the cart shipped, whether that is a reference fragment putting its
        # own source back or a repaired row's patch restoring the Japanese
        # tiles it recorded as `source`.
        if lang == "off":
            return rom[address:address + ROW_SIZE]
        payloads = rows[address]
        if lang in payloads:
            return bytes.fromhex(payloads[lang])
        # Every chain here ends at English. A repaired row spells that out as
        # `en_hex`; elsewhere the patch's `source` IS the English row, because
        # the imported fragments wrote it before this patch runs.
        if "en" in payloads:
            return bytes.fromhex(payloads["en"])
        return bytes.fromhex(payloads["source"])

    failures: list[str] = []
    images = {}
    for lang in languages:
        image, _applied, skipped = build_image(lang, table, rom)
        images[lang] = image
        if lang in ("en", "zh", "ko", "th") and skipped:
            failures.append(f"{lang}: {skipped} guard-skipped patches at boot")
        for address in rows:
            want = expected(address, lang)
            got = image[address:address + ROW_SIZE]
            if got != want:
                failures.append(
                    f"{lang}: row 0x{address:06x} is not the expected payload")
    for address, (_reference, repaired) in repairs.items():
        if images["es"][address:address + ROW_SIZE] != repaired:
            failures.append(
                f"es: row 0x{address:06x} is not the repaired Spanish row")
        if images["off"][address:address + ROW_SIZE] != rom[address:address + ROW_SIZE]:
            failures.append(
                f"off: row 0x{address:06x} does not restore the cart bytes")
        if images["en"][address:address + ROW_SIZE] != bytes.fromhex(
                entries[address]["en_hex"]):
            failures.append(f"en: row 0x{address:06x} moved")

    for sequence in (("es", "fr"), ("es", "off"), ("off", "es"), ("fr", "es"),
                     ("en", "es"), ("es", "ko"), ("ko", "es"), ("es", "en"),
                     ("off", "fr", "es", "off", "es")):
        image = rom
        for lang in sequence:
            image, _applied, _skipped = build_image(lang, table, image)
        label = "->".join(sequence)
        for address in rows:
            want = expected(address, sequence[-1])
            if image[address:address + ROW_SIZE] == want:
                if address in KNOWN_SWITCH_LOSS and address in repairs:
                    failures.append(
                        f"0x{address:06x} now survives a switch: drop it from "
                        "KNOWN_SWITCH_LOSS")
                continue
            if address in KNOWN_SWITCH_LOSS and address not in repairs:
                continue
            failures.append(
                f"{label}: row 0x{address:06x} did not resolve to "
                f"{sequence[-1]}")
    return failures


def replace_generated_section(table_text: str, generated: str) -> str:
    pattern = re.compile(rf"(?ms)^{re.escape(BEGIN_MARKER)}\n.*?^{re.escape(END_MARKER)}\n?")
    matches = list(pattern.finditer(table_text))
    if matches:
        pieces = [table_text[:matches[0].start()], generated]
        cursor = matches[0].end()
        for match in matches[1:]:
            pieces.append(table_text[cursor:match.start()])
            cursor = match.end()
        pieces.append(table_text[cursor:])
        return "".join(pieces)
    return table_text.rstrip() + "\n\n" + generated


def normalize_embedded_markers(table_text: str) -> str:
    return re.sub(
        r'("[0-9a-fA-F]*")# BEGIN GENERATED ([^\r\n]+)',
        r'\1\n# BEGIN GENERATED \2',
        table_text,
    )


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
    parser.add_argument(
        "--accents",
        default=str(root / "translations" / "endless_duel_dialogue_accents.toml"),
        help="per-language accented glyph cell allocation table",
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    accents = load_accent_source(Path(args.accents))
    charmaps = {lang: per_language_charmap(accents, lang)
                for lang in AUTHORED_LANGS}
    source_path = Path(args.source)
    table_path = Path(args.table)
    dialogue = load_toml(source_path)
    targets = load_targets(Path(args.targets))
    decoded_addresses = {int(entry["address"]) for entry in dialogue.get("line", [])}
    unknown_targets = sorted(set(targets) - decoded_addresses)
    if unknown_targets:
        formatted = ", ".join(f"0x{address:06x}" for address in unknown_targets)
        raise ValueError(f"target dialogue address not present in decoded source: {formatted}")
    cjk = cjk_row_payloads(root)
    unknown_cjk = sorted(set(cjk) - decoded_addresses)
    if unknown_cjk:
        formatted = ", ".join(f"0x{address:06x}" for address in unknown_cjk)
        raise ValueError(
            f"CJK dialogue address not present in decoded source: {formatted}")
    table_text = normalize_embedded_markers(table_path.read_text(encoding="utf-8"))
    repairs = es_repairs(dialogue, targets, charmaps)
    rom = default_rom_path().read_bytes()
    rows = row_payloads(dialogue, targets, charmaps, cjk, repairs, rom)
    split_text, split, dropped = split_reference_fragments(
        table_text, sorted(repairs))
    updated = replace_generated_section(
        split_text, "\n".join(generated_blocks(dialogue, rows)))

    if args.write:
        table_path.write_text(updated, encoding="utf-8", newline="\n")
        print(f"updated {table_path}")
        print(f"es repairs: {len(repairs)} rows; reference fragments "
              f"split {split}, dropped {dropped}")
        return 0

    if updated != table_text:
        print("dialogue patch section is not up to date")
        return 1
    if split or dropped:
        print(f"reference fragments still overlap {len(repairs)} repaired rows "
              f"(split {split}, dropped {dropped}); run --write")
        return 1
    failures = verify_replay(updated, dialogue, rows, repairs, rom)
    if failures:
        for line in sorted(set(failures)):
            print(line)
        return 1
    print(f"dialogue replay verified: {len(rows)} rows, "
          f"{len(ALL_LANGS) + 1} languages, {len(repairs)} es repairs")
    print("dialogue patch section is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
