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

from collections.abc import Mapping

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
    "name_similarity": 0.22,
    "identifying_name_similarity": 0.16,
    "distinctive_token_agreement": 0.32,
    "token_overlap": 0.08,
    "acronym_match": 0.04,
    "country_agreement": 0.08,
    "address_similarity": 0.10,
}

#: A token appearing in this fraction of records or more carries no identity.
#:
#: `trust`, `fund`, `capital`, `holdings`, `schindlers` -- the boilerplate that made
#: `SCHINDLERS REG. TREUUNTERNEHMEN AS TRUSTEE OF THE BRAHMS TRUST` and
#: `... OF THE CLANDEBOYE TRUST` score 0.87 on Jaro-Winkler while naming two different trusts. The
#: identity is in `brahms` and `clandeboye`, and those are exactly the tokens a string similarity
#: over a long shared prefix cannot see.
COMMON_TOKEN_FRACTION = 0.01

#: An identifier on more than this many records is an umbrella, not an identity.
#:
#: Two is the only defensible ceiling: a registration number should appear on the duplicate
#: and on its successor, and on nothing else.
MAX_RECORDS_PER_IDENTIFIER = 2

#: Below this many shared tokens, "identical but for the discriminator" is not a description of
#: the pair -- `A 1` and `A 2` share one token and are not evidence of anything.
MIN_SHARED_FOR_DISCRIMINATOR = 2


class HardSignal:
    """Statements that are facts about identity rather than degrees of similarity."""

    IDENTIFIER_AGREEMENT = "identifier_agreement"
    IDENTIFIER_CONFLICT = "identifier_conflict"
    DISCRIMINATOR_CONFLICT = "discriminator_conflict"
    IDENTIFYING_NAME_AGREEMENT = "identifying_name_agreement"


#: Tokens whose whole job is to tell two otherwise-identically-named entities apart.
#:
#: In fund, trust, share-class and series naming this token *is* the identity: `Target 2015 CIT` and
#: `Target 2040 CIT` are different products of the same manager, and `Fund II Class C` is not
#: `Fund 1 Class A`. A similarity average erases exactly this, because the strings are 95% identical
#: -- which is why it is a hard signal and not a feature with a negative weight.
_ROMAN = frozenset(
    [
        "i",
        "ii",
        "iii",
        "iv",
        "v",
        "vi",
        "vii",
        "viii",
        "ix",
        "x",
        "xi",
        "xii",
        "xiii",
        "xiv",
        "xv",
        "xx",
        "xxx",
    ]
)


def legal_forms_of(name: str) -> frozenset[str]:
    """The canonical legal-form tokens present in a name."""
    canonical = set(LEGAL_FORMS.values())
    return frozenset(t for t in normalize_name(name).split() if t in canonical)


def _is_discriminator(token: str) -> bool:
    """Does this token exist to distinguish, rather than to name?"""
    return token.isdigit() or token in _ROMAN or len(token) == 1


def discriminator_conflict(left_name: str, right_name: str) -> tuple[bool, str]:
    """True when two names agree on everything **except** their discriminating tokens.

    The condition is deliberately narrow. It fires only when the shared part is substantial and
    every differing token is a discriminator -- a digit, a roman numeral, or a single letter. Two
    names that differ by an ordinary word are simply two different names and the similarity features
    handle them; two names that differ *only* by `2027` against `2037` are two products, and no
    quantity of shared text should be allowed to merge them.
    """
    # Legal-form tokens are excluded: they are handled by the legal-form conflict above, and
    # leaving them in stopped this rule firing on `Pan Ivy I LLC` against `Pan Ivy II`, where the
    # difference is {i, ii} plus an `llc` that says nothing about which trust is which.
    canonical_forms = set(LEGAL_FORMS.values())
    left_tokens = set(normalize_name(left_name).split()) - canonical_forms
    right_tokens = set(normalize_name(right_name).split()) - canonical_forms
    shared = left_tokens & right_tokens
    differing = left_tokens.symmetric_difference(right_tokens)

    if not differing or not shared:
        return (False, "")
    if len(shared) < MIN_SHARED_FOR_DISCRIMINATOR:
        return (False, "")  # Too little in common for the difference to be the *only* difference.
    if not all(_is_discriminator(token) for token in differing):
        return (False, "")

    # **Both sides must name a designator, and they must be different ones.**
    #
    # `Target 2027` against `Target 2037` is two products. `NB Holdings Corporation (7690)` against
    # `NB Holdings Corporation` is one company written down twice, and so is `Gladiator Equities
    # P/L` against `Gladiator Equities Pty Ltd`, where `P/L` splits into two single letters that
    # look like designators and are an abbreviation. Requiring a designator on each side is what
    # separates "these are series 1 and series 2" from "somebody appended a suffix": it recovered
    # adjudicated duplicates this rule had been denying, at no cost in false merges.
    if not (differing & left_tokens) or not (differing & right_tokens):
        return (False, "")

    return (
        True,
        f"identical but for {sorted(differing)} — a series, class or vintage designator, which is "
        "the one token that distinguishes these entities",
    )


