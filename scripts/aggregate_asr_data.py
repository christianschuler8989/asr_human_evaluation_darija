#!/usr/bin/env python3
"""
aggregate_asr_data.py
─────────────────────
Aggregates ASR experiment data (CSV transcriptions + optional TextGrid
word-alignments) into a single structured JSON file for the pilot-study
review application.

Directory layout expected (relative to --data-dir):
  audio_files/
      <Location>/
          <filename>.wav
  audio_files_transcriptions/
      <Location>/
          <filename>__<system_name>.TextGrid   (optional, per-system)
      transcriptions-<system_name>.csv
      
Each CSV must have at minimum:
  - A "Filename" column  (the audio filename, with or without extension)
  - A "Region"   column  (the location/city, e.g. "Casablanca")
  - A transcription column (e.g. "Transcription", "Text", …)

Lookup key: (region.lower(), audio_stem.lower()) — so the same filename
in two different regions is always kept as a separate, distinct entry.

Output: data.json  (written to --output-dir, default: same as --data-dir)

Usage:
  python aggregate_asr_data.py
  python aggregate_asr_data.py --data-dir /path/to/project/data --output-dir ./web
  python aggregate_asr_data.py --help
"""

import argparse
import csv
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)



# ──────────────────────────────────────────────────────────────────────────────
# Column name detection
# ──────────────────────────────────────────────────────────────────────────────
 
# Each set is tried in order; first match wins.
FILENAME_CANDIDATES   = {"filename", "file", "audio_file", "audio", "name"}
REGION_CANDIDATES     = {"region", "location", "city", "loc", "place"}
TRANSCRIPT_CANDIDATES = {"transcription", "text", "transcript", "output",
                         "result", "asr_output", "utterance", "hypothesis"}
 
 
def _detect_col(fieldnames: list[str], candidates: set[str], label: str,
                csv_name: str) -> str | None:
    """Return the first fieldname (case-insensitive) that matches candidates."""
    for raw in fieldnames:
        if raw.strip().lower() in candidates:
            return raw
    log.warning("  [%s] Cannot find a '%s' column. Fieldnames: %s",
                csv_name, label, fieldnames)
    return None
 
 
# ──────────────────────────────────────────────────────────────────────────────
# TextGrid parser
# ──────────────────────────────────────────────────────────────────────────────
 


