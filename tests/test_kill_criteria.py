"""The predeclared kill test. Written before the package existed.

ADR-001 fixes six conditions, A-F, any of which fails the project. This file asserts them against
the **committed artifacts** rather than against anything the resolver reports about itself: a
component that grades itself is not evidence.

It opened with an `importorskip`, because it was committed at `b620131` before
`counterparty_resolver` existed. That skip was a pre-registration device with a short life and it is
gone: `test_predeclaration.py` fails the build the moment the package is importable and any skip is
still here, and `conftest.py` refuses the whole session if anything disables these tests by a mark.
Project 4 shipped a guard that checked only the literal string `importorskip` and stayed green under
`pytest.mark.skip`; that is not repeated here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"

#: ADR-001 kill test A. The authoritative threshold from PORTFOLIO_BLUEPRINT.md.
MIN_LABELLED_PAIRS = 1_000

#: ADR-001 kill test E. Candidate generation caps every downstream number, so it gets its own floor.
MIN_CANDIDATE_RECALL = 0.90

#: ADR-001 kill test B. A sample this size must exhibit every claimed variant type.
VARIANT_SAMPLE = 200


def _load(name: str) -> Any:
    path = ARTIFACTS / name
    if not path.is_file():
        pytest.fail(f"{name} is missing; run `make corpus` and `make evaluate`")
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------ A: corpus size


def test_A_at_least_one_thousand_usable_labelled_pairs() -> None:
    """The authoritative criterion. Counted from the corpus, not from a README sentence."""
    corpus = _load("corpus.json")
    pairs = corpus["pairs"]

    assert len(pairs) >= MIN_LABELLED_PAIRS, (
        f"ADR-001 kill test A: {len(pairs)} usable labelled pairs, threshold {MIN_LABELLED_PAIRS}"
    )


def test_A_both_labels_are_present_in_quantity() -> None:
    """A corpus of one class measures nothing, whatever its size."""
    corpus = _load("corpus.json")
    positives = [p for p in corpus["pairs"] if p["label"] == "match"]
    negatives = [p for p in corpus["pairs"] if p["label"] == "no_match"]

    assert len(positives) >= 500, f"only {len(positives)} positive pairs"
    assert len(negatives) >= 500, f"only {len(negatives)} negative pairs"


# ---------------------------------------------------------------------- B: the claimed variants


def test_B_the_duplicates_exhibit_the_claimed_variant_types() -> None:
    """The blueprint claims these failure modes are *in* the GLEIF duplicates. Checked, not assumed.

    If they are absent the corpus is real but does not support the claim made about it, which is the
    second half of the authoritative kill criterion.
    """
    corpus = _load("corpus.json")
    sample = [p for p in corpus["pairs"] if p["label"] == "match"][:VARIANT_SAMPLE]
    assert len(sample) >= VARIANT_SAMPLE, f"only {len(sample)} positives to inspect"

    seen = {v for pair in sample for v in pair.get("variant_types", [])}
    required = {
        "legal_form_variance",
        "diacritic_or_casing_drift",
        "distinct_registration_authority",
    }
    missing = required - seen

    assert not missing, (
        "ADR-001 kill test B: the claimed variant types are absent from the first "
        f"{VARIANT_SAMPLE} adjudicated duplicates: {sorted(missing)}"
    )


# ------------------------------------------------------------------------------- C: provenance


def test_C_every_pair_carries_its_provenance() -> None:
    """A label whose origin is not recorded is a label nobody can check."""
    corpus = _load("corpus.json")
    required = {
        "pair_id",
        "source",
        "left_id",
        "right_id",
        "label",
        "label_basis",
        "source_revision",
    }

    incomplete = [
        p["pair_id"]
        for p in corpus["pairs"]
        if not required.issubset(p) or not all(p[k] for k in required)
    ]

    assert not incomplete, f"ADR-001 kill test C: {len(incomplete)} pairs without full provenance"


# ------------------------------------------------------------------------------ D: reproducible


def test_D_the_corpus_declares_what_it_was_built_from() -> None:
    """A rebuild must be able to produce the same labels, which needs the inputs pinned."""
    corpus = _load("corpus.json")

    for key in ("sources", "generated_at", "builder_version"):
        assert key in corpus, f"corpus.json has no `{key}`"
    for source in corpus["sources"]:
        for key in ("name", "url", "licence", "revision", "vendored"):
            assert key in source, f"source {source.get('name')} has no `{key}`"


# ---------------------------------------------------------------- E: candidate generation floor


def test_E_candidate_generation_keeps_the_true_matches() -> None:
    """What blocking drops, no amount of scoring recovers."""
    evaluation = _load("evaluation.json")
    recall = evaluation["candidate_generation"]["recall"]

    assert recall >= MIN_CANDIDATE_RECALL, (
        f"ADR-001 kill test E: candidate recall {recall:.3f}, floor {MIN_CANDIDATE_RECALL}"
    )


# ------------------------------------------------------------------ F: better than the baseline


def test_F_the_system_beats_the_best_predeclared_baseline() -> None:
    """Measured on the same pairs. Precision is the headline because the asymmetry is real."""
    evaluation = _load("evaluation.json")
    system = evaluation["development"]["system"]["precision"]
    best = max(b["precision"] for b in evaluation["development"]["baselines"].values())

    assert system > best, (
        f"ADR-001 kill test F: system precision {system:.4f} does not beat the best "
        f"baseline {best:.4f}"
    )


def test_F_the_system_is_not_beating_the_baseline_by_abstaining() -> None:
    """Precision bought entirely with REVIEW is not an improvement, it is a smaller answer.

    ADR-001 fixes that abstention is never folded into precision or recall. This is the assertion
    that keeps that honest: the system must still decide on most of the corpus.
    """
    evaluation = _load("evaluation.json")
    review_rate = evaluation["development"]["system"]["review_rate"]

    assert review_rate <= 0.25, f"the system abstains on {review_rate:.1%} of pairs"
