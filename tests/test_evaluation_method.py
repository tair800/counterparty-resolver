"""The evaluation's own method, checked against the pipeline it claims to describe.

`test_kill_criteria.py` asserts the *thresholds* ADR-001 fixed. This file asserts that the
quantities being compared to them are the quantities they name — which is a different failure, and
the one that actually happened: published candidate recall was measured by asking whether two
records share a blocking key, while the generator skips any block over `MAX_BLOCK_SIZE`. Forty-five
adjudicated duplicates were counted as surfaced that the system never generates.

A metric that measures something adjacent to what it is named for is worse than no metric, because
it clears its floor and nobody looks again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from counterparty_resolver.blocking import MAX_BLOCK_SIZE, blocking_keys, candidate_pairs
from counterparty_resolver.domain import CandidatePair, CounterpartyRecord, Label
from counterparty_resolver.evaluate import candidate_generation_report

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def _record(name: str, **kwargs: Any) -> CounterpartyRecord:
    kwargs.setdefault("source", "test")
    kwargs.setdefault("source_id", name)
    return CounterpartyRecord(legal_name=name, **kwargs)


def _pair(left: CounterpartyRecord, right: CounterpartyRecord, label: Label) -> CandidatePair:
    return CandidatePair(pair_id=f"{left.key}|{right.key}", left=left, right=right, label=label)


@pytest.fixture(scope="module")
def evaluation() -> dict[str, Any]:
    return dict(json.loads((ARTIFACTS / "evaluation.json").read_text(encoding="utf-8")))


def test_candidate_recall_counts_only_emitted_pairs() -> None:
    """A pair inside an over-sized block shares a key and is never generated. It is not surfaced.

    Built rather than sampled. The crowd is large enough that its shared name prefix forms a block
    over `MAX_BLOCK_SIZE`, so blocking emits nothing from it; the positive pair sits inside that
    crowd. Every crowd record has to reach the report, which takes its record universe from the
    pairs it is given -- so the rest are passed as negatives, which is what they are.
    """
    # An even count, so pairing the tail two at a time leaves nobody out of the report.
    crowd = [_record(f"Consolidated Holdings {n} Limited") for n in range(MAX_BLOCK_SIZE + 6)]
    left, right = crowd[0], crowd[1]
    assert set(blocking_keys(left)) & set(blocking_keys(right)), "the two must share a key"

    pairs = [_pair(left, right, "match")]
    pairs += [_pair(a, b, "no_match") for a, b in zip(crowd[2::2], crowd[3::2], strict=False)]
    emitted = {frozenset((a.key, b.key)) for a, b, _ in candidate_pairs(crowd)}
    assert frozenset((left.key, right.key)) not in emitted, (
        "this block is over the ceiling, so blocking should emit nothing from it"
    )

    report = candidate_generation_report(pairs)

    assert report["records"] == len(crowd), "the report has to see the whole over-sized block"
    assert report["recall"] == 0.0, "a pair the generator never emits is not surfaced"
    assert report["share_a_blocking_key"] == 1
    assert report["dropped_by_block_size_ceiling"] == 1
    assert report["recall_if_block_size_were_unbounded"] == 1.0, (
        "and the looser number is still reported, so the difference is visible"
    )


def test_the_published_recall_is_the_stricter_of_the_two(evaluation: dict[str, Any]) -> None:
    """Both are reported; the headline must be the one the pipeline honours."""
    report = evaluation["candidate_generation"]

    assert report["recall"] <= report["recall_if_block_size_were_unbounded"]
    assert (
        report["surfaced"] + report["dropped_by_block_size_ceiling"]
        == (report["share_a_blocking_key"])
    )


def test_the_hold_out_is_scored_both_ways_and_the_floor_is_published(
    evaluation: dict[str, Any],
) -> None:
    """The corpus-wide frequency tables are priced rather than argued about.

    `distinctive_token_agreement` and the identifier-distinctiveness guard read tables built over
    every record in the corpus, held-out records included. No label is involved, so it is not label
    leakage — but it is information from the held-out records reaching their own scoring, and a
    repository that says "there is no fitted parameter to overfit" owes the reader the size of it.
    """
    sensitivity = evaluation["prior_sensitivity"]["arms"]

    assert set(sensitivity) == {"corpus_wide_priors", "development_only_priors"}
    shipped = sensitivity["corpus_wide_priors"]["holdout"]["precision"]
    conservative = sensitivity["development_only_priors"]["holdout"]["precision"]
    assert conservative <= shipped, (
        "if the split-local arm scores higher, the disclosure is describing the wrong direction"
    )
    assert evaluation["holdout"]["system"]["precision"] == shipped, (
        "the headline hold-out row has to be the corpus-wide arm, or the two disagree"
    )


def test_the_baselines_have_no_review_band(evaluation: dict[str, Any]) -> None:
    """ADR-001 prices abstention. A baseline that abstained would be getting it for free."""
    for side in ("development", "holdout"):
        for name, arm in evaluation[side]["baselines"].items():
            assert arm["review_rate"] == 0.0, (
                f"{side} {name} abstains, which nothing charges it for"
            )


def test_recall_charges_abstention_and_precision_does_not(evaluation: dict[str, Any]) -> None:
    """The asymmetry ADR-001 fixed, checked against the counts rather than the docstring."""
    system = evaluation["development"]["system"]
    tp, fp = system["true_positives"], system["false_positives"]
    fn, reviewed_positive = system["false_negatives"], system["review_on_positive"]

    assert system["precision"] == pytest.approx(tp / (tp + fp), abs=5e-5)
    assert system["recall"] == pytest.approx(tp / (tp + fn + reviewed_positive), abs=5e-5)
    assert reviewed_positive > 0, "with no abstention on a positive, this proves nothing"
