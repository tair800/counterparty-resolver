"""Fetch GLEIF's adjudicated duplicates and write the labelled corpus.

    uv run python scripts/build_corpus.py            # full run, ~65 requests
    uv run python scripts/build_corpus.py --limit 400  # a fast partial build

Writes `artifacts/corpus.json`. Raw fetched records are cached under `data/raw/`, which `.gitignore`
excludes -- so re-running is cheap and no source data can reach git by accident.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from counterparty_resolver.corpus.acquire import fetch_duplicates, fetch_leis, source_revision
from counterparty_resolver.corpus.build import build_corpus

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
ARTIFACTS = ROOT / "artifacts"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="stop after N duplicate records")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache and re-fetch")
    parser.add_argument("--out", type=Path, default=ARTIFACTS / "corpus.json")
    args = parser.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    duplicates_path = RAW / "gleif_duplicates.json"
    successors_path = RAW / "gleif_successors.json"

    if args.refresh or not duplicates_path.is_file():
        print("fetching adjudicated duplicates from GLEIF...")
        duplicates = fetch_duplicates(limit=args.limit)
        duplicates_path.write_text(json.dumps(duplicates), encoding="utf-8")
    else:
        duplicates = json.loads(duplicates_path.read_text(encoding="utf-8"))
        if args.limit:
            duplicates = duplicates[: args.limit]
    print(f"  {len(duplicates)} records with registration.status=DUPLICATE")

    wanted: list[str] = [
        lei
        for d in duplicates
        if (lei := (d["attributes"]["entity"].get("successorEntity") or {}).get("lei"))
    ]

    if args.refresh or not successors_path.is_file():
        print(f"resolving {len(set(wanted))} successor LEIs...")
        successors = fetch_leis(wanted)
        successors_path.write_text(json.dumps(successors), encoding="utf-8")
    else:
        successors = json.loads(successors_path.read_text(encoding="utf-8"))
        missing = [lei for lei in set(wanted) if lei not in successors]
        if missing:
            print(f"  {len(missing)} successors not cached; fetching")
            successors.update(fetch_leis(missing))
            successors_path.write_text(json.dumps(successors), encoding="utf-8")
    print(f"  {len(successors)} successor records")

    revision = source_revision()
    print(f"GLEIF golden-copy revision: {revision}")

    corpus = build_corpus(duplicates, successors, source_revision=revision)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(corpus, indent=1) + "\n", encoding="utf-8")

    counts = corpus["counts"]
    print(f"\nwrote {args.out}")
    print(f"  labelled pairs : {counts['pairs']}")
    print(f"  positives      : {counts['positives']}  (registrar adjudications)")
    print(f"  negatives      : {counts['negatives']}  (blocking-surfaced, unlinked)")
    print(f"  distinct records: {counts['distinct_records']}")

    variants: dict[str, int] = {}
    for pair in corpus["pairs"]:
        if pair["label"] != "match":
            continue
        for variant in pair["variant_types"]:
            variants[variant] = variants.get(variant, 0) + 1
    print("\n  variant types among the adjudicated duplicates:")
    for name, count in sorted(variants.items(), key=lambda kv: -kv[1]):
        print(f"    {name:34} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
