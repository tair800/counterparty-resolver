"""Scoring. The definitions are ADR-001's, written out here so they cannot drift.

**How abstention is charged, exactly.** ADR-001 fixes that REVIEW is never folded into either
metric, and the two halves of that mean different things:

- ``precision = TP / (TP + FP)`` — over the decisions actually made. A pair sent to REVIEW is not a
  decision and does not appear.
- ``recall = TP / (every true positive there was)`` — **including the ones sent to REVIEW.**
  Abstention is charged here in full. A system that reviews every hard positive and matches only the
  easy ones has not earned its recall, and this is the denominator that says so.

That asymmetry is deliberate and it is the conservative direction: abstention is free in precision
and expensive in recall, so the published precision is never inflated by declining the hard cases
while the cost of declining them stays visible.

**The baselines have no REVIEW.** They are binary by construction, so their review rate is zero and
their recall denominator is the same set. Comparing a three-band system with a two-band baseline is
only fair if the abstention is priced, which is what the recall definition above does.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from rapidfuzz.distance import JaroWinkler

from counterparty_resolver.blocking import blocking_keys, candidate_pairs
from counterparty_resolver.domain import CandidatePair, CounterpartyRecord, Decision
from counterparty_resolver.normalize import normalize_identifier, normalize_name
from counterparty_resolver.resolve import THRESHOLD_MATCH, THRESHOLD_NO_MATCH, resolve_pair

#: The fuzzy baseline's cut-off. The number a person picks when told to pick one.
FUZZY_BASELINE_THRESHOLD = 0.90

#: How many "not surfaced" examples the candidate report keeps, for a reader to look at.
_MISSED_EXAMPLES = 25

__all__ = [
    "BASELINES",
    "MATCH_SWEEP",
    "REVIEW_SWEEP",
    "candidate_generation_report",
    "confusion",
    "operating_points",
    "score_baseline",
    "score_system",
]

#: MATCH cut-offs the shipped configuration was chosen from. `None` is "the score never matches".
MATCH_SWEEP: tuple[float | None, ...] = (0.86, 0.88, 0.90, 0.92, 0.94, 0.96, 0.98, None)

#: REVIEW cut-offs, swept to show that precision, recall and F1 do not move with this knob.
REVIEW_SWEEP: tuple[float, ...] = (0.55, 0.62, 0.66, 0.70, 0.74, 0.78, 0.82)


def confusion(outcomes: Iterable[tuple[str, Decision]]) -> dict[str, Any]:
    """Counts and the metrics derived from them, with abstention priced as ADR-001 fixes.

    Args:
        outcomes: ``(label, decision)`` per pair, where label is ``"match"`` or ``"no_match"``.
    """
    tp = fp = tn = fn = review_pos = review_neg = 0
    for label, decision in outcomes:
        if decision is Decision.REVIEW:
            if label == "match":
                review_pos += 1
            else:
                review_neg += 1
        elif decision is Decision.MATCH:
            if label == "match":
                tp += 1
            else:
                fp += 1
        elif label == "match":
            fn += 1
        else:
            tn += 1

    decided = tp + fp + tn + fn
    reviewed = review_pos + review_neg
    total = decided + reviewed
    all_positives = tp + fn + review_pos

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / all_positives if all_positives else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "review_on_positive": review_pos,
        "review_on_negative": review_neg,
        "pairs": total,
        "decided": decided,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "review_rate": round(reviewed / total, 4) if total else 0.0,
        "precision_definition": "TP / (TP + FP), over decisions actually made; REVIEW excluded",
        "recall_definition": (
            "TP / (TP + FN + REVIEW-on-positive); a pair sent to REVIEW is not a recall success"
        ),
    }


# ------------------------------------------------------------------------------- the baselines


def _baseline_exact_name(left: CounterpartyRecord, right: CounterpartyRecord) -> Decision:
    """Same normalised name, or not. The simplest thing that could work."""
    same = normalize_name(left.legal_name) == normalize_name(right.legal_name)
    return Decision.MATCH if same else Decision.NO_MATCH


def _baseline_fuzzy_name(left: CounterpartyRecord, right: CounterpartyRecord) -> Decision:
    """One similarity over normalised names, one cut-off.

    0.90 is the cut-off a person picks when told to pick one -- high enough to look safe. This is
    the arm the system has to beat, and it is not a straw man: it is what most of this problem gets
    solved with in practice.
    """
    score = JaroWinkler.similarity(
        normalize_name(left.legal_name), normalize_name(right.legal_name)
    )
    return Decision.MATCH if score >= FUZZY_BASELINE_THRESHOLD else Decision.NO_MATCH


def _baseline_identifier_first(left: CounterpartyRecord, right: CounterpartyRecord) -> Decision:
    """Same authority and same number is a match; otherwise fall back to exact name.

    The careful engineer's first answer, and the strongest of the three. Beating it is the point of
    kill test F.
    """
    left_id = normalize_identifier(left.registered_as)
    right_id = normalize_identifier(right.registered_as)
    left_ra = (left.registration_authority or "").upper() or None
    right_ra = (right.registration_authority or "").upper() or None
    if (
        left_id
        and right_id
        and left_ra
        and right_ra
        and left_ra == right_ra
        and left_id == right_id
    ):
        return Decision.MATCH
    return _baseline_exact_name(left, right)


BASELINES: dict[str, Callable[[CounterpartyRecord, CounterpartyRecord], Decision]] = {
    "exact_normalized_name": _baseline_exact_name,
    "fuzzy_name_only_0.90": _baseline_fuzzy_name,
    "identifier_first": _baseline_identifier_first,
}


def score_baseline(name: str, pairs: Iterable[CandidatePair]) -> dict[str, Any]:
    """One baseline over one set of pairs."""
    decide = BASELINES[name]
    return confusion((p.label or "no_match", decide(p.left, p.right)) for p in pairs)


def score_system(
    pairs: Iterable[CandidatePair],
    *,
    threshold_match: float | None = THRESHOLD_MATCH,
    threshold_no_match: float = THRESHOLD_NO_MATCH,
    frequency: Mapping[str, int] | None = None,
    total_records: int = 0,
    identifier_frequency: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """The system over one set of pairs."""
    return confusion(
        (
            p.label or "no_match",
            resolve_pair(
                p,
                threshold_match=threshold_match,
                threshold_no_match=threshold_no_match,
                frequency=frequency,
                total_records=total_records,
                identifier_frequency=identifier_frequency,
            ).decision,
        )
        for p in pairs
    )


def operating_points(
    pairs: list[CandidatePair],
    *,
    frequency: Mapping[str, int] | None = None,
    total_records: int = 0,
    identifier_frequency: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Every operating point the shipped thresholds were chosen from, published beside the result.

    A single reported number invites the reader to assume the rest of the curve was worse. Here it
    is, so they can see what the choice cost and disagree with it. Two facts this table is meant to
    make checkable rather than claimed:

    - the MATCH band buys very little recall and most of the false merges, which is why the shipped
      `THRESHOLD_MATCH` is `None`;
    - precision, recall and F1 are **identical down the REVIEW column**, because that cut-off only
      moves pairs between REVIEW and NO_MATCH and ADR-001 charges those the same. A knob that cannot
      change the headline numbers cannot be tuned toward the kill test.
    """
    scored = {
        "match_threshold_sweep": [
            {
                "threshold_match": tm,
                **{
                    k: v
                    for k, v in score_system(
                        pairs,
                        threshold_match=tm,
                        frequency=frequency,
                        total_records=total_records,
                        identifier_frequency=identifier_frequency,
                    ).items()
                    if k in ("precision", "recall", "f1", "review_rate", "false_positives")
                },
            }
            for tm in MATCH_SWEEP
        ],
        "review_threshold_sweep": [
            _review_point(
                pairs,
                tn,
                frequency=frequency,
                total_records=total_records,
                identifier_frequency=identifier_frequency,
            )
            for tn in REVIEW_SWEEP
        ],
        "shipped": {
            "threshold_match": THRESHOLD_MATCH,
            "threshold_no_match": THRESHOLD_NO_MATCH,
            "why": (
                "THRESHOLD_MATCH is None because the MATCH band decided at 0.645 precision on "
                "development and ADR-001 fixes that a wrong merge is the expensive error. "
                "THRESHOLD_NO_MATCH is the widest review band the predeclared 25% abstention cap "
                "permits, which maximises the duplicates a reviewer can reach; it cannot move "
                "precision, recall or F1."
            ),
        },
    }
    return scored


