"""Interpretable features, and the two hard signals that are not features at all.

**The distinction this module is built on.** A weighted sum is the right shape for *similarity*
evidence — names are alike, addresses are alike, tokens overlap. It is the wrong shape for *facts*.
Two records asserting different registration numbers at the same registration authority are not
"somewhat similar"; one of them is about a different company, and no quantity of name similarity
should be able to outvote that. So identifier agreement and identifier conflict are returned as
`hard_signal` and the scorer short-circuits on them, rather than being handed a large weight and
hoping the arithmetic holds.

Every feature returns a value in [0, 1] and a `detail` string a person can read, because ADR-001
requires evidence somebody can disagree with rather than a number they can only compare to a
cut-off.
"""

from __future__ import annotations

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

from counterparty_resolver.domain import CounterpartyRecord, FeatureContribution
from counterparty_resolver.normalize import (
    LEGAL_FORMS,
    acronym,
    fold_accents,
    normalize_identifier,
    normalize_name,
    strip_legal_form,
    tokens,
)

__all__ = ["FEATURE_WEIGHTS", "HardSignal", "extract_features", "hard_signal"]

#: Weights, fixed before the hold-out was scored and unchanged since.
#:
#: They are deliberately flat-ish. A tuned weight vector over 5 features and a few thousand pairs is
#: a model fitted to this corpus wearing the costume of a rule, and it would generalise the way
#: project 3's rules generalised -- badly, and invisibly. These are the weights a person would argue
#: for from the meaning of each feature, and the hold-out tests exactly that.
FEATURE_WEIGHTS: dict[str, float] = {
    "name_similarity": 0.34,
    "identifying_name_similarity": 0.26,
    "token_overlap": 0.14,
    "acronym_match": 0.06,
    "country_agreement": 0.10,
    "address_similarity": 0.10,
}


class HardSignal:
    """The three statements that are facts rather than similarities."""

    IDENTIFIER_AGREEMENT = "identifier_agreement"
    IDENTIFIER_CONFLICT = "identifier_conflict"
    COUNTRY_CONFLICT_WITH_WEAK_NAME = "country_conflict_with_weak_name"


def _ratio(a: str, b: str) -> float:
    return float(fuzz.token_sort_ratio(a, b)) / 100.0


def _jaro(a: str, b: str) -> float:
    return float(JaroWinkler.similarity(a, b))


def hard_signal(left: CounterpartyRecord, right: CounterpartyRecord) -> tuple[str | None, str]:
    """The fact, if there is one, and a sentence explaining it.

    Agreement requires **the same registration authority**. Two registries can issue the same number
    to different companies, so `registered_as` alone is a coincidence generator, not an identity.
    That is why blocking treats it as a candidate key and this function does not treat it as proof
    without the authority beside it.

    Conflict is the mirror and is the more valuable half: same authority, both numbers present, and
    they differ. That is one registrar saying these are two companies.
    """
    left_id = normalize_identifier(left.registered_as)
    right_id = normalize_identifier(right.registered_as)
    left_ra = (left.registration_authority or "").strip().upper() or None
    right_ra = (right.registration_authority or "").strip().upper() or None

    if left_id and right_id and left_ra and right_ra and left_ra == right_ra:
        # Zero-padding is a formatting difference, not a different company. Compared explicitly
        # here rather than folded away in normalisation, so the decision is visible.
        if left_id == right_id or left_id.lstrip("0") == right_id.lstrip("0"):
            return (
                HardSignal.IDENTIFIER_AGREEMENT,
                f"both registered at {left_ra} as {left.registered_as} / {right.registered_as}",
            )
        return (
            HardSignal.IDENTIFIER_CONFLICT,
            f"registrar {left_ra} holds these as {left.registered_as} and {right.registered_as} — "
            "one registrar, two numbers, two companies",
        )
    return (None, "")


