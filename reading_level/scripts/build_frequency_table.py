"""Build the word frequency table consumed by reading_level.frequency.

Run once, offline. Not imported at runtime.

The reference corpus is SUBTLEX-US, which is not redistributed with this
project: pass its path explicitly. A `wordfreq` source is provided so the
package has a working table without a manual corpus download; it draws on
mixed corpora rather than film subtitles alone, so absolute values differ.
Whichever source is used is recorded in the artifact's _meta block.

    python -m reading_level.scripts.build_frequency_table \
        --source subtlex --corpus /path/to/SUBTLEXusfrequencyabove1.csv

    python -m reading_level.scripts.build_frequency_table --source wordfreq
"""

from __future__ import annotations

import argparse
import csv
import datetime
import gzip
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reading_level import score as config  # noqa: E402

DEFAULT_WORDFREQ_LIMIT = 60000
PROPER_NOUN_MIN_CAPITAL_RATIO = 0.70
LEMMA_INHERITANCE_LOGFREQ_GAP = 0.30


def _log_frequency(per_million: float) -> float:
    return math.log10(per_million + 1.0)


def _load_proper_noun_helpers():
    from nltk.corpus import names, wordnet

    name_set = {name.lower() for name in names.words()}
    return name_set, wordnet


def _read_subtlex(corpus_path: Path) -> dict[str, dict]:
    """Read SUBTLEX-US.

    Uses SUBTLWF (frequency per million) when present. FREQlow counts
    lowercase occurrences, which gives a direct capitalisation ratio.
    """
    entries: dict[str, dict] = {}

    with corpus_path.open(newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        reader = csv.DictReader(handle, dialect=dialect)

        total_count = 0.0
        rows = []
        for row in reader:
            word = (row.get("Word") or "").strip()
            if not word or not word.isalpha():
                continue
            try:
                count = float(row.get("FREQcount") or 0)
            except ValueError:
                continue
            if count <= 0:
                continue
            total_count += count
            rows.append((word, count, row))

        for word, count, row in rows:
            try:
                per_million = float(row.get("SUBTLWF") or 0)
            except ValueError:
                per_million = 0.0
            if per_million <= 0:
                per_million = count / total_count * 1_000_000

            try:
                lowercase_count = float(row.get("FREQlow") or 0)
            except ValueError:
                lowercase_count = count
            capital_ratio = 1.0 - (lowercase_count / count) if count else 0.0

            key = word.lower()
            existing = entries.get(key)
            logfreq = _log_frequency(per_million)
            if existing is None or logfreq > existing["logfreq"]:
                entries[key] = {
                    "logfreq": logfreq,
                    "proper": capital_ratio >= PROPER_NOUN_MIN_CAPITAL_RATIO,
                }

    return entries


def _read_wordfreq(limit: int) -> dict[str, dict]:
    """Read frequencies from the `wordfreq` package.

    wordfreq is case-insensitive, so proper nouns are identified with a
    heuristic: present in the NLTK names corpus and absent from WordNet. This
    is weaker than SUBTLEX's capitalisation ratio. The runtime scorer also
    treats any token spaCy tags PROPN as a proper noun, which covers the gap.
    """
    from wordfreq import top_n_list, word_frequency

    name_set, wordnet = _load_proper_noun_helpers()
    entries: dict[str, dict] = {}

    for word in top_n_list("en", limit):
        if not word.isalpha():
            continue
        per_million = word_frequency(word, "en") * 1_000_000
        if per_million <= 0:
            continue
        key = word.lower()
        is_proper = key in name_set and not wordnet.synsets(key)
        entries[key] = {
            "logfreq": _log_frequency(per_million),
            "proper": is_proper,
        }

    return entries


def _apply_lemma_inheritance(entries: dict[str, dict]) -> int:
    """Let inflected forms inherit their lemma's frequency.

    A reader who knows "run" can handle "running", so an inflected form should
    not be scored as rare just because that exact surface form is uncommon.
    """
    from nltk.stem import WordNetLemmatizer

    lemmatizer = WordNetLemmatizer()
    inherited = 0

    for word, entry in entries.items():
        best = entry["logfreq"]
        for pos in ("n", "v", "a", "r"):
            lemma = lemmatizer.lemmatize(word, pos=pos)
            if lemma == word:
                continue
            lemma_entry = entries.get(lemma)
            if lemma_entry is None:
                continue
            if lemma_entry["logfreq"] - best >= LEMMA_INHERITANCE_LOGFREQ_GAP:
                best = max(best, lemma_entry["logfreq"])

        if best > entry["logfreq"]:
            entry["logfreq"] = best
            inherited += 1

    return inherited


def build(source: str, corpus: Path | None, limit: int) -> dict:
    if source == "subtlex":
        if corpus is None:
            raise SystemExit("--corpus is required when --source subtlex")
        if not corpus.exists():
            raise SystemExit(f"Corpus not found: {corpus}")
        entries = _read_subtlex(corpus)
        source_name = f"SUBTLEX-US ({corpus.name})"
    else:
        entries = _read_wordfreq(limit)
        source_name = "wordfreq (en, mixed corpora)"

    if not entries:
        raise SystemExit("No usable entries were read from the corpus")

    inherited = _apply_lemma_inheritance(entries)

    values = sorted(entry["logfreq"] for entry in entries.values())

    def percentile(fraction: float) -> float:
        index = min(len(values) - 1, int(fraction * len(values)))
        return round(values[index], 4)

    payload = dict(entries)
    payload["_meta"] = {
        "source": source_name,
        "built": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "entry_count": len(entries),
        "lemma_inherited": inherited,
        "proper_count": sum(1 for e in entries.values() if e["proper"]),
        "percentiles": {
            "p05": percentile(0.05),
            "p25": percentile(0.25),
            "p50": percentile(0.50),
            "p75": percentile(0.75),
        },
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("subtlex", "wordfreq"), required=True)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help="Path to the SUBTLEX-US frequency file. Required for --source subtlex.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_WORDFREQ_LIMIT,
        help="Vocabulary size when using --source wordfreq.",
    )
    parser.add_argument("--output", type=Path, default=config.FREQUENCY_TABLE_PATH)
    args = parser.parse_args()

    payload = build(args.source, args.corpus, args.limit)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.output, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)

    meta = payload["_meta"]
    print(f"Wrote {args.output}")
    print(f"  source:     {meta['source']}")
    print(f"  entries:    {meta['entry_count']}")
    print(f"  proper:     {meta['proper_count']}")
    print(f"  inherited:  {meta['lemma_inherited']}")
    print(f"  percentiles {meta['percentiles']}")


if __name__ == "__main__":
    main()