def parse_textgrid(path: Path) -> list[dict]:
    """
    Parse a Praat TextGrid file and return word-alignment intervals from the
    first IntervalTier named 'words' (case-insensitive), falling back to the
    first IntervalTier found.
 
    Each entry: {"word": str, "start": float, "end": float}
    Empty/silence intervals are excluded.
    Returns [] if the file cannot be parsed or contains no usable tier.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("Cannot read TextGrid %s: %s", path, exc)
        return []
 
    lines = text.splitlines()
    tiers: list[dict] = []
    current_tier: dict | None = None
    current_interval: dict | None = None
 
    for line in lines:
        line = line.strip()
 
        if line == 'class = "IntervalTier"':
            current_tier = {"name": "", "intervals": []}
            tiers.append(current_tier)
            current_interval = None
            continue
 
        if current_tier is None:
            continue
 
        m = re.match(r'^name\s*=\s*"(.*)"$', line)
        if m:
            current_tier["name"] = m.group(1)
            continue
 
        m = re.match(r'^xmin\s*=\s*([0-9.e+\-]+)$', line)
        if m:
            # xmin opens a new interval candidate; we use a sentinel dict
            # and only commit it once we have xmax + text
            current_interval = {"start": float(m.group(1))}
            continue
 
        m = re.match(r'^xmax\s*=\s*([0-9.e+\-]+)$', line)
        if m and current_interval is not None and "start" in current_interval:
            current_interval["end"] = float(m.group(1))
            continue
 
        m = re.match(r'^(?:text|label)\s*=\s*"(.*)"$', line)
        if m and current_interval is not None and "end" in current_interval:
            word = m.group(1).strip()
            if word:
                current_tier["intervals"].append({
                    "word":  word,
                    "start": current_interval["start"],
                    "end":   current_interval["end"],
                })
            current_interval = None
 
    if not tiers:
        log.debug("No IntervalTiers found in %s", path)
        return []
 
    words_tier = next(
        (t for t in tiers if t["name"].lower() == "words"),
        tiers[0],
    )
 
    if not words_tier["intervals"]:
        log.debug("Tier '%s' in %s has no non-empty intervals",
                  words_tier["name"], path)
        return []
 
    return words_tier["intervals"]
 
 
# ──────────────────────────────────────────────────────────────────────────────
# CSV reader  —  keyed on (region, stem) pairs
# ──────────────────────────────────────────────────────────────────────────────
 
def read_transcription_csvs(
    transcription_dir: Path,
) -> dict[str, dict[tuple[str, str], str]]:
    """
    Read all transcriptions-<system>.csv files.
 
    Lookup key: (region_lower, stem_lower)
      region = value in the "Region" column  (e.g. "Casablanca")
      stem   = Path(filename).stem           (e.g. "Casablanca_1-01_16k")
 
    Returns:
        {
          system_name: {
            (region_lower, stem_lower): transcription_text,
            ...
          },
          ...
        }
    """
    pattern = re.compile(r"^transcriptions[-_](.+)\.csv$", re.IGNORECASE)
    result: dict[str, dict[tuple[str, str], str]] = {}
 
    csv_files = sorted(transcription_dir.glob("transcriptions-*.csv"))
    if not csv_files:
        log.warning("No transcription CSVs found in %s", transcription_dir)
 
    for csv_path in csv_files:
        m = pattern.match(csv_path.name)
        if not m:
            continue
        system_name = m.group(1)
 
        try:
            with csv_path.open(newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                if not reader.fieldnames:
                    log.warning("Empty or header-less CSV: %s", csv_path)
                    continue
 
                fn_col = _detect_col(list(reader.fieldnames), FILENAME_CANDIDATES,
                                     "Filename", csv_path.name)
                rg_col = _detect_col(list(reader.fieldnames), REGION_CANDIDATES,
                                     "Region", csv_path.name)
                tx_col = _detect_col(list(reader.fieldnames), TRANSCRIPT_CANDIDATES,
                                     "Transcription", csv_path.name)
 
                if fn_col is None or tx_col is None:
                    log.error("  Skipping %s — cannot identify required columns.",
                              csv_path.name)
                    continue
 
                if rg_col is None:
                    log.warning(
                        "  No Region column in %s — filenames must be globally "
                        "unique across all locations for this system.",
                        csv_path.name,
                    )
 
                system_map: dict[tuple[str, str], str] = {}
                row_num = 1
                for row in reader:
                    row_num += 1
                    raw_fn = row.get(fn_col, "").strip()
                    if not raw_fn:
                        log.debug("  [%s] row %d: empty filename, skipping",
                                  csv_path.name, row_num)
                        continue
 
                    stem   = Path(raw_fn).stem.strip()
                    region = row.get(rg_col, "").strip() if rg_col else ""
                    key    = (region.lower(), stem.lower())
 
                    if key in system_map:
                        log.warning(
                            "  [%s] Duplicate key (%r, %r) at row %d — "
                            "later entry overwrites earlier.",
                            csv_path.name, region, stem, row_num,
                        )
 
                    system_map[key] = row.get(tx_col, "").strip()
 
                result[system_name] = system_map
                log.info("  CSV  %-45s  → %d entries", csv_path.name, len(system_map))
 
        except OSError as exc:
            log.warning("Cannot open %s: %s", csv_path, exc)
 
    return result
 
 
# ──────────────────────────────────────────────────────────────────────────────
# TextGrid index builder
# ──────────────────────────────────────────────────────────────────────────────
 
def build_textgrid_index(
    transcription_dir: Path,
) -> dict[tuple[str, str], dict[str, Path]]:
    """
    Scan location sub-directories under transcription_dir for TextGrid files.
 
    Filename convention: <audio_stem>__<system_name>.TextGrid
    The sub-directory name is used as the region.
 
    Returns:
        {
          (region_lower, stem_lower): {system_name: Path},
          ...
        }
    """
    index: dict[tuple[str, str], dict[str, Path]] = defaultdict(dict)
    pattern = re.compile(r"^(.+?)__(.+)\.TextGrid$", re.IGNORECASE)
 
    for tg_path in transcription_dir.rglob("*.TextGrid"):
        m = pattern.match(tg_path.name)
        if not m:
            log.debug("Skipping non-standard TextGrid name: %s", tg_path.name)
            continue
        audio_stem, system_name = m.group(1), m.group(2)
 
        # Derive region from the immediate parent directory name
        # (transcription_dir/<Region>/<stem>__<system>.TextGrid)
        try:
            region = tg_path.relative_to(transcription_dir).parts[0]
        except (ValueError, IndexError):
            region = ""
 
        key = (region.lower(), audio_stem.lower())
        index[key][system_name] = tg_path
 
    total = sum(len(v) for v in index.values())
    log.info("  TextGrids found: %d (across %d audio files)", total, len(index))
    return dict(index)
 
 
# ──────────────────────────────────────────────────────────────────────────────
# Audio file discoverer
# ──────────────────────────────────────────────────────────────────────────────
 
def discover_audio_files(audio_dir: Path) -> list[dict]:
    """
    Walk audio_dir/<Location>/*.wav and return a sorted list of dicts.
    Sort order: location alphabetically, then filename alphabetically.
    """
    files = []
    for wav_path in sorted(audio_dir.rglob("*.wav")):
        rel   = wav_path.relative_to(audio_dir)
        parts = rel.parts
        if len(parts) < 2:
            log.warning("WAV not inside a location sub-dir, skipping: %s", wav_path)
            continue
        location = parts[0]
        files.append({
            "stem":     wav_path.stem,
            "filename": wav_path.name,
            "location": location,
            "rel_path": "/".join(parts),
        })
 
    log.info("  Audio files found: %d", len(files))
    return files
 
 
# ──────────────────────────────────────────────────────────────────────────────
# Main aggregation
# ──────────────────────────────────────────────────────────────────────────────
 
def aggregate(data_dir: Path, output_path: Path) -> None:
    audio_dir         = data_dir / "audio_files"
    transcription_dir = data_dir / "audio_files_transcriptions"
 
    if not audio_dir.exists():
        log.error("Audio directory not found: %s", audio_dir)
        sys.exit(1)
    if not transcription_dir.exists():
        log.error("Transcription directory not found: %s", transcription_dir)
        sys.exit(1)
 
    log.info("── Discovering audio files ──")
    audio_files = discover_audio_files(audio_dir)
 
    log.info("── Reading transcription CSVs ──")
    csv_data = read_transcription_csvs(transcription_dir)
 
    log.info("── Indexing TextGrid files ──")
    tg_index = build_textgrid_index(transcription_dir)
 
    # All known system names
    all_systems: set[str] = set(csv_data.keys())
    for systems in tg_index.values():
        all_systems.update(systems.keys())
    log.info("  Systems detected: %s", sorted(all_systems))
 
    # ── Per-system match diagnostics ─────────────────────────────────────────
    # Track which CSV rows were actually consumed so we can report orphans.
    consumed: dict[str, set[tuple[str, str]]] = {
        sys: set() for sys in csv_data
    }
 
    # ── Build output structure ────────────────────────────────────────────────
    log.info("── Assembling JSON ──")
 
    output_files: list[dict] = []
    files_missing_all_transcriptions = 0
 
    for af in audio_files:
        stem     = af["stem"]
        location = af["location"]
        # Primary lookup key — matches CSV Region + filename stem
        key = (location.lower(), stem.lower())
 
        transcriptions: list[dict] = []
 
        for system in sorted(all_systems):
            tx_text: str | None = None
 
            # ── CSV lookup ──────────────────────────────────────────────────
            if system in csv_data:
                sys_map = csv_data[system]
 
                if key in sys_map:
                    tx_text = sys_map[key]
                    consumed[system].add(key)
                else:
                    # Fallback: try with stem variants (e.g. missing _16k suffix)
                    for alt_stem in _stem_variants(stem):
                        alt_key = (location.lower(), alt_stem.lower())
                        if alt_key in sys_map:
                            tx_text = sys_map[alt_key]
                            consumed[system].add(alt_key)
                            log.debug(
                                "  [%s / %s] matched via stem variant '%s'",
                                system, stem, alt_stem,
                            )
                            break
 
                    if tx_text is None:
                        log.debug(
                            "  [%s] No CSV entry for (%r, %r)",
                            system, location, stem,
                        )
 
            # ── TextGrid lookup ─────────────────────────────────────────────
            word_alignments: list[dict] | None = None
            tg_systems = tg_index.get(key, {})
            if system not in tg_systems:
                # Try stem variants for TextGrids too
                for alt_stem in _stem_variants(stem):
                    alt_key = (location.lower(), alt_stem.lower())
                    if system in tg_index.get(alt_key, {}):
                        tg_systems = tg_index[alt_key]
                        break
 
            if system in tg_systems:
                word_alignments = parse_textgrid(tg_systems[system])
                log.debug(
                    "  TextGrid [%s / %s] → %d words",
                    system, stem, len(word_alignments),
                )
 
            # Only add the system row if we have something to show
            if tx_text is not None or word_alignments is not None:
                entry: dict = {
                    "system": system,
                    "text":   tx_text if tx_text is not None else "",
                }
                if word_alignments is not None:
                    entry["word_alignments"] = word_alignments
                transcriptions.append(entry)
 
        if not transcriptions:
            log.warning(
                "  No transcriptions at all for: %s / %s", location, stem
            )
            files_missing_all_transcriptions += 1
 
        output_files.append({
            "id":             stem,
            "filename":       af["filename"],
            "location":       location,
            "audio_path":     f"audio/{af['rel_path']}",
            "transcriptions": transcriptions,
            "annotations":    {},
        })
 
    # ── Orphan CSV row report ─────────────────────────────────────────────────
    log.info("── Checking for unmatched CSV rows ──")
    total_orphans = 0
    for system, sys_map in csv_data.items():
        orphans = [k for k in sys_map if k not in consumed[system]]
        if orphans:
            total_orphans += len(orphans)
            log.warning(
                "  [%s] %d CSV row(s) had no matching audio file:",
                system, len(orphans),
            )
            for region, stem in sorted(orphans):
                log.warning("      region=%-15s  stem=%s", repr(region), stem)
 
    if total_orphans == 0:
        log.info("  All CSV rows matched to an audio file. ✓")
 
    # ── Summary ───────────────────────────────────────────────────────────────
    if files_missing_all_transcriptions:
        log.warning(
            "  %d audio file(s) have NO transcription data at all — "
            "check the orphan report above and your CSV Region values.",
            files_missing_all_transcriptions,
        )
 
    # ── Write JSON ────────────────────────────────────────────────────────────
    output: dict = {
        "_meta": {
            "version":     "1.0.0",
            "description": "ASR pilot-study data — aggregated for review application",
            "locations":   sorted({f["location"] for f in output_files}),
            "systems":     sorted(all_systems),
            "total_files": len(output_files),
        },
        "files": output_files,
    }
 
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)
 
    log.info("── Done ──")
    log.info("  Output:      %s", output_path)
    log.info("  Audio files: %d", len(output_files))
    log.info("  Systems:     %d  (%s)", len(all_systems), ", ".join(sorted(all_systems)))
    log.info("  File size:   %.1f KB", output_path.stat().st_size / 1024)
 
 
# ──────────────────────────────────────────────────────────────────────────────
# Stem variant helpers
# ──────────────────────────────────────────────────────────────────────────────
 
def _stem_variants(stem: str) -> list[str]:
    """
    Return plausible alternative stem spellings to try when a direct lookup
    fails. Only used as a secondary fallback — the primary key (region, stem)
    should always match for well-formed data.
    """
    variants: list[str] = []
    for suffix in ("_16k", "_8k", "_22k", "_48k"):
        if stem.lower().endswith(suffix):
            variants.append(stem[: -len(suffix)])
    return variants
 
 
# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────
 
def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Root data directory (default: ./data)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write data.json (default: same as --data-dir)",
    )
    parser.add_argument(
        "--output-name",
        default="data.json",
        help="Output filename (default: data.json)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging",
    )
    args = parser.parse_args()
 
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
 
    output_dir  = args.output_dir or args.data_dir
    output_path = output_dir / args.output_name
 
    log.info("Data directory : %s", args.data_dir.resolve())
    log.info("Output         : %s", output_path.resolve())
 
    aggregate(args.data_dir, output_path)
 
 
if __name__ == "__main__":
    main()
