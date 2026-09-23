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

from collections.abc import Iterable

from counterparty_resolver.domain import (
    CandidatePair,
    CounterpartyRecord,
    Decision,
    MatchEvidence,
    ResolutionDecision,
)
from counterparty_resolver.features import HardSignal, extract_features, hard_signal

__all__ = ["THRESHOLD_MATCH", "THRESHOLD_NO_MATCH", "resolve", "resolve_pair", "score_pair"]

#: At or above this, MATCH. Between the two, REVIEW. Below the lower, NO_MATCH.
#:
#: Chosen on the development corpus and **frozen before the hold-out was built**, which git shows.
#: Two numbers rather than one because a single cut-off forces a decision on every pair, and the
#: pairs nearest a single cut-off are exactly the ones a person should see.
THRESHOLD_MATCH = 0.86
THRESHOLD_NO_MATCH = 0.62


def score_pair(left: CounterpartyRecord, right: CounterpartyRecord) -> MatchEvidence:
    """Every contribution, the weighted total, and the hard signal if there is one."""
    contributions = extract_features(left, right)
    signal, detail = hard_signal(left, right)
    score = round(sum(c.contribution for c in contributions), 4)
    return MatchEvidence(
        contributions=contributions,
        score=score,
        hard_signal=f"{signal}: {detail}" if signal else None,
    )


def _band(score: float, threshold_match: float, threshold_no_match: float) -> Decision:
    """Which band a score falls in, when no hard signal has already answered.

    The middle is REVIEW, and its width is `threshold_match - threshold_no_match`. That width
    is a published number: it is how much of the corpus the system declines to decide, and
    ADR-001 fixes that it is never folded into precision or recall.
    """
    if score >= threshold_match:
        return Decision.MATCH
    if score < threshold_no_match:
        return Decision.NO_MATCH
    return Decision.REVIEW


def resolve_pair(
    pair: CandidatePair,
    *,
    threshold_match: float = THRESHOLD_MATCH,
    threshold_no_match: float = THRESHOLD_NO_MATCH,
) -> ResolutionDecision:
    """Decide one pair.

    The order is the whole design: hard signal first, weighted score only if there is no fact to
    read. Reversing it would let a sum overrule a registrar.
    """
    evidence = score_pair(pair.left, pair.right)
    signal, _ = hard_signal(pair.left, pair.right)

    # A fact decides, or a score does -- never both, and the fact goes first. Written as two
    # statements rather than one condition because that split is the design: a registrar's
    # answer is not a high similarity score that happens to clear a threshold.
    if signal == HardSignal.IDENTIFIER_CONFLICT:
        decision = Decision.NO_MATCH
    elif signal == HardSignal.IDENTIFIER_AGREEMENT:
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
    threshold_match: float = THRESHOLD_MATCH,
    threshold_no_match: float = THRESHOLD_NO_MATCH,
) -> list[ResolutionDecision]:
    """Decide many pairs. No state carries between them, on purpose.

    A resolver whose answer for one pair depends on which pairs it saw earlier cannot be evaluated
    pair-by-pair, and cannot be explained to the person who has to approve the merge.
    """
    return [
        resolve_pair(p, threshold_match=threshold_match, threshold_no_match=threshold_no_match)
        for p in pairs
    ]
