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
from counterparty_resolver.normalize import (
    normalize_identifier,
    normalize_name,
    strip_legal_form,
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


def _tables(pairs: list[CandidatePair]) -> tuple[dict[str, int], dict[str, int], int]:
    """Token and identifier document frequency over exactly these pairs' records."""
    records = {r.key: r for p in pairs for r in (p.left, p.right)}
    tokens: dict[str, int] = {}
    identifiers: dict[str, int] = {}
    for record in records.values():
        for token in set(strip_legal_form(normalize_name(record.legal_name)).split()):
            tokens[token] = tokens.get(token, 0) + 1
        number = normalize_identifier(record.registered_as)
        authority = (record.registration_authority or "").strip().upper()
        if number and authority:
            key = f"{authority}|{number}"
            identifiers[key] = identifiers.get(key, 0) + 1
    return tokens, identifiers, len(records)


def _prior_sensitivity(
    development: list[CandidatePair], holdout: list[CandidatePair]
) -> dict[str, Any]:
    """What the corpus-wide frequency tables are worth, measured rather than argued about.

    `distinctive_token_agreement` and the identifier-distinctiveness guard both read tables computed
    over **every** record in the corpus, held-out records included. No label is used, so this is not
    label leakage -- but it is information from the hold-out reaching the scoring of the hold-out,
    and a project that says "there is no fitted parameter to overfit" owes the reader the size of it
    rather than the argument.

    So both are published: the shipped configuration, and the same system with tables built from
    development records only. The second is the conservative number.
    """
    dev_tokens, dev_identifiers, dev_records = _tables(development)
    arms = {}
    for name, (tokens, identifiers, records) in {
        "corpus_wide_priors": _tables(development + holdout),
        "development_only_priors": (dev_tokens, dev_identifiers, dev_records),
    }.items():
        arms[name] = {
            "development": score_system(
                development,
                frequency=tokens,
                total_records=records,
                identifier_frequency=identifiers,
            ),
            "holdout": score_system(
                holdout, frequency=tokens, total_records=records, identifier_frequency=identifiers
            ),
        }
    return {
        "note": (
            "Token and identifier document frequency are computed over every record in the corpus, "
            "both splits. No label is involved, so this is not label leakage -- but it is "
            "information from the held-out records reaching their own scoring, and the "
            "development_only_priors arm is what the numbers are without it. That arm is the "
            "conservative one and is the one the README quotes as the floor."
        ),
        "arms": arms,
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
        # Carried into the evaluation so the console can show the size of the thing being scored
        # without shipping the 20 MB corpus into a container that needs three numbers from it.
        # A verification pass against the live deployment found that a visitor could see the split
        # -- 10,532 development and 2,452 held out -- and never the total they add up to.
        "corpus": {
            **corpus["counts"],
            "sources": [
                {k: source[k] for k in ("name", "licence", "revision", "vendored")}
                for source in corpus["sources"]
            ],
        },
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
        report["prior_sensitivity"] = _prior_sensitivity(development, holdout)
        report["holdout"]["note"] = (
            "Scored once, after the split was committed. ADR-001 fixes that no rule, threshold, "
            "feature or normalisation changes on the basis of this number."
        )
        _print_arm("HELD OUT (scored once)", report["holdout"])

        print("\n  the corpus-wide frequency tables, priced:")
        for name, arm in report["prior_sensitivity"]["arms"].items():
            for side in ("development", "holdout"):
                result = arm[side]
                print(
                    f"    {name:24} {side:12} precision {result['precision']:.4f}  "
                    f"false merges {result['false_positives']}"
                )

    out = ARTIFACTS / "evaluation.json"
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
