#!/usr/bin/env python3
"""
aggregate_human_data.py
────────────────────────
Builds data_humans.json (or a name of your choice) for the human-annotator
agreement review tool.

Reads
─────
  audio/audio_files_humans_tiered_splits/<Tier>/*.wav
      → determines which audio files exist and their agreement tier

  audio/audio_files_humanannotations/transcriptions-<Annotator>.csv
      → per-annotator labels (transcription + metadata columns)

CSV columns expected (exact names, case-insensitive match):
  Filename, Transcription, Speaker Accent, Register,
  Loan Word Languages, Genders Present, Speaker Count, Region

Output schema
─────────────
{
  "_meta": {
    "version":     "1.0.0",
    "description": "...",
    "tiers":       ["Gold", "Silver", "Bronze"],
    "annotators":  ["Bouazza", "Imrane", "Yassine"],
    "total_files": N
  },
  "files": [
    {
      "id":         "Casablanca_1-01_16k",
      "filename":   "Casablanca_1-01_16k.wav",
      "tier":       "Gold",
      "region":     "Casablanca",
      "audio_path": "audio/audio_files_humans_tiered_splits/Gold/Casablanca_1-01_16k.wav",
      "annotations": {
        "Bouazza": {
          "transcription":      "...",
          "speaker_accent":     "Casablanca",
          "register":           "Moroccan Arabic",
          "loan_word_languages": "French",
          "genders_present":    "Male",
          "speaker_count":      "1"
        },
        "Imrane":  { ... },
        "Yassine": { ... }
      }
    }
  ]
}

Missing / empty annotator fields are stored as null.
The "Of " prefix is stripped from speaker_accent values.

Usage
─────
  python scripts/aggregate_human_data.py
  python scripts/aggregate_human_data.py --audio-dir audio --output data_humans.json
  python scripts/aggregate_human_data.py --verbose
"""

import argparse
import csv
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# Preferred display order for tiers (Gold first = most agreement)
TIER_ORDER = ["Gold", "Silver", "Bronze"]

# CSV column names → JSON field names for annotator data.
# Mapping is case-insensitive; exact CSV header names on the left.
ANNOTATOR_FIELD_MAP: list[tuple[str, str]] = [
    ("Transcription",       "transcription"),
    ("Speaker Accent",      "speaker_accent"),
    ("Register",            "register"),
    ("Loan Word Languages", "loan_word_languages"),
    ("Genders Present",     "genders_present"),
    ("Speaker Count",       "speaker_count"),
]


# ──────────────────────────────────────────────────────────────────────────────
# CSV helpers
# ──────────────────────────────────────────────────────────────────────────────

def _col(fieldnames: list[str], target: str) -> str | None:
    """Return the actual fieldname that case-insensitively matches target."""
    t = target.lower()
    for f in fieldnames:
        if f.strip().lower() == t:
            return f
    return None


def _normalise(value: str) -> str | None:
    """Strip whitespace; return None for empty strings."""
    v = value.strip()
    return v if v else None


def _strip_of_prefix(value: str | None) -> str | None:
    """Remove leading 'Of ' (case-insensitive) from speaker_accent values."""
    if value and re.match(r"^of\s+", value, flags=re.IGNORECASE):
        return value[value.index(" ") + 1:].strip() or None
    return value


# ──────────────────────────────────────────────────────────────────────────────
# CSV reader
# ──────────────────────────────────────────────────────────────────────────────