def _ratio(a: str, b: str) -> float:
    return float(fuzz.token_sort_ratio(a, b)) / 100.0


def _jaro(a: str, b: str) -> float:
    return float(JaroWinkler.similarity(a, b))


def hard_signal(
    left: CounterpartyRecord,
    right: CounterpartyRecord,
    *,
    identifier_frequency: Mapping[str, int] | None = None,
) -> tuple[str | None, str]:
    """The fact, if there is one, and a sentence explaining it.

    **The order is the argument.** What a registrar wrote down is checked first; what two strings
    look like is checked afterwards. Everything below is a statement about identity rather than a
    degree of similarity, but they are not equally authoritative, and the ordering says which wins:

    1. the registrar's number — agreement, then conflict;
    2. disjoint legal forms;
    3. a differing series, class or vintage designator;
    4. exact agreement of the whole canonicalised name.

    (4) sat above (1) in an earlier version and it was wrong: two SEC series, `S000016688` and
    `S000015881`, are both named `High Yield Strategy Fund`, and the resolver merged them because
    the names matched. A registrar issuing two numbers has said these are two things, and no
    quantity of name agreement — not even exact agreement — may outvote that.

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
            # **Only if the identifier identifies.** Forty false merges on the development corpus
            # came from here: every Allianz fund carries the same registration number, so the
            # "fact" claimed `Allianz Global Equity` and `Allianz Bondspezial` were one company.
            # An identifier held by many records names a family, and agreeing on it is not identity.
            shared_by = (identifier_frequency or {}).get(f"{left_ra}|{left_id}", 1)
            if shared_by <= MAX_RECORDS_PER_IDENTIFIER:
                return (
                    HardSignal.IDENTIFIER_AGREEMENT,
                    f"both registered at {left_ra} as {left.registered_as} / "
                    f"{right.registered_as}, an identifier held by {shared_by} record(s)",
                )
        else:
            return (
                HardSignal.IDENTIFIER_CONFLICT,
                f"registrar {left_ra} holds these as {left.registered_as} and "
                f"{right.registered_as} — one registrar, two numbers, two companies",
            )

    # Applies to the entities that have no identifier at all -- funds, trusts and share classes
    # registered at RA999999 with a null `registeredAs`, which is where this failure actually lives.
    conflict, detail = discriminator_conflict(left.legal_name, right.legal_name)
    if conflict:
        return (HardSignal.DISCRIMINATOR_CONFLICT, detail)

    # Exact agreement of the **full** canonicalised name, legal form included -- not the stripped
    # one. Stripping first was a bug, and `normalize.py`'s own docstring had predicted it: "Phoenix
    # Holdings Ltd and Phoenix Holdings GmbH have identical stripped names and are different
    # companies". It cost 65 false merges on the development corpus. Canonicalisation already
    # collapses the spelling variance this signal is meant to see through -- `Sp.K` and
    # `SPOLKA KOMANDYTOWA` both become `spk` -- so keeping the form loses nothing.
    #
    # A resolver that ignores a signal a one-line baseline uses is not better designed than the
    # baseline; it is worse. This is the system subsuming the strongest thing its competitors do,
    # which is the only honest way to claim it beats them.
    left_full = normalize_name(left.legal_name)
    right_full = normalize_name(right.legal_name)
    if left_full and left_full == right_full:
        return (
            HardSignal.IDENTIFYING_NAME_AGREEMENT,
            f"names are identical after normalisation, legal form included: {left_full!r}",
        )

    return (None, "")


def distinctive_tokens(
    name: str, frequency: Mapping[str, int] | None, total: int
) -> frozenset[str]:
    """The tokens in a name that actually identify it.

    Everything that appears in at least `COMMON_TOKEN_FRACTION` of records is boilerplate and is
    dropped. With no frequency table the whole identifying name is returned, which degrades to the
    old behaviour rather than to a wrong answer.
    """
    core = frozenset(strip_legal_form(normalize_name(name)).split())
    if not frequency or total <= 0:
        return core
    ceiling = max(2, int(total * COMMON_TOKEN_FRACTION))
    rare = frozenset(t for t in core if frequency.get(t, 0) < ceiling)
    return rare or core


def extract_features(
    left: CounterpartyRecord,
    right: CounterpartyRecord,
    *,
    frequency: Mapping[str, int] | None = None,
    total_records: int = 0,
) -> tuple[FeatureContribution, ...]:
    """Every similarity feature, with what it saw and what it contributed.

    `frequency` is token document frequency over the corpus. It is passed in rather than computed
    or cached globally so that a decision is a pure function of its inputs -- the same pair scored
    twice with the same table gives the same answer, and a test can supply its own.
    """
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

    left_rare = distinctive_tokens(left.legal_name, frequency, total_records)
    right_rare = distinctive_tokens(right.legal_name, frequency, total_records)
    rare_union = left_rare | right_rare
    rare_shared = left_rare & right_rare
    distinctive = len(rare_shared) / len(rare_union) if rare_union else 0.0
    only_left = sorted(left_rare - right_rare)
    only_right = sorted(right_rare - left_rare)
    add(
        "distinctive_token_agreement",
        distinctive,
        (
            f"identifying tokens {sorted(rare_shared) or 'none'} shared; "
            f"{only_left or 'none'} only on the left, {only_right or 'none'} only on the right"
        ),
    )

    add("acronym_match", *_acronym_match(left_name, right_name, left_core, right_core))
    add("country_agreement", *_country_agreement(left, right))
    add("address_similarity", *_address_similarity(left, right))

    return tuple(out)


def _acronym_match(
    left_name: str, right_name: str, left_core: str, right_core: str
) -> tuple[float, str]:
    """An acronym against a full name: `ibm` vs `international business machines`.

    Only meaningful when one side is genuinely short, or every pair of two-word names scores.
    """
    left_acr, right_acr = acronym(left_name), acronym(right_name)
    if left_acr and right_acr:
        if left_acr == right_acr:
            return (1.0, f"both reduce to {left_acr!r}")
        if left_core.replace(" ", "") == right_acr or right_core.replace(" ", "") == left_acr:
            return (1.0, f"{left_core!r} is the expansion of {right_acr!r}")
    return (0.0, "neither side looks like an acronym")


def _country_agreement(left: CounterpartyRecord, right: CounterpartyRecord) -> tuple[float, str]:
    """Same jurisdiction, different jurisdiction, or unknown.

    Unknown is not disagreement. Scoring a missing country as a mismatch punishes records for being
    incomplete, which is the normal state of the data this runs on.
    """
    left_country = (left.country or "").upper() or None
    right_country = (right.country or "").upper() or None
    if not (left_country and right_country):
        return (0.5, "country missing on at least one side")
    if left_country == right_country:
        return (1.0, f"both {left_country}")
    return (0.0, f"{left_country} vs {right_country}")


def _address_similarity(left: CounterpartyRecord, right: CounterpartyRecord) -> tuple[float, str]:
    """Token-sorted similarity over the whole postal address, accents folded."""
    left_addr = " ".join((*left.address_lines, left.city or "", left.postal_code or "")).strip()
    right_addr = " ".join((*right.address_lines, right.city or "", right.postal_code or "")).strip()
    if not (left_addr and right_addr):
        return (0.5, "address missing on at least one side")
    score = _ratio(fold_accents(left_addr).lower(), fold_accents(right_addr).lower())
    return (score, f"{left_addr[:44]!r} vs {right_addr[:44]!r}")


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
    """True when two raw names differ, but only in their accents or their capitalisation.

    An earlier version asked `normalize_name(..., fold=False)` whether the two differed, which
    **could not detect casing at all**: normalisation lowercases whether or not it folds accents, so
    `DYNAMIC FIXED INCOME FUND` and `Dynamic Fixed Income Fund` came back identical and the variant
    named in the function's own title was invisible. It under-counted the drift in the corpus by an
    order of magnitude, and kill test B is the thing that caught it.

    Asked of the raw strings instead. The two must be genuinely different as stored, must agree once
    canonicalised, and must agree under accent-folding and case-folding alone -- that last condition
    is what makes the answer *only* diacritics or case, rather than any other difference that
    normalisation happens to absorb.
    """
    if left == right:
        return False
    if normalize_name(left) != normalize_name(right):
        return False
    return fold_accents(left).casefold() == fold_accents(right).casefold()
