"""The adversarial cases, run against the resolver.

`adversarial_cases.json` holds pairs chosen because a naive matcher gets them wrong: fund vintages,
SEC series sharing one name, legal-form spellings that a token lookup never resolved, an
abbreviation that looks like a series designator. Each carries the decision the resolver must reach
and the reason.

**They are not labels.** Nothing here enters the corpus, the evaluation, or any published number.
They exist because the aggregate cannot see them: each of these shapes occurs a handful of times in
12,984 real pairs, so breaking one moves precision by 0.0002 and nothing goes red. Here it goes red
with the case's own sentence attached.

The frequency table is the real one, read from the committed corpus, because
`distinctive_token_agreement` is only meaningful against the corpus it was measured on and a test
that invents its own frequencies is testing a resolver nobody ships.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from counterparty_resolver.domain import CandidatePair, CounterpartyRecord, Decision
from counterparty_resolver.resolve import resolve_pair

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((Path(__file__).parent / "adversarial_cases.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def corpus_context() -> tuple[dict[str, int], int, dict[str, int]]:
    """Token frequency, record count and identifier frequency, from the committed corpus."""
    corpus = json.loads((ROOT / "artifacts" / "corpus.json").read_text(encoding="utf-8"))
    return (
        corpus["token_document_frequency"],
        corpus["counts"]["distinct_records"],
        corpus["identifier_document_frequency"],
    )


def _record(side: str, payload: dict[str, Any]) -> CounterpartyRecord:
    return CounterpartyRecord(source="adversarial", source_id=side, **payload)


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: str(c["id"]))
def test_the_resolver_reaches_the_required_decision(
    case: dict[str, Any], corpus_context: tuple[dict[str, int], int, dict[str, int]]
) -> None:
    frequency, total_records, identifier_frequency = corpus_context
    pair = CandidatePair(
        pair_id=case["id"],
        left=_record("left", case["left"]),
        right=_record("right", case["right"]),
    )

    result = resolve_pair(
        pair,
        frequency=frequency,
        total_records=total_records,
        identifier_frequency=identifier_frequency,
    )

    assert result.decision is Decision(case["expect"]), (
        f"{case['id']}: expected {case['expect']}, got {result.decision.value} "
        f"at score {result.score} ({result.evidence.hard_signal or 'no hard signal'}).\n"
        f"Why this case exists: {case['why']}"
    )


def test_the_case_set_covers_all_three_decisions() -> None:
    """A suite that never expects REVIEW is not testing the band that ADR-001 is about."""
    expected = {case["expect"] for case in CASES["cases"]}

    assert expected == {"match", "review", "no_match"}


def test_every_case_says_why_it_is_here() -> None:
    """A case without a reason becomes a number nobody dares change."""
    silent = [case["id"] for case in CASES["cases"] if len(case.get("why", "")) < 20]

    assert not silent, f"adversarial cases with no stated reason: {silent}"


#: Cases whose recorded decision is not the one a person would want. Named individually, so adding
#: one is an edit to this list rather than a flag somebody sets in a data file during a red build.
KNOWN_LIMITATIONS = frozenset(
    {
        "suffix-on-one-side-only",
        "abbreviated-legal-form-that-looks-like-a-designator",
        "acronym-against-its-expansion",
    }
)


def test_the_known_limitations_are_exactly_the_ones_declared() -> None:
    """A suite that can absorb a new defect by setting a flag is not a regression suite.

    The hold-out was scored at `b67b83e` and ADR-001 forbids changing a rule afterwards, so these
    three ship unfixed. That is a reason to publish them, not a reason to let a fourth join quietly.
    """
    flagged = {case["id"] for case in CASES["cases"] if case.get("known_limitation")}

    assert flagged == KNOWN_LIMITATIONS, (
        "the set of accepted defects changed; if that is intended, edit KNOWN_LIMITATIONS and say "
        "why in DECISIONS.md"
    )