def extract_features(
    left: CounterpartyRecord, right: CounterpartyRecord
) -> tuple[FeatureContribution, ...]:
    """Every similarity feature, with what it saw and what it contributed."""
    left_name = normalize_name(left.legal_name)
    right_name = normalize_name(right.legal_name)
    left_core = strip_legal_form(left_name)
    right_core = strip_legal_form(right_name)

    out: list[FeatureContribution] = []

    def add(feature: str, value: float, detail: str) -> None:
        weight = FEATURE_WEIGHTS[feature]
        out.append(
            FeatureContribution(
                feature=feature,
                value=round(value, 4),
                weight=weight,
                contribution=round(value * weight, 4),
                detail=detail,
            )
        )

    name_sim = _jaro(left_name, right_name)
    add(
        "name_similarity",
        name_sim,
        f"{left_name!r} vs {right_name!r} after normalisation",
    )

    core_sim = _jaro(left_core, right_core)
    # The identifying part, with legal forms removed. High here and low above means the names differ
    # only by legal form -- which is one of the adjudicated variant types, not a difference.
    add(
        "identifying_name_similarity",
        core_sim,
        f"{left_core!r} vs {right_core!r} with legal-form tokens removed",
    )

    left_tokens, right_tokens = tokens(left_name), tokens(right_name)
    union = left_tokens | right_tokens
    overlap = len(left_tokens & right_tokens) / len(union) if union else 0.0
    shared = sorted(left_tokens & right_tokens)
    add(
        "token_overlap",
        overlap,
        f"{len(shared)} of {len(union)} tokens shared: {', '.join(shared[:6]) or 'none'}",
    )

    left_acr, right_acr = acronym(left_name), acronym(right_name)
    # An acronym against a full name: `ibm` vs `international business machines`. Only meaningful
    # when one side is genuinely short, or every pair of two-word names scores.
    acr = 0.0
    acr_detail = "neither side looks like an acronym"
    if left_acr and right_acr:
        if left_acr == right_acr:
            acr, acr_detail = 1.0, f"both reduce to {left_acr!r}"
        elif left_core.replace(" ", "") == right_acr or right_core.replace(" ", "") == left_acr:
            acr, acr_detail = 1.0, f"{left_core!r} is the expansion of {right_acr!r}"
    add("acronym_match", acr, acr_detail)

    left_country = (left.country or "").upper() or None
    right_country = (right.country or "").upper() or None
    if left_country and right_country:
        agreement = 1.0 if left_country == right_country else 0.0
        country_detail = (
            f"both {left_country}" if agreement else f"{left_country} vs {right_country}"
        )
    else:
        # Unknown is not disagreement. Scoring a missing country as a mismatch punishes records for
        # being incomplete, which is the normal state of the data this runs on.
        agreement, country_detail = 0.5, "country missing on at least one side"
    add("country_agreement", agreement, country_detail)

    left_addr = " ".join((*left.address_lines, left.city or "", left.postal_code or "")).strip()
    right_addr = " ".join((*right.address_lines, right.city or "", right.postal_code or "")).strip()
    if left_addr and right_addr:
        addr = _ratio(fold_accents(left_addr).lower(), fold_accents(right_addr).lower())
        addr_detail = f"{left_addr[:44]!r} vs {right_addr[:44]!r}"
    else:
        addr, addr_detail = 0.5, "address missing on at least one side"
    add("address_similarity", addr, addr_detail)

    return tuple(out)


def differs_only_by_legal_form(left: str, right: str) -> bool:
    """True when two raw names are identical once legal-form tokens are canonical and removed.

    Used to label the variant type kill test B asserts, and computed from the pair rather than
    asserted about it.
    """
    ln, rn = normalize_name(left), normalize_name(right)
    if ln == rn:
        return False
    return strip_legal_form(ln) == strip_legal_form(rn)


def differs_only_by_diacritics_or_case(left: str, right: str) -> bool:
    """True when two raw names differ, but not once accents are folded and case is dropped."""
    if left == right:
        return False
    return normalize_name(left, fold=True) == normalize_name(right, fold=True) and (
        normalize_name(left, fold=False) != normalize_name(right, fold=False)
    )


def legal_forms_of(name: str) -> frozenset[str]:
    """The canonical legal-form tokens present in a name."""
    canonical = set(LEGAL_FORMS.values())
    return frozenset(t for t in normalize_name(name).split() if t in canonical)
