"""The types. Six of them, and each one exists because a claim in ADR-001 needs it to be durable.

The load-bearing decision here is :class:`Decision`. It has **three** members, not two. A resolver
that must answer MATCH or NO_MATCH on every pair will merge counterparties it should have left
alone, because the asymmetry is real: a duplicate costs a duplicate, and a bad merge corrupts
payment routing and takes days to unpick. REVIEW is the type saying so.

The second is that :class:`MatchEvidence` carries the *contributions* rather than a total. A score
somebody can only compare with a threshold is not evidence; a reader has to be able to see which
feature moved the decision and by how much, and disagree with it.
"""

from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CandidatePair",
    "CanonicalEntity",
    "CounterpartyRecord",
    "Decision",
    "FeatureContribution",
    "Label",
    "MatchEvidence",
    "ResolutionDecision",
    "SourceProvenance",
    "VariantType",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Decision(enum.StrEnum):
    """What the resolver concluded.

    ``REVIEW`` is not a failure mode and not a hedge. It is the decision that a human must look,
    and ADR-001 fixes how it is scored: never folded into precision or recall, always published
    beside them as a rate. Abstention that is not counted is abstention being used to hide errors.
    """

    MATCH = "match"
    REVIEW = "review"
    NO_MATCH = "no_match"


#: What a labelled pair asserts. Deliberately only two: a *label* has no REVIEW, because the
#: registrar either adjudicated these as the same entity or they are not linked. Uncertainty is the
#: resolver's to express, not the ground truth's.
Label = Literal["match", "no_match"]


class VariantType(enum.StrEnum):
    """The failure modes `PORTFOLIO_BLUEPRINT.md` claims the GLEIF duplicates exhibit.

    Kill test B asserts all three appear in a sample of 200. They are computed from the pair rather
    than asserted about it, so the claim is checkable and can come out false.
    """

    LEGAL_FORM_VARIANCE = "legal_form_variance"
    DIACRITIC_OR_CASING_DRIFT = "diacritic_or_casing_drift"
    DISTINCT_REGISTRATION_AUTHORITY = "distinct_registration_authority"
    TOKEN_ORDER_OR_ABBREVIATION = "token_order_or_abbreviation"  # noqa: S105 - a variant name
    ADDRESS_DRIFT = "address_drift"


class SourceProvenance(_Frozen):
    """Where a record or a label came from, precisely enough to fetch it again.

    ``vendored`` is here because one of this project's sources may be redistributed and one may only
    be fetched. A field that records which is the difference between a licensing claim and a hope.
    """

    name: str
    url: str
    licence: str
    revision: str
    vendored: bool


class CounterpartyRecord(_Frozen):
    """One counterparty as some source system holds it, before anything is normalised.

    The shape is deliberately the intersection of what a CRM, an ERP and a registry all have, and
    nothing more. Every field is optional except the identity of the record itself, because the
    whole problem is that real systems are missing most of them most of the time.
    """

    source: str
    source_id: str
    legal_name: str
    other_names: tuple[str, ...] = ()
    country: str | None = None
    jurisdiction: str | None = None
    legal_form_id: str | None = None
    registration_authority: str | None = None
    registered_as: str | None = None
    city: str | None = None
    postal_code: str | None = None
    address_lines: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """``source:source_id``. One string that identifies the record across the whole pipeline."""
        return f"{self.source}:{self.source_id}"


class CanonicalEntity(_Frozen):
    """A cluster of records the system believes are one counterparty.

    ``members`` is a set of record keys and ``survivor`` is the one the cluster resolves to. Nothing
    here is a merge: a merge is an act, recorded in the ledger, and reversible. This is a belief.
    """

    entity_id: str
    survivor: str
    members: frozenset[str]


class CandidatePair(_Frozen):
    """Two records that blocking thought were worth comparing, with the label if one is known.

    ``blocking_keys`` records *why* they were surfaced. Candidate recall is a published number, and
    when it is short the only useful question is which key should have fired and did not.
    """

    pair_id: str
    left: CounterpartyRecord
    right: CounterpartyRecord
    blocking_keys: tuple[str, ...] = ()
    label: Label | None = None
    label_basis: str | None = None
    source_revision: str | None = None
    variant_types: tuple[VariantType, ...] = ()


class FeatureContribution(_Frozen):
    """One feature, what it saw, and what it did to the score.

    ``detail`` is the part a person reads. "name_jaro_winkler 0.94" is a number; "PHOENIX HOLDINGS
    LIMITED vs PHOENIX HOLDINGS LTD after legal-form normalisation" is why.
    """

    feature: str
    value: float
    weight: float
    contribution: float
    detail: str


class MatchEvidence(_Frozen):
    """Everything that produced a decision, in the order it applied.

    ``hard_signal`` is separate from the weighted features on purpose. An identifier conflict is not
    "a feature with a large negative weight" — it is a different kind of statement, and burying it
    in a sum lets enough name similarity outvote a fact. ADR-001 says conflicting authoritative
    identifiers are strong negative evidence; this field is that sentence made structural.
    """

    contributions: tuple[FeatureContribution, ...]
    score: float
    hard_signal: str | None = None


class ResolutionDecision(_Frozen):
    """The answer, the evidence for it, and the band it fell in."""

    pair_id: str
    decision: Decision
    score: float
    evidence: MatchEvidence
    threshold_match: float = Field(description="At or above this, MATCH.")
    threshold_no_match: float = Field(description="Below this, NO_MATCH. Between the two, REVIEW.")