def read_annotator_csvs(
    annotations_dir: Path,
) -> dict[str, dict[str, dict]]:
    """
    Read all transcriptions-<Annotator>.csv files in annotations_dir.

    Returns
    -------
    {
      annotator_name: {
        stem_lower: {
          "region":            str | None,
          "transcription":     str | None,
          "speaker_accent":    str | None,   # "Of " prefix already stripped
          "register":          str | None,
          "loan_word_languages": str | None,
          "genders_present":   str | None,
          "speaker_count":     str | None,
        }
      }
    }
    """
    pattern = re.compile(r"^transcriptions[-_](.+)\.csv$", re.IGNORECASE)
    result: dict[str, dict[str, dict]] = {}

    csv_files = sorted(annotations_dir.glob("transcriptions-*.csv"))
    if not csv_files:
        log.error("No transcription CSVs found in %s", annotations_dir)
        sys.exit(1)

    for csv_path in csv_files:
        m = pattern.match(csv_path.name)
        if not m:
            continue
        annotator = m.group(1)

        annotator_map: dict[str, dict] = {}

        try:
            with csv_path.open(newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                if not reader.fieldnames:
                    log.warning("Empty or header-less CSV: %s", csv_path.name)
                    continue

                fields = list(reader.fieldnames)

                fn_col     = _col(fields, "Filename")
                region_col = _col(fields, "Region")

                if fn_col is None:
                    log.error("[%s] No 'Filename' column — skipping.", csv_path.name)
                    continue
                if region_col is None:
                    log.warning("[%s] No 'Region' column — region will be null.", csv_path.name)

                # Build column map for the annotator fields
                field_cols: list[tuple[str, str | None]] = [
                    (json_key, _col(fields, csv_col))
                    for csv_col, json_key in ANNOTATOR_FIELD_MAP
                ]

                row_num = 1
                for row in reader:
                    row_num += 1
                    raw_fn = row.get(fn_col, "").strip()
                    if not raw_fn:
                        continue

                    stem = Path(raw_fn).stem.lower()
                    region = _normalise(row.get(region_col, "") if region_col else "")

                    entry: dict = {"region": region}
                    for json_key, col in field_cols:
                        val = _normalise(row.get(col, "") if col else "")
                        if json_key == "speaker_accent":
                            val = _strip_of_prefix(val)
                        entry[json_key] = val

                    if stem in annotator_map:
                        log.warning(
                            "[%s] Duplicate stem '%s' at row %d — overwriting earlier entry.",
                            csv_path.name, stem, row_num,
                        )
                    annotator_map[stem] = entry

        except OSError as exc:
            log.warning("Cannot open %s: %s", csv_path, exc)
            continue

        result[annotator] = annotator_map
        log.info("  CSV  %-40s  → %d entries", csv_path.name, len(annotator_map))

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Tiered audio discoverer
# ──────────────────────────────────────────────────────────────────────────────

def discover_tiered_audio(tiered_dir: Path) -> dict[str, dict]:
    """
    Walk tiered_dir/<Tier>/*.wav (and *.mp3, as a safety net).

    Returns
    -------
    {
      stem_lower: {
        "stem":     str,   # original case
        "filename": str,
        "tier":     str,
      }
    }
    """
    result: dict[str, dict] = {}
    extensions = {".wav", ".mp3"}

    for audio_path in sorted(tiered_dir.rglob("*")):
        if audio_path.suffix.lower() not in extensions:
            continue
        rel = audio_path.relative_to(tiered_dir)
        parts = rel.parts
        if len(parts) < 2:
            log.warning("Audio file not inside a tier sub-dir, skipping: %s", audio_path)
            continue

        tier      = parts[0]
        stem_orig = audio_path.stem
        stem_lo   = stem_orig.lower()

        if stem_lo in result:
            log.warning(
                "Duplicate stem '%s' across tiers ('%s' and '%s') — "
                "keeping the first occurrence.",
                stem_orig, result[stem_lo]["tier"], tier,
            )
            continue

        result[stem_lo] = {
            "stem":     stem_orig,
            "filename": audio_path.name,
            "tier":     tier,
        }

    tiers_found = sorted({v["tier"] for v in result.values()})
    log.info("  Audio files found: %d  (tiers: %s)", len(result), tiers_found)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Region resolver
# ──────────────────────────────────────────────────────────────────────────────

def resolve_region(stem_lo: str, annotator_data: dict[str, dict[str, dict]]) -> str | None:
    """
    Derive the canonical region for a file by majority vote across annotators.
    Falls back to any single non-null value, then to None.
    """
    regions = [
        ann_map[stem_lo]["region"]
        for ann_map in annotator_data.values()
        if stem_lo in ann_map and ann_map[stem_lo].get("region")
    ]
    if not regions:
        return None
    counts = Counter(regions)
    winner, _ = counts.most_common(1)[0]
    if len(counts) > 1:
        log.warning(
            "  Region disagreement for '%s': %s — using '%s'.",
            stem_lo, dict(counts), winner,
        )
    return winner


# ──────────────────────────────────────────────────────────────────────────────
# Main aggregation
# ──────────────────────────────────────────────────────────────────────────────

def aggregate(audio_dir: Path, output_path: Path) -> None:
    tiered_dir      = audio_dir / "audio_files_humans_tiered_splits"
    annotations_dir = audio_dir / "audio_files_humanannotations"

    for d, label in [(tiered_dir, "Tiered audio dir"), (annotations_dir, "Annotations dir")]:
        if not d.exists():
            log.error("%s not found: %s", label, d)
            sys.exit(1)

    log.info("── Reading annotator CSVs ──")
    annotator_data = read_annotator_csvs(annotations_dir)
    if not annotator_data:
        log.error("No annotator CSV data loaded — cannot continue.")
        sys.exit(1)

    annotators = sorted(annotator_data.keys())
    log.info("  Annotators: %s", annotators)

    log.info("── Discovering tiered audio files ──")
    tiered_audio = discover_tiered_audio(tiered_dir)

    # ── Check for CSV stems with no matching audio file ────────────────────────
    log.info("── Cross-checking CSVs against audio files ──")
    all_csv_stems: set[str] = set()
    for ann_map in annotator_data.values():
        all_csv_stems.update(ann_map.keys())

    orphan_stems = all_csv_stems - set(tiered_audio.keys())
    if orphan_stems:
        log.warning(
            "  %d CSV stem(s) have no matching audio file in tiered splits:",
            len(orphan_stems),
        )
        for s in sorted(orphan_stems):
            log.warning("      %s", s)
    else:
        log.info("  All CSV stems matched to a tiered audio file. ✓")

    # ── Assemble output ────────────────────────────────────────────────────────
    log.info("── Assembling JSON ──")

    # Sort: by tier (Gold→Silver→Bronze), then by stem alphabetically
    tier_rank = {t: i for i, t in enumerate(TIER_ORDER)}

    def sort_key(item: tuple[str, dict]) -> tuple[int, str]:
        stem_lo, info = item
        return (tier_rank.get(info["tier"], 99), stem_lo)

    output_files: list[dict] = []
    files_with_no_annotations = 0

    for stem_lo, audio_info in sorted(tiered_audio.items(), key=sort_key):
        tier     = audio_info["tier"]
        stem     = audio_info["stem"]
        filename = audio_info["filename"]
        region   = resolve_region(stem_lo, annotator_data)

        if region is None:
            log.warning("  No region found for '%s' — set to null.", stem)

        audio_path = f"audio/audio_files_humans_tiered_splits/{tier}/{filename}"

        # Per-annotator data
        annotations: dict[str, dict] = {}
        has_any = False
        for ann in annotators:
            ann_entry = annotator_data[ann].get(stem_lo)
            if ann_entry is None:
                log.debug("  [%s] No CSV entry for '%s'", ann, stem)
                annotations[ann] = {
                    "transcription":       None,
                    "speaker_accent":      None,
                    "register":            None,
                    "loan_word_languages": None,
                    "genders_present":     None,
                    "speaker_count":       None,
                }
            else:
                has_any = True
                annotations[ann] = {k: ann_entry.get(k) for _, k in ANNOTATOR_FIELD_MAP}

        if not has_any:
            log.warning("  No annotations at all for: %s", stem)
            files_with_no_annotations += 1

        output_files.append({
            "id":          stem,
            "filename":    filename,
            "tier":        tier,
            "region":      region,
            "audio_path":  audio_path,
            "annotations": annotations,
        })

    # ── Tier summary ──────────────────────────────────────────────────────────
    tier_counts: Counter = Counter(f["tier"] for f in output_files)
    for tier in TIER_ORDER:
        log.info("  %-8s  %d files", tier, tier_counts.get(tier, 0))

    if files_with_no_annotations:
        log.warning("  %d file(s) have no annotation data at all.", files_with_no_annotations)

    # ── Write JSON ────────────────────────────────────────────────────────────
    all_tiers = [t for t in TIER_ORDER if tier_counts.get(t, 0) > 0]

    output: dict = {
        "_meta": {
            "version":     "1.0.0",
            "description": "Human-annotator agreement data — Darija ASR pilot study",
            "tiers":       all_tiers,
            "annotators":  annotators,
            "total_files": len(output_files),
        },
        "files": output_files,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)

    log.info("── Done ──")
    log.info("  Output:      %s", output_path)
    log.info("  Total files: %d", len(output_files))
    log.info("  Annotators:  %s", annotators)
    log.info("  File size:   %.1f KB", output_path.stat().st_size / 1024)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--audio-dir",
        type=Path,
        default=Path("audio"),
        help="Root audio directory containing the tiered splits and annotations "
             "(default: ./audio)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data_humans.json"),
        help="Output JSON file path (default: ./data_humans.json)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    log.info("Audio dir : %s", args.audio_dir.resolve())
    log.info("Output    : %s", args.output.resolve())

    aggregate(args.audio_dir, args.output)


if __name__ == "__main__":
    main()
