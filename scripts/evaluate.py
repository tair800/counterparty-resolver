"""Score the development corpus. Writes artifacts/evaluation.json.

    uv run python scripts/evaluate.py                 # development only
    uv run python scripts/evaluate.py --score-holdout # spends the hold-out, once

The hold-out is behind a flag on purpose. Scoring it is a one-way door: ADR-001 fixes that no rule,
threshold, feature or normalisation changes afterwards, so it must be a deliberate act rather than
something that happens every time somebody runs the evaluator.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from counterparty_resolver.domain import CandidatePair, CounterpartyRecord
from counterparty_resolver.evaluate import (
    BASELINES,
    candidate_generation_report,
    operating_points,
    score_baseline,
    score_system,
)
from counterparty_resolver.resolve import THRESHOLD_MATCH, THRESHOLD_NO_MATCH

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


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


def _arm(
    pairs: list[CandidatePair],
    frequency: dict[str, int],
    total_records: int,
    identifier_frequency: dict[str, int],
) -> dict[str, Any]:
    return {
        "pairs": len(pairs),
        "positives": sum(1 for p in pairs if p.label == "match"),
        "negatives": sum(1 for p in pairs if p.label == "no_match"),
        "baselines": {name: score_baseline(name, pairs) for name in BASELINES},
        "system": score_system(
            pairs,
            frequency=frequency,
            total_records=total_records,
            identifier_frequency=identifier_frequency,
        ),
    }


def _print_arm(title: str, arm: dict[str, Any]) -> None:
    print(f"\n{title}  ({arm['pairs']} pairs, {arm['positives']}+ / {arm['negatives']}-)")
    print(
        f"  {'arm':<26} {'precision':>9} {'recall':>8} {'F1':>8} {'review':>8}  {'FP':>5} {'FN':>5}"
    )
    for name, result in arm["baselines"].items():
        print(
            f"  {name:<26} {result['precision']:>9.4f} {result['recall']:>8.4f} "
            f"{result['f1']:>8.4f} {result['review_rate']:>8.1%}  "
            f"{result['false_positives']:>5} {result['false_negatives']:>5}"
        )
    s = arm["system"]
    print(
        f"  {'SYSTEM':<26} {s['precision']:>9.4f} {s['recall']:>8.4f} "
        f"{s['f1']:>8.4f} {s['review_rate']:>8.1%}  "
        f"{s['false_positives']:>5} {s['false_negatives']:>5}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score-holdout", action="store_true")
    args = parser.parse_args()

    corpus = json.loads((ARTIFACTS / "corpus.json").read_text(encoding="utf-8"))
    split = json.loads((ARTIFACTS / "holdout.json").read_text(encoding="utf-8"))

    by_id = {p["pair_id"]: p for p in corpus["pairs"]}
    frequency: dict[str, int] = corpus.get("token_document_frequency", {})
    total_records = corpus["counts"]["distinct_records"]
    identifier_frequency: dict[str, int] = corpus.get("identifier_document_frequency", {})
    development = [_pair(by_id[i]) for i in split["development_pair_ids"]]

    report: dict[str, Any] = {
        "generated_at": dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds"),
        "corpus_revision": corpus["sources"][0]["revision"],
        "thresholds": {"match": THRESHOLD_MATCH, "no_match": THRESHOLD_NO_MATCH},
        "candidate_generation": candidate_generation_report(development),
        "development": _arm(development, frequency, total_records, identifier_frequency),
        "operating_points": operating_points(
            development,
            frequency=frequency,
            total_records=total_records,
            identifier_frequency=identifier_frequency,
        ),
    }

    cg = report["candidate_generation"]
    print(f"candidate generation over {cg['records']} records")
    print(f"  recall            : {cg['recall']:.4f}  ({cg['surfaced']}/{cg['true_pairs']})")
    print(
        f"  candidates        : {cg['candidates_generated']:,}"
        f" of {cg['all_possible_pairs']:,} possible"
    )
    print(f"  reduction ratio   : {cg['reduction_ratio']:.6f}")
    print(f"  recovered by      : {cg['recovered_by_key_family']}")
    _print_arm("DEVELOPMENT", report["development"])

    print("\n  MATCH cut-offs considered (development):")
    print(f"    {'threshold':>10} {'precision':>10} {'recall':>8} {'F1':>8} {'FP':>5}")
    for point in report["operating_points"]["match_threshold_sweep"]:
        label = "never" if point["threshold_match"] is None else f"{point['threshold_match']:.2f}"
        print(
            f"    {label:>10} {point['precision']:>10.4f} {point['recall']:>8.4f} "
            f"{point['f1']:>8.4f} {point['false_positives']:>5}"
        )

    if args.score_holdout:
        holdout = [_pair(by_id[i]) for i in split["holdout_pair_ids"]]
        report["holdout"] = _arm(holdout, frequency, total_records, identifier_frequency)
        report["holdout"]["note"] = (
            "Scored once, after the split was committed. ADR-001 fixes that no rule, threshold, "
            "feature or normalisation changes on the basis of this number."
        )
        _print_arm("HELD OUT (scored once)", report["holdout"])

    out = ARTIFACTS / "evaluation.json"
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
