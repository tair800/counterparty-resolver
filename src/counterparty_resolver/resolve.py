"""The decision. Three bands, and the thresholds that separate them.

Two properties are deliberate and both are about the asymmetry in ADR-001 — a duplicate costs a
duplicate, a bad merge corrupts payment routing for days.

**Hard signals short-circuit the sum.** An identifier conflict returns NO_MATCH whatever the names
look like, and an identifier agreement returns MATCH. This is not a large weight; it is a different
kind of statement, and a weight can always be outvoted by enough of everything else. The one thing a
weighted average must never do here is let `PHOENIX HOLDINGS LIMITED` and `PHOENIX HOLDINGS LIMITED`
merge when Companies House says they are 00445790 and 00445791.

**REVIEW is a band, not a tie-break.** Everything between the two thresholds is handed to a person.
Its width is a published number, because a system that abstains on half the corpus has bought its
precision rather than earned it, and ADR-001 fixes that abstention is never folded into either
metric.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from counterparty_resolver.domain import (
    CandidatePair,
    CounterpartyRecord,
    Decision,
    MatchEvidence,
    ResolutionDecision,
)
from counterparty_resolver.features import HardSignal, extract_features, hard_signal

__all__ = ["THRESHOLD_MATCH", "THRESHOLD_NO_MATCH", "resolve", "resolve_pair", "score_pair"]

#: **`None`: there is no score at which the weighted sum asserts a match.** This is a measurement,
#: not a stance -- see ADR-002. On the development corpus a MATCH band at 0.86 decided 234 pairs,
#: 151 correctly and 83 wrongly: a precision of 0.645 inside the band. Raising the cut-off from 0.86
#: to "never" costs 0.029 recall and removes 83 of the system's 89 false merges, and ADR-001 had
#: already fixed that a wrong merge is the expensive error. A band that decides at 0.645 precision
#: is not an auto-merge rule; it is a queue.
#:
#: So a MATCH comes from a fact -- a registrar's number, or exact agreement of the whole
#: canonicalised name -- and the weighted score decides only whether the remainder is worth a
#: person's time. The full sweep this was chosen from is published in `artifacts/evaluation.json`
#: under `operating_points`, so the reader can see the alternatives and disagree with the choice.
THRESHOLD_MATCH: float | None = None

#: Below this, NO_MATCH; at or above it and with no fact to read, REVIEW.
#:
#: **Precision, recall and F1 are provably invariant to this number** -- it moves pairs between
#: REVIEW and NO_MATCH, and ADR-001 charges both identically. The sweep in `operating_points` shows
#: the three metrics unchanged across the whole range, which is what makes this knob impossible to
#: tune toward the kill test. It is chosen operationally instead: the widest review band the
#: predeclared 25% abstention cap permits, because a wider band puts more real duplicates in front
#: of a person. At 0.66 the queue is 21.2% of pairs and 36.4% of it is genuine duplicates.
THRESHOLD_NO_MATCH = 0.66


def score_pair(
    left: CounterpartyRecord,
    right: CounterpartyRecord,
    *,
    frequency: Mapping[str, int] | None = None,
    total_records: int = 0,
    identifier_frequency: Mapping[str, int] | None = None,
) -> MatchEvidence:
    """Every contribution, the weighted total, and the hard signal if there is one."""
    contributions = extract_features(left, right, frequency=frequency, total_records=total_records)
    signal, detail = hard_signal(left, right, identifier_frequency=identifier_frequency)
    score = round(sum(c.contribution for c in contributions), 4)
    return MatchEvidence(
        contributions=contributions,
        score=score,
        hard_signal=f"{signal}: {detail}" if signal else None,
    )


def _band(score: float, threshold_match: float | None, threshold_no_match: float) -> Decision:
    """Which band a score falls in, when no hard signal has already answered.

    `threshold_match=None` means the band has no upper edge: everything the score decides is either
    REVIEW or NO_MATCH, which is the shipped configuration and the reason `THRESHOLD_MATCH` is
    `None`. A number is still accepted so that the sweep behind that decision can be re-run, and so
    that an operator who accepts more bad merges than this project does can set one.
    """
    if threshold_match is not None and score >= threshold_match:
        return Decision.MATCH
    if score < threshold_no_match:
        return Decision.NO_MATCH
    return Decision.REVIEW


def resolve_pair(
    pair: CandidatePair,
    *,
    threshold_match: float | None = THRESHOLD_MATCH,
    threshold_no_match: float = THRESHOLD_NO_MATCH,
    frequency: Mapping[str, int] | None = None,
    total_records: int = 0,
    identifier_frequency: Mapping[str, int] | None = None,
) -> ResolutionDecision:
    """Decide one pair.

    The order is the whole design: hard signal first, weighted score only if there is no fact to
    read. Reversing it would let a sum overrule a registrar.
    """
    evidence = score_pair(
        pair.left,
        pair.right,
        frequency=frequency,
        total_records=total_records,
        identifier_frequency=identifier_frequency,
    )
    signal, _ = hard_signal(pair.left, pair.right, identifier_frequency=identifier_frequency)

    # A fact decides, or a score does -- never both, and the fact goes first. Written as two
    # statements rather than one condition because that split is the design: a registrar's
    # answer is not a high similarity score that happens to clear a threshold.
    if signal in (HardSignal.IDENTIFIER_CONFLICT, HardSignal.DISCRIMINATOR_CONFLICT):
        decision = Decision.NO_MATCH
    elif signal in (HardSignal.IDENTIFIER_AGREEMENT, HardSignal.IDENTIFYING_NAME_AGREEMENT):
        decision = Decision.MATCH
    else:
        decision = _band(evidence.score, threshold_match, threshold_no_match)

    return ResolutionDecision(
        pair_id=pair.pair_id,
        decision=decision,
        score=evidence.score,
        evidence=evidence,
        threshold_match=threshold_match,
        threshold_no_match=threshold_no_match,
    )


def resolve(
    pairs: Iterable[CandidatePair],
    *,
    threshold_match: float | None = THRESHOLD_MATCH,
    threshold_no_match: float = THRESHOLD_NO_MATCH,
    frequency: Mapping[str, int] | None = None,
    total_records: int = 0,
    identifier_frequency: Mapping[str, int] | None = None,
) -> list[ResolutionDecision]:
    """Decide many pairs. No state carries between them, on purpose.

    A resolver whose answer for one pair depends on which pairs it saw earlier cannot be evaluated
    pair-by-pair, and cannot be explained to the person who has to approve the merge.
    """
    return [
        resolve_pair(
            p,
            threshold_match=threshold_match,
            threshold_no_match=threshold_no_match,
            frequency=frequency,
            total_records=total_records,
            identifier_frequency=identifier_frequency,
        )
        for p in pairs
    ]
