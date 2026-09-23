"""Build the steward console's dataset from the committed corpus.

    uv run python scripts/build_demo.py

Writes `artifacts/demo.json`: a few hundred real pairs with their decision and full evidence, so
the console starts instantly and shows real records rather than fixtures. The alternative -- loading
the 19 MB corpus at boot -- costs several seconds on a free-tier container for no benefit, and
inventing demo data would put names on screen that no registrar ever adjudicated.

**Development pairs only.** The hold-out was scored once at `b67b83e` and nothing may look at it
again, including a console. Drawn from `development_pair_ids`, and
`tests/test_api.py::test_the_console_never_sees_a_held_out_pair` asserts it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from counterparty_resolver.domain import CandidatePair, CounterpartyRecord, Decision
from counterparty_resolver.resolve import resolve_pair

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"

#: How many of each decision to carry. The queue is what a steward works, so it gets the most; the
#: other two are there because a console that only shows its uncertain cases hides its behaviour.
QUOTA = {Decision.REVIEW: 150, Decision.MATCH: 40, Decision.NO_MATCH: 40}


def _pair(raw: dict[str, Any]) -> CandidatePair:
    return CandidatePair(
        pair_id=raw["pair_id"],
        left=CounterpartyRecord(**raw["left"]),
        right=CounterpartyRecord(**raw["right"]),
        blocking_keys=tuple(raw.get("blocking_keys", ())),
        label=raw["label"],
        label_basis=raw.get("label_basis"),
        source_revision=raw.get("source_revision"),
        variant_types=tuple(raw.get("variant_types", ())),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ARTIFACTS / "demo.json")
    args = parser.parse_args()

    corpus = json.loads((ARTIFACTS / "corpus.json").read_text(encoding="utf-8"))
    split = json.loads((ARTIFACTS / "holdout.json").read_text(encoding="utf-8"))
    by_id = {p["pair_id"]: p for p in corpus["pairs"]}
    frequency: dict[str, int] = corpus["token_document_frequency"]
    identifier_frequency: dict[str, int] = corpus["identifier_document_frequency"]
    total_records = corpus["counts"]["distinct_records"]

    taken: dict[Decision, list[dict[str, Any]]] = {band: [] for band in QUOTA}
    for pair_id in split["development_pair_ids"]:
        if all(len(rows) >= QUOTA[band] for band, rows in taken.items()):
            break
        pair = _pair(by_id[pair_id])
        result = resolve_pair(
            pair,
            frequency=frequency,
            total_records=total_records,
            identifier_frequency=identifier_frequency,
        )
        bucket = taken[result.decision]
        if len(bucket) >= QUOTA[result.decision]:
            continue
        bucket.append(
            {
                "pair_id": pair.pair_id,
                "decision": result.decision.value,
                "score": result.score,
                "hard_signal": result.evidence.hard_signal,
                "contributions": [c.model_dump() for c in result.evidence.contributions],
                "blocking_keys": list(pair.blocking_keys),
                "variant_types": [v.value for v in pair.variant_types],
                # The adjudication is carried so the console can show whether a steward's approval
                # agreed with the registrar. It is evidence about the console, not an input to it:
                # nothing in `api/` reads this field when deciding anything.
                "adjudicated": pair.label,
                "left": pair.left.model_dump(),
                "right": pair.right.model_dump(),
            }
        )

    pairs = [
        row for band in (Decision.REVIEW, Decision.MATCH, Decision.NO_MATCH) for row in taken[band]
    ]
    pairs.sort(key=lambda row: (-float(row["score"]), str(row["pair_id"])))

    payload = {
        "generated_at": dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds"),
        "source": "artifacts/corpus.json, development split only",
        "corpus_revision": corpus["sources"][0]["revision"],
        "counts": {band.value: len(rows) for band, rows in taken.items()},
        "pairs": pairs,
    }
    args.out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")

    print(f"wrote {args.out}")
    for band, rows in taken.items():
        print(f"  {band.value:10} {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