def _review_point(
    pairs: list[CandidatePair],
    threshold_no_match: float,
    *,
    frequency: Mapping[str, int] | None,
    total_records: int,
    identifier_frequency: Mapping[str, int] | None,
) -> dict[str, Any]:
    """One REVIEW cut-off, with the queue it produces and how much of that queue is real.

    `queue_yield` is the number an operations lead actually asks for: of the pairs this sends to a
    person, what fraction are duplicates. A queue nobody should work is not an abstention policy.
    """
    result = score_system(
        pairs,
        threshold_no_match=threshold_no_match,
        frequency=frequency,
        total_records=total_records,
        identifier_frequency=identifier_frequency,
    )
    queue = result["review_on_positive"] + result["review_on_negative"]
    return {
        "threshold_no_match": threshold_no_match,
        "precision": result["precision"],
        "recall": result["recall"],
        "f1": result["f1"],
        "review_rate": result["review_rate"],
        "queue": queue,
        "queue_yield": round(result["review_on_positive"] / queue, 4) if queue else 0.0,
    }


# ------------------------------------------------------------------- candidate generation report


def candidate_generation_report(pairs: list[CandidatePair]) -> dict[str, Any]:
    """Does blocking surface the pairs a registrar says are the same entity?

    The number that caps everything else. Measured against **the pairs `candidate_pairs` actually
    emits**, which is the question that matters: would this pair ever have reached the scorer.

    It used to ask whether the two records share a blocking key, and that is a different and more
    flattering question. `blocking.py` skips any block larger than `MAX_BLOCK_SIZE`, so 45
    adjudicated duplicates shared a key and were still never generated -- counted as surfaced by a
    measurement the pipeline does not honour. Published recall fell from 0.9223 to 0.9138 when this
    was corrected, which is the real number and still clears the predeclared 0.90 floor.

    ``reduction_ratio`` is reported beside it because recall alone is trivially maximised by
    blocking on nothing: a candidate set of every possible pair has perfect recall and is useless.
    """
    records: dict[str, CounterpartyRecord] = {}
    for pair in pairs:
        records[pair.left.key] = pair.left
        records[pair.right.key] = pair.right

    positives = [p for p in pairs if p.label == "match"]
    emitted: set[frozenset[str]] = set()
    generated = 0
    for left, right, _ in candidate_pairs(records.values()):
        emitted.add(frozenset((left.key, right.key)))
        generated += 1

    surfaced = 0
    shares_a_key = 0
    by_key_family: dict[str, int] = {}
    missed: list[dict[str, str]] = []

    for pair in positives:
        shared = set(blocking_keys(pair.left)) & set(blocking_keys(pair.right))
        shares_a_key += bool(shared)
        if frozenset((pair.left.key, pair.right.key)) in emitted:
            surfaced += 1
            for key in shared:
                family = key.split(":", 1)[0]
                by_key_family[family] = by_key_family.get(family, 0) + 1
        elif len(missed) < _MISSED_EXAMPLES:
            missed.append(
                {
                    "pair_id": pair.pair_id,
                    "left": pair.left.legal_name,
                    "right": pair.right.legal_name,
                    "why": (
                        "every shared key is in a block over MAX_BLOCK_SIZE"
                        if shared
                        else "the two records share no blocking key"
                    ),
                }
            )

    n = len(records)
    all_possible = n * (n - 1) // 2

    return {
        "records": n,
        "true_pairs": len(positives),
        "surfaced": surfaced,
        "recall": round(surfaced / len(positives), 4) if positives else 0.0,
        # Reported beside it because the difference is the block-size ceiling, and a reader
        # comparing this repository against one that publishes the looser number should be able
        # to see both.
        "share_a_blocking_key": shares_a_key,
        "recall_if_block_size_were_unbounded": (
            round(shares_a_key / len(positives), 4) if positives else 0.0
        ),
        "dropped_by_block_size_ceiling": shares_a_key - surfaced,
        "candidates_generated": generated,
        "all_possible_pairs": all_possible,
        "reduction_ratio": round(1 - generated / all_possible, 6) if all_possible else 0.0,
        "candidates_per_record": round(generated / n, 2) if n else 0.0,
        "recovered_by_key_family": dict(sorted(by_key_family.items(), key=lambda kv: -kv[1])),
        "examples_not_surfaced": missed,
        "note": (
            "Recall here caps every downstream number: a true pair blocking never surfaces "
            "cannot be recovered by any scorer. It is measured against the pairs candidate_pairs "
            "actually emits, not against whether the two records share a key -- the block-size "
            "ceiling makes those different questions. Reduction ratio is beside it because recall "
            "alone is maximised by blocking on nothing."
        ),
        "population_note": (
            "Every record here participates in an adjudicated duplicate, because the corpus is "
            "built from GLEIF's DUPLICATE records and their successors. The reduction ratio is "
            "therefore measured over a pool already filtered to duplicates and is not the ratio "
            "the same blocking would achieve over a counterparty master."
        ),
    }
